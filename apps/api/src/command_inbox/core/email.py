"""Transactional email (invitations and account notices), sent by the worker from an outbox table.

`queue_email` writes the message and its send job in the caller's transaction, so an invitation exists if
and only if its email is queued. The body can hold a single-use link, so it is sealed at rest (AES-GCM) and
dropped once the message is sent.

Without SMTP_HOST (development and tests only; production refuses to start without it) messages are not
sent: they are logged and kept in a small in-memory mailbox (`dev_mailbox`) that the development API exposes.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import uuid
from collections import deque
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.config import settings
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.crypto import decrypt, encrypt
from command_inbox.core.jobs import JobRow, enqueue
from command_inbox.db.engine import global_tx
from command_inbox.db.models import EmailMessage

log = structlog.get_logger(__name__)

# Development only: the last messages "sent" without SMTP, newest last.
dev_mailbox: deque[dict[str, Any]] = deque(maxlen=50)


async def queue_email(
    tx: AsyncSession, *, tenant_id: str, template: str, to: str, subject: str, text: str
) -> str:
    message_id = str(uuid.uuid4())
    tx.add(
        EmailMessage(
            id=message_id,
            tenant_id=tenant_id,
            template=template,
            to_addr=to,
            subject=subject,
            body_sealed=encrypt(text, purpose="email", aad=message_id),
        )
    )
    await tx.flush()
    await enqueue(tx, tenant_id, "send_email", {"messageId": message_id}, dedupe_key=f"email:{message_id}")
    return message_id


def _smtp_send(to: str, subject: str, text: str) -> str:
    msg = MimeMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg_id = make_msgid(domain=settings.mail_from.rsplit("@", 1)[-1].rstrip(">"))
    msg["Message-ID"] = msg_id
    msg.set_content(text)
    with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(msg)
    return msg_id


async def run_send_email(job: JobRow) -> None:
    """Idempotent: a message already sent (or logged) is not sent again."""
    message_id = str(job.payload["messageId"])
    async with global_tx() as tx:
        m = (
            await tx.execute(select(EmailMessage).where(EmailMessage.id == message_id).with_for_update())
        ).scalar_one_or_none()
        if m is None or m.state in ("sent", "logged") or m.body_sealed is None:
            return
        text = decrypt(m.body_sealed, purpose="email", aad=message_id)
        to, subject = m.to_addr, m.subject
        await tx.execute(
            update(EmailMessage)
            .where(EmailMessage.id == message_id)
            .values(attempts=EmailMessage.attempts + 1)
        )
    try:
        if settings.smtp_host:
            provider_id: str | None = await asyncio.to_thread(_smtp_send, to, subject, text)
            state = "sent"
        else:
            provider_id, state = None, "logged"
            dev_mailbox.append(
                {"id": message_id, "to": to, "subject": subject, "text": text, "at": iso_ms(clock.now())}
            )
            log.info("email not sent (no SMTP_HOST); kept in the development mailbox", to=to, subject=subject)
    except Exception as err:
        async with global_tx() as tx:
            await tx.execute(
                update(EmailMessage).where(EmailMessage.id == message_id).values(last_error=str(err)[:500])
            )
        raise
    async with global_tx() as tx:
        await tx.execute(
            update(EmailMessage)
            .where(EmailMessage.id == message_id)
            .values(
                state=state,
                sent_at=clock.now(),
                provider_message_id=provider_id,
                body_sealed=None,
                last_error="",
            )
        )
