"""Mailbox jobs: connect, catch up, renew the stream, send approved replies, and the test-mail round trip.

- `mail_connect`  after the OAuth grant: set the sync baseline (only mail from now on becomes tickets), start
                  the change-notification stream when a public webhook URL is configured, go live.
- `mail_sync`     catch up from the stored cursor, fetch and process each new message. Runs on every
                  notification and on a sweep (every 10 minutes with notifications, every minute without),
                  so a dropped notification costs minutes, never mail. An expired cursor resyncs from a day
                  before the last message seen; dedupe makes that safe.
- `mail_renew`    keep the stream alive (renew within 48 hours of expiry; recreate when it is gone).
- `mail_send`     one approved reply, as a state machine recorded before each provider call: pending →
                  drafted (createReply, carrying our intent id) → sent. A retry after a crash resumes from
                  the recorded state and checks Sent Items for the intent id, so a reply is never sent twice.
Failures are written to the mailbox (`last_error`) and to `mail_sync_events`, which drive the health view.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, text, update

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.core.crypto import sha256
from command_inbox.core.jobs import JobRow, enqueue
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Mailbox, MailMessage, MailSendIntent, MailSyncEvent
from command_inbox.mail import pipeline
from command_inbox.mail.credentials import TokenProvider
from command_inbox.mail.types import (
    ConnectorError,
    CursorExpired,
    MailboxConnector,
    NotFound,
    ReauthRequired,
    StreamGone,
    Throttled,
)

log = structlog.get_logger(__name__)

PROVIDER_OF = {"microsoft": "graph", "google": "gmail"}
LABEL_TRIAGED, LABEL_REPLIED, LABEL_PERSON = "CI: triaged", "CI: replied", "CI: needs person"


def connector_for(org_id: str, mb: Mailbox) -> MailboxConnector:
    """The connector for a mailbox row. Tests replace this to inject a recorded or fake provider."""
    provider = PROVIDER_OF.get(mb.provider, mb.provider)
    token = TokenProvider(org_id, mb.id, provider)
    if provider == "graph":
        from command_inbox.mail.graph import GraphConnector

        return GraphConnector(token, key=mb.id)
    if provider == "gmail":
        from command_inbox.mail.gmail import GmailConnector

        return GmailConnector(token, key=mb.id)
    raise ConnectorError(f"{mb.provider} mailboxes have no connector")


def webhook_urls(provider: str) -> tuple[str, str] | None:
    base = (settings.mail_webhook_base_url or "").rstrip("/")
    if not base.startswith("https://"):
        return None  # providers only deliver to public HTTPS: poll instead
    if provider == "graph":
        return f"{base}/v1/hooks/graph", f"{base}/v1/hooks/graph/lifecycle"
    return f"{base}/v1/hooks/gmail", f"{base}/v1/hooks/gmail"


async def _load(org_id: str, mailbox_id: str) -> Mailbox | None:
    async with tenant_tx(org_id) as tx:
        mb = (
            await tx.execute(select(Mailbox).where(Mailbox.org_id == org_id, Mailbox.id == mailbox_id))
        ).scalar_one_or_none()
        if mb is not None:
            tx.expunge(mb)
        return mb


async def _event(org_id: str, mailbox_id: str, kind: str, ok: bool = True, **detail: Any) -> None:
    async with tenant_tx(org_id) as tx:
        tx.add(MailSyncEvent(org_id=org_id, mailbox_id=mailbox_id, kind=kind, ok=ok, detail=detail))


async def _set(org_id: str, mailbox_id: str, **values: Any) -> None:
    async with tenant_tx(org_id) as tx:
        await tx.execute(
            update(Mailbox).where(Mailbox.org_id == org_id, Mailbox.id == mailbox_id).values(**values)
        )


async def _failed(org_id: str, mailbox_id: str, kind: str, err: Exception) -> None:
    message = str(err)[:500] or type(err).__name__
    await _set(
        org_id,
        mailbox_id,
        last_error=message,
        last_error_at=clock.now(),
        # A missing or revoked sign-in needs a person; anything else is a degraded, retried connection.
        connection="reauth_required" if isinstance(err, ReauthRequired) else "degraded",
    )
    await _event(org_id, mailbox_id, kind, ok=False, error=message, type=type(err).__name__)


async def enqueue_sync(
    tx: Any, org_id: str, mailbox_id: str, reason: str, *, every: int | None = None
) -> None:
    """Queue a catch-up.

    - Notifications debounce: every notification inside a 5-second window joins one job that runs at the
      *end* of the window, so a burst costs one sync and nothing that arrives during it is lost.
    - Sweeps and polls (`every` seconds) run at once, at most once per period.
    - Anything else (a person pressing "sync", a test) always runs.
    """
    import uuid

    now = clock.now()
    if every:
        key, run_at = f"mail-sync:{mailbox_id}:p{every}:{int(now.timestamp() // every)}", now
    elif reason in ("manual", "test"):
        key, run_at = f"mail-sync:{mailbox_id}:{uuid.uuid4()}", now
    else:
        end = (int(now.timestamp() // 5) + 1) * 5
        key, run_at = f"mail-sync:{mailbox_id}:n{end}", datetime.fromtimestamp(end, tz=now.tzinfo)
    await enqueue(
        tx,
        org_id,
        "mail_sync",
        {"mailboxId": mailbox_id, "reason": reason},
        run_at=run_at,
        dedupe_key=key,
        max_attempts=6,
    )


# ── connect ────────────────────────────────────────────────────────────────────────────────────────────


async def run_mail_connect(job: JobRow) -> None:
    org_id, mailbox_id = job.org_id, str(job.payload["mailboxId"])
    mb = await _load(org_id, mailbox_id)
    if mb is None or mb.connection not in ("connecting", "syncing"):
        return
    conn = connector_for(org_id, mb)
    try:
        baseline = await conn.catch_up(None, since=clock.now())
        values: dict[str, Any] = {
            "cursor": baseline.cursor,
            "cursor_updated_at": clock.now(),
            "last_sweep_at": clock.now(),
            "connection": "live",
            "last_error": "",
        }
        urls = webhook_urls(conn.provider)
        if urls and not mb.stream_id:
            stream = await conn.start_stream(notify_url=urls[0], lifecycle_url=urls[1])
            values |= {
                "stream_id": stream.id,
                "stream_secret_hash": sha256(stream.secret),
                "stream_expires_at": stream.expires_at,
            }
        await _set(org_id, mailbox_id, **values)
        await _event(org_id, mailbox_id, "connected", mode="notifications" if urls else "polling")
    except ReauthRequired as err:
        await _failed(org_id, mailbox_id, "connect", err)
    except ConnectorError as err:
        await _failed(org_id, mailbox_id, "connect", err)
        raise


# ── catch up ───────────────────────────────────────────────────────────────────────────────────────────


async def run_mail_sync(job: JobRow) -> None:
    org_id, mailbox_id = job.org_id, str(job.payload["mailboxId"])
    mb = await _load(org_id, mailbox_id)
    if mb is None or mb.connection not in ("live", "degraded", "syncing"):
        return
    conn = connector_for(org_id, mb)
    since = (mb.last_message_at or mb.cursor_updated_at or clock.now()) - timedelta(days=1)
    try:
        try:
            delta = await conn.catch_up(mb.cursor, since=since)
            resync = False
        except CursorExpired:
            delta = await conn.catch_up(None, since=since)
            resync = True
        outcomes: dict[str, int] = {}
        for pid in delta.ids:
            try:
                raw = await conn.fetch(pid)
            except NotFound:
                continue  # moved or deleted before we got to it
            out = await pipeline.process(org_id, mb, raw)
            outcomes[out.kind] = outcomes.get(out.kind, 0) + 1
            if not out.duplicate and out.kind in ("ticket", "thread"):
                try:
                    await conn.label(pid, [LABEL_TRIAGED])
                except ConnectorError as err:
                    log.info("could not label message", mailbox_id=mailbox_id, error=str(err))
        await _set(
            org_id,
            mailbox_id,
            cursor=delta.cursor,
            cursor_updated_at=clock.now(),
            last_sweep_at=clock.now(),
            connection="live",
            last_error="",
        )
        await _event(
            org_id,
            mailbox_id,
            "sync",
            reason=job.payload.get("reason", ""),
            resync=resync,
            seen=len(delta.ids),
            outcomes=outcomes,
        )
    except ReauthRequired as err:
        await _failed(org_id, mailbox_id, "sync", err)
    except Throttled as err:
        await _event(org_id, mailbox_id, "sync", ok=False, error="throttled", retryAfter=err.retry_after)
        raise
    except ConnectorError as err:
        await _failed(org_id, mailbox_id, "sync", err)
        raise


# ── stream renewal ─────────────────────────────────────────────────────────────────────────────────────


async def run_mail_renew(job: JobRow) -> None:
    org_id, mailbox_id = job.org_id, str(job.payload["mailboxId"])
    mb = await _load(org_id, mailbox_id)
    if mb is None or mb.connection not in ("live", "degraded"):
        return
    conn = connector_for(org_id, mb)
    urls = webhook_urls(conn.provider)
    if urls is None:
        return
    try:
        reauthorize = job.payload.get("reason") == "reauthorize"
        if mb.stream_id and reauthorize and hasattr(conn, "reauthorize_stream"):
            await conn.reauthorize_stream(mb.stream_id)  # type: ignore[attr-defined]
        # Graph subscriptions: renew within 48 h of expiry. Gmail watches: renew daily, as Google advises.
        margin = timedelta(hours=48) if conn.provider == "graph" else timedelta(days=6)
        if (
            mb.stream_id
            and mb.stream_expires_at
            and mb.stream_expires_at - clock.now() > margin
            and not reauthorize
        ):
            return
        try:
            if not mb.stream_id:
                raise StreamGone("no stream")
            expires = await conn.renew_stream(mb.stream_id)
            await _set(org_id, mailbox_id, stream_expires_at=expires)
            await _event(org_id, mailbox_id, "stream_renewed", expiresAt=expires.isoformat())
        except StreamGone:
            stream = await conn.start_stream(notify_url=urls[0], lifecycle_url=urls[1])
            await _set(
                org_id,
                mailbox_id,
                stream_id=stream.id,
                stream_secret_hash=sha256(stream.secret),
                stream_expires_at=stream.expires_at,
            )
            await _event(org_id, mailbox_id, "stream_created", expiresAt=stream.expires_at.isoformat())
            async with tenant_tx(org_id) as tx:
                await enqueue_sync(tx, org_id, mailbox_id, "manual")
    except ReauthRequired as err:
        await _failed(org_id, mailbox_id, "renew", err)
    except ConnectorError as err:
        await _failed(org_id, mailbox_id, "renew", err)
        raise


# ── sending ────────────────────────────────────────────────────────────────────────────────────────────


async def live_mailbox_for_ticket(tx: Any, org_id: str, mailbox_id: str | None) -> Mailbox | None:
    if not mailbox_id:
        return None
    mb = (
        await tx.execute(select(Mailbox).where(Mailbox.org_id == org_id, Mailbox.id == mailbox_id))
    ).scalar_one_or_none()
    return mb if mb is not None and mb.connection in ("live", "degraded") and mb.send_enabled else None


async def queue_send(tx: Any, org_id: str, mb: Mailbox, ticket_id: str, source: str, source_id: str) -> str:
    """Record the intent to send (inside the approval's transaction) and queue the provider call."""
    from sqlalchemy.dialects.postgresql import insert

    reply_to = (
        await tx.execute(
            select(MailMessage.provider_message_id)
            .where(
                MailMessage.org_id == org_id,
                MailMessage.ticket_id == ticket_id,
                MailMessage.direction == "inbound",
            )
            .order_by(MailMessage.received_at.desc().nulls_last(), MailMessage.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    intent_id = (
        await tx.execute(
            insert(MailSendIntent)
            .values(
                org_id=org_id,
                mailbox_id=mb.id,
                ticket_id=ticket_id,
                source=source,
                source_id=source_id,
                reply_to_provider_id=reply_to,
            )
            .on_conflict_do_nothing()
            .returning(MailSendIntent.id)
        )
    ).scalar_one_or_none()
    if intent_id is None:
        intent_id = (
            await tx.execute(
                select(MailSendIntent.id).where(
                    MailSendIntent.org_id == org_id,
                    MailSendIntent.source == source,
                    MailSendIntent.source_id == source_id,
                )
            )
        ).scalar_one()
    await enqueue(
        tx,
        org_id,
        "mail_send",
        {"intentId": str(intent_id)},
        dedupe_key=f"mail-send:{intent_id}",
        max_attempts=8,
    )
    return str(intent_id)


async def run_mail_send(job: JobRow) -> None:
    org_id, intent_id = job.org_id, str(job.payload["intentId"])
    async with tenant_tx(org_id) as tx:
        it = (
            await tx.execute(
                select(MailSendIntent).where(MailSendIntent.org_id == org_id, MailSendIntent.id == intent_id)
            )
        ).scalar_one_or_none()
        if it is None or it.state in ("sent", "failed"):
            return
        tx.expunge(it)
        body = await _reply_body(tx, org_id, it)
    mb = await _load(org_id, it.mailbox_id)
    if mb is None:
        return
    conn = connector_for(org_id, mb)
    try:
        if not it.reply_to_provider_id:
            raise ConnectorError(
                "The customer's message is not in the connected mailbox, so there is nothing to reply to."
            )
        draft_id = it.provider_draft_id
        if it.state == "drafted" and draft_id:
            try:
                await conn.send_draft(draft_id)
            except NotFound:
                # The draft is gone: either it was sent (crash after send) or someone deleted it.
                sent = await conn.find_sent(intent_id)
                if sent:
                    await _mark_sent(org_id, it, sent, conn)
                    return
                draft_id = None
        if draft_id is None or it.state == "pending":
            draft_id = await conn.create_reply(it.reply_to_provider_id, body, intent_id=intent_id)
            async with tenant_tx(org_id) as tx:
                await tx.execute(
                    update(MailSendIntent)
                    .where(MailSendIntent.id == intent_id)
                    .values(state="drafted", provider_draft_id=draft_id, attempts=MailSendIntent.attempts + 1)
                )
            await conn.send_draft(draft_id)
        await _mark_sent(org_id, it, None, conn)
    except ReauthRequired as err:
        await _send_failed(org_id, it, err, final=False)
        raise
    except Throttled:
        raise
    except ConnectorError as err:
        final = job.attempts + 1 >= job.max_attempts
        await _send_failed(org_id, it, err, final=final)
        raise


async def _reply_body(tx: Any, org_id: str, it: MailSendIntent) -> str:
    from command_inbox.db.models import Draft, Reply

    if it.source == "draft":
        d = (
            await tx.execute(select(Draft).where(Draft.org_id == org_id, Draft.id == it.source_id))
        ).scalar_one()
        return d.current_body
    r = (await tx.execute(select(Reply).where(Reply.org_id == org_id, Reply.id == it.source_id))).scalar_one()
    return r.body


async def _mark_sent(
    org_id: str, it: MailSendIntent, provider_id: str | None, conn: MailboxConnector
) -> None:
    async with tenant_tx(org_id) as tx:
        await tx.execute(
            update(MailSendIntent)
            .where(MailSendIntent.id == it.id)
            .values(state="sent", sent_at=clock.now(), provider_message_id=provider_id, error="")
        )
        tx.add(
            MailSyncEvent(
                org_id=org_id,
                mailbox_id=it.mailbox_id,
                kind="send",
                detail={"intentId": it.id, "ticketId": it.ticket_id},
            )
        )
    if it.reply_to_provider_id:
        try:
            await conn.label(it.reply_to_provider_id, [LABEL_REPLIED])
        except ConnectorError as err:
            log.info("could not label replied message", error=str(err))


async def _send_failed(org_id: str, it: MailSendIntent, err: Exception, *, final: bool) -> None:
    from command_inbox.modules.tickets.ops import system_note

    async with tenant_tx(org_id) as tx:
        await tx.execute(
            update(MailSendIntent)
            .where(MailSendIntent.id == it.id)
            .values(error=str(err)[:500], **({"state": "failed"} if final else {}))
        )
        tx.add(
            MailSyncEvent(
                org_id=org_id,
                mailbox_id=it.mailbox_id,
                kind="send",
                ok=False,
                detail={"intentId": it.id, "error": str(err)[:300], "final": final},
            )
        )
        if final:
            await system_note(
                tx,
                org_id,
                it.ticket_id,
                f"The reply could not be sent from the mailbox: {str(err)[:200]}. Send it again or reply from Outlook.",
            )


# ── test mail ──────────────────────────────────────────────────────────────────────────────────────────


async def run_mail_test(job: JobRow) -> None:
    org_id, mailbox_id, nonce = job.org_id, str(job.payload["mailboxId"]), str(job.payload["nonce"])
    mb = await _load(org_id, mailbox_id)
    if mb is None:
        return
    conn = connector_for(org_id, mb)
    try:
        await conn.send_test(
            f"Command Inbox connection test {nonce[:8]}",
            "This message checks that Command Inbox can send from and receive into this mailbox. "
            "It is recorded as a test and never becomes a ticket.",
            nonce,
        )
        await _event(org_id, mailbox_id, "test_sent", nonce=nonce[:8])
        async with tenant_tx(org_id) as tx:
            await enqueue(
                tx,
                org_id,
                "mail_sync",
                {"mailboxId": mailbox_id, "reason": "test"},
                run_at=clock.now() + timedelta(seconds=15),
                dedupe_key=f"mail-test-sync:{nonce}",
            )
    except ConnectorError as err:
        await _failed(org_id, mailbox_id, "test", err)


# ── scheduling ─────────────────────────────────────────────────────────────────────────────────────────


async def schedule_mail(now: datetime | None = None) -> int:
    """Called every minute by the scheduler: sweeps and renewals for every connected mailbox."""
    now = now or clock.now()
    async with global_tx() as g:
        rows = (await g.execute(text("select org_id, mailbox_id from ci_live_mailboxes()"))).all()
    for org_id, mailbox_id in rows:
        org_id, mailbox_id = str(org_id), str(mailbox_id)
        mb = await _load(org_id, mailbox_id)
        if mb is None:
            continue
        streaming = bool(mb.stream_id) and webhook_urls(PROVIDER_OF.get(mb.provider, mb.provider)) is not None
        every = settings.mail_sweep_minutes * 60 if streaming else settings.mail_poll_seconds
        async with tenant_tx(org_id) as tx:
            await enqueue_sync(tx, org_id, mailbox_id, "sweep" if streaming else "poll", every=every)
            window = int(now.timestamp() // (12 * 3600))
            await enqueue(
                tx,
                org_id,
                "mail_renew",
                {"mailboxId": mailbox_id},
                dedupe_key=f"mail-renew:{mailbox_id}:{window}",
            )
    return len(rows)
