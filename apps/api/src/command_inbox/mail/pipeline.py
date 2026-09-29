"""What a provider message becomes. Shared by every connector.

1. Dedupe by (mailbox, provider id); a message is processed once however often it is notified.
2. Our own traffic: a test-mail round trip (x-ci-test), a copy of our own reply (x-ci-intent), or mail from
   the mailbox itself, never becomes a ticket.
3. Noise: automatic replies (Auto-Submitted, X-Auto-Response-Suppress, out-of-office) and bounces (delivery
   status reports) are recorded and skipped, so we never answer a robot or loop with one.
4. Sender trust: the receiving server's Authentication-Results (SPF, DKIM, DMARC) decides whether the
   sender is verified; an unverified sender never reaches an automatic lane. Mail claiming to come from the
   bank's own domain without passing DMARC is flagged as spoofing and goes to a person.
5. Content is untrusted: hidden characters are removed and quoted history is cut before triage; the full
   original is kept sealed with the tenant's key.
6. Threading: the provider's conversation id first, then In-Reply-To/References, then (in the intake) the
   subject.
Then the message enters the existing intake and triage, as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from command_inbox.core.clock import clock
from command_inbox.core.telemetry import ingest_lag
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import MailAttachment, Mailbox, MailMessage, MailSyncEvent
from command_inbox.mail.types import INTENT_HEADER, TEST_HEADER, RawMessage
from command_inbox.modules.intake.security import SenderAuth, parse_authentication_results
from command_inbox.platform.keys import tenant_encrypt

log = structlog.get_logger(__name__)

_HIDDEN = re.compile("[​‌‍⁠﻿­‪-‮⁦-⁩]")
_QUOTE_START = re.compile(
    r"^(?:-{2,}\s*Original Message\s*-{2,}|From:\s.+|On .+ wrote:|_{10,})\s*$", re.I | re.M
)
_AUTO_SUBJECT = re.compile(
    r"^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office|autoreply)\b", re.I
)
_BOUNCE_FROM = re.compile(r"^(mailer-daemon|postmaster|microsoftexchange[0-9a-f]*)@", re.I)


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: str
    ticket_id: str | None = None
    duplicate: bool = False


def clean_text(text: str) -> str:
    """Untrusted body → what triage reads: no hidden characters, no quoted history, bounded length."""
    text = _HIDDEN.sub("", text).replace("\r\n", "\n")
    match = _QUOTE_START.search(text)
    if match and match.start() > 0:
        text = text[: match.start()]
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()[:50_000]


def is_auto_reply(raw: RawMessage) -> bool:
    auto = (raw.header("auto-submitted") or "no").strip().lower()
    if auto != "no":
        return True
    if raw.header("x-auto-response-suppress") and (raw.header("x-autoreply") or raw.header("x-autorespond")):
        return True
    if (raw.header("precedence") or "").strip().lower() in ("auto_reply", "bulk", "junk"):
        return True
    return bool(_AUTO_SUBJECT.match(raw.subject.strip()))


def is_bounce(raw: RawMessage) -> bool:
    ctype = (raw.header("content-type") or "").lower()
    return "report-type=delivery-status" in ctype or bool(_BOUNCE_FROM.match(raw.sender.email))


def sender_auth(raw: RawMessage) -> SenderAuth:
    results = "; ".join(raw.headers.get("authentication-results", []))
    return parse_authentication_results(results or None, raw.sender.email, "header")


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


async def _thread_parent(org_id: str, mailbox_id: str, raw: RawMessage) -> str | None:
    """The provider id of an earlier message of the same conversation that became (part of) a ticket."""
    candidates = [raw.header("in-reply-to"), *(raw.header("references") or "").split()]
    async with tenant_tx(org_id) as tx:
        if raw.conversation_id:
            pid = (
                await tx.execute(
                    select(MailMessage.provider_message_id)
                    .where(
                        MailMessage.org_id == org_id,
                        MailMessage.mailbox_id == mailbox_id,
                        MailMessage.conversation_id == raw.conversation_id,
                        MailMessage.ticket_id.is_not(None),
                    )
                    .order_by(MailMessage.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if pid:
                return pid
        ids = [c.strip() for c in candidates if c and c.strip()]
        if ids:
            return (
                await tx.execute(
                    select(MailMessage.provider_message_id)
                    .where(
                        MailMessage.org_id == org_id,
                        MailMessage.internet_message_id.in_(ids),
                        MailMessage.ticket_id.is_not(None),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
    return None


async def process(org_id: str, mailbox: Mailbox, raw: RawMessage) -> Outcome:
    from command_inbox.modules.intake import service
    from command_inbox.schemas.requests import IntakeMessageBody

    async with tenant_tx(org_id) as tx:
        seen = (
            await tx.execute(
                select(MailMessage.outcome, MailMessage.ticket_id).where(
                    MailMessage.org_id == org_id,
                    MailMessage.mailbox_id == mailbox.id,
                    MailMessage.provider_message_id == raw.provider_id,
                )
            )
        ).first()
    if seen:
        return Outcome(seen[0], str(seen[1]) if seen[1] else None, duplicate=True)

    account = (mailbox.provider_account or mailbox.address).lower()
    flags: list[str] = []
    ticket_id: str | None = None
    auth = sender_auth(raw)
    if raw.header(TEST_HEADER):
        kind = "test"
    elif raw.header(INTENT_HEADER):
        kind = "skipped_loop"
    elif raw.sender.email == account:
        kind = "skipped_own"
    elif is_bounce(raw):
        kind = "skipped_bounce"
    elif is_auto_reply(raw):
        kind = "skipped_auto_reply"
    else:
        if _domain(raw.sender.email) == _domain(account) and auth.dmarc != "pass":
            flags.append("own_domain_unverified")
            auth = SenderAuth(auth.dkim, auth.spf, auth.dmarc, False, auth.source)
        parent = await _thread_parent(org_id, mailbox.id, raw)
        body = clean_text(raw.body_text) or "(no text)"
        result = await service.ingest(
            IntakeMessageBody.model_validate(
                {
                    "mailbox": mailbox.address,
                    "fromName": (raw.sender.name or raw.sender.email)[:120],
                    "fromEmail": raw.sender.email,
                    "subject": (raw.subject or "(no subject)")[:300],
                    "body": body,
                    "messageId": raw.provider_id[:300],
                    "inReplyTo": parent,
                }
            ),
            auth,
            org_id=org_id,
        )
        kind = "ticket" if result.created else "thread"
        ticket_id = result.ticket_id or None
        if raw.attachments:
            flags.append("attachments_not_scanned")

    now = clock.now()
    async with tenant_tx(org_id) as tx:
        sealed = (
            await tenant_encrypt(
                tx, org_id, raw.mime.decode("utf-8", "replace"), aad=f"mime|{raw.provider_id}"
            )
            if raw.mime
            else None
        )
        inserted = (
            await tx.execute(
                insert(MailMessage)
                .values(
                    org_id=org_id,
                    mailbox_id=mailbox.id,
                    provider_message_id=raw.provider_id,
                    internet_message_id=raw.internet_message_id,
                    conversation_id=raw.conversation_id,
                    in_reply_to=raw.header("in-reply-to"),
                    references_=raw.header("references") or "",
                    from_addr=raw.sender.email,
                    from_name=raw.sender.name,
                    to_addrs=[a.email for a in raw.to],
                    cc_addrs=[a.email for a in raw.cc],
                    subject=raw.subject[:1000],
                    received_at=raw.received_at,
                    auth=auth.as_dict(),
                    raw_sealed=sealed,
                    raw_size=len(raw.mime or b""),
                    body_text=clean_text(raw.body_text)[:20_000],
                    flags=flags,
                    outcome=kind,
                    ticket_id=ticket_id,
                )
                .on_conflict_do_nothing()
                .returning(MailMessage.id)
            )
        ).scalar_one_or_none()
        if inserted is None:
            return Outcome(kind, ticket_id, duplicate=True)
        for a in raw.attachments:
            tx.add(
                MailAttachment(
                    org_id=org_id,
                    message_id=inserted,
                    provider_attachment_id=a.id,
                    filename=a.name[:300],
                    content_type=a.content_type[:200],
                    size=a.size,
                    is_inline=a.is_inline,
                )
            )
        values: dict[str, object] = {"last_message_at": now}
        if raw.received_at:
            values["lag_seconds"] = max(0, int((now - raw.received_at).total_seconds()))
            ingest_lag.labels(mailbox.provider).observe(values["lag_seconds"])
        if kind == "test":
            values["last_test_ok_at"] = now
        await tx.execute(
            update(Mailbox).where(Mailbox.org_id == org_id, Mailbox.id == mailbox.id).values(**values)
        )
        tx.add(
            MailSyncEvent(
                org_id=org_id,
                mailbox_id=mailbox.id,
                kind="message",
                detail={"outcome": kind, "flags": flags, "ticketId": ticket_id},
            )
        )
    return Outcome(kind, ticket_id)
