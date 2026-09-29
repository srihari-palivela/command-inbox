"""Mailbox connections for the admin: one mailbox per tenant at launch (plan limits), connected by OAuth.

Sending stays off until the connection test round trip has succeeded and an admin turns it on: until then
the AI reads and drafts, and people reply from their own mail client.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.crypto import random_token
from command_inbox.core.errors import conflict, not_found
from command_inbox.core.jobs import enqueue
from command_inbox.db.models import Deployment, Mailbox, MailMessage, MailSyncEvent, Org
from command_inbox.mail.sync import PROVIDER_OF, enqueue_sync, webhook_urls
from command_inbox.modules.intake import oauth
from command_inbox.modules.mailboxes.health import overall, signals
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import CreateMailboxBody, MailboxSendingBody

EVENT_WORDS = {
    "authorized": "Signed in as the mailbox",
    "connected": "Connected",
    "sync": "Caught up",
    "message": "Message processed",
    "send": "Reply sent",
    "test_sent": "Test mail sent",
    "token_refresh": "Sign-in refreshed",
    "stream_renewed": "Notifications renewed",
    "stream_created": "Notifications started",
    "notification": "Notification received",
    "lifecycle": "Provider lifecycle event",
    "connect": "Connection",
    "renew": "Notification renewal",
    "test": "Connection test",
    "disconnected": "Disconnected",
}


def _summary(e: MailSyncEvent) -> str:
    d: dict[str, Any] = e.detail or {}
    base = EVENT_WORDS.get(e.kind, e.kind)
    if not e.ok:
        return f"{base} failed: {d.get('error', '')}".strip()
    if e.kind == "sync":
        outcomes = d.get("outcomes") or {}
        parts = [f"{v} {k.replace('_', ' ')}" for k, v in outcomes.items()]
        return f"{base}: {d.get('seen', 0)} new" + (f" ({', '.join(parts)})" if parts else "")
    if e.kind == "message":
        return f"{base}: {str(d.get('outcome', '')).replace('_', ' ')}"
    if e.kind == "connected":
        return f"{base} ({d.get('mode', '')})"
    return base


async def _connection_dto(tx: AsyncSession, org_id: str, mb: Mailbox) -> dto.MailboxConnectionDTO:
    now = clock.now()
    day = now - timedelta(hours=24)
    events = (
        (
            await tx.execute(
                select(MailSyncEvent)
                .where(MailSyncEvent.org_id == org_id, MailSyncEvent.mailbox_id == mb.id)
                .order_by(MailSyncEvent.at.desc(), MailSyncEvent.id.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    counts = {
        f"{kind}:{'true' if ok else 'false'}": int(n)
        for kind, ok, n in (
            await tx.execute(
                select(MailSyncEvent.kind, MailSyncEvent.ok, func.count())
                .where(
                    MailSyncEvent.org_id == org_id, MailSyncEvent.mailbox_id == mb.id, MailSyncEvent.at >= day
                )
                .group_by(MailSyncEvent.kind, MailSyncEvent.ok)
            )
        ).all()
    }
    throttled = int(
        (
            await tx.execute(
                select(func.count())
                .select_from(MailSyncEvent)
                .where(
                    MailSyncEvent.org_id == org_id,
                    MailSyncEvent.mailbox_id == mb.id,
                    MailSyncEvent.at >= day,
                    MailSyncEvent.detail["error"].astext == "throttled",
                )
            )
        ).scalar_one()
    )
    messages = int(
        (
            await tx.execute(
                select(func.count())
                .select_from(MailMessage)
                .where(
                    MailMessage.org_id == org_id,
                    MailMessage.mailbox_id == mb.id,
                    MailMessage.created_at >= day,
                )
            )
        ).scalar_one()
    )
    provider = PROVIDER_OF.get(mb.provider, mb.provider)
    streaming = bool(mb.stream_id) and webhook_urls(provider) is not None
    sig = signals(
        mb,
        now,
        streaming=streaming,
        sends_ok=counts.get("send:true", 0),
        sends_failed=counts.get("send:false", 0),
        throttled=throttled,
        calls=counts.get("sync:true", 0) + counts.get("sync:false", 0),
    )
    connected = mb.connection not in ("not_connected", "disconnected")
    return dto.MailboxConnectionDTO(
        id=mb.id,
        address=mb.address,
        provider=mb.provider,  # type: ignore[arg-type]
        connection=mb.connection,  # type: ignore[arg-type]
        account=mb.provider_account,
        mode=("notifications" if streaming else "polling") if connected else None,
        send_enabled=mb.send_enabled,
        level=overall(mb, sig),  # type: ignore[arg-type]
        signals=sig,
        last_error=mb.last_error,
        last_error_at=iso_ms(mb.last_error_at) if mb.last_error_at else None,
        last_message_at=iso_ms(mb.last_message_at) if mb.last_message_at else None,
        last_test_at=iso_ms(mb.last_test_at) if mb.last_test_at else None,
        last_test_ok_at=iso_ms(mb.last_test_ok_at) if mb.last_test_ok_at else None,
        messages24h=messages,
        events=[
            dto.MailSyncEventDTO(at=iso_ms(e.at), kind=e.kind, ok=e.ok, summary=_summary(e)) for e in events
        ],
    )


async def _limit(tx: AsyncSession, org_id: str) -> int:
    limits = (await tx.execute(select(Org.limits).where(Org.id == org_id))).scalar_one() or {}
    return int(limits.get("mailboxes", 1))


async def overview(tx: AsyncSession, ctx: Ctx) -> dto.MailConnectorsDTO:
    require(ctx, "setup.view", "see mailboxes")
    rows = (
        (
            await tx.execute(
                select(Mailbox).where(Mailbox.org_id == ctx.org_id).order_by(Mailbox.sort, Mailbox.created_at)
            )
        )
        .scalars()
        .all()
    )
    return dto.MailConnectorsDTO(
        providers=dto.MailConnectorsDTOProviders(
            microsoft=oauth.is_configured("microsoft"), google=oauth.is_configured("google")
        ),
        webhooks=webhook_urls("graph") is not None,
        mailbox_limit=await _limit(tx, ctx.org_id),
        mailboxes=[await _connection_dto(tx, ctx.org_id, m) for m in rows],
    )


async def _get(tx: AsyncSession, ctx: Ctx, mailbox_id: str, lock: bool = False) -> Mailbox:
    q = select(Mailbox).where(Mailbox.org_id == ctx.org_id, Mailbox.id == mailbox_id)
    mb = (await tx.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if mb is None:
        raise not_found("Mailbox")
    return mb


async def create(tx: AsyncSession, ctx: Ctx, body: CreateMailboxBody) -> dto.MailboxConnectionDTO:
    require(ctx, "setup.edit", "add mailboxes")
    count = int(
        (
            await tx.execute(select(func.count()).select_from(Mailbox).where(Mailbox.org_id == ctx.org_id))
        ).scalar_one()
    )
    limit = await _limit(tx, ctx.org_id)
    if count >= limit:
        raise conflict(
            "mailbox_limit", f"This workspace's plan allows {limit} mailbox{'es' if limit != 1 else ''}."
        )
    from sqlalchemy import text

    taken = (await tx.execute(text("select ci_mailbox_org(:a)"), {"a": body.address})).scalar()
    if taken:
        raise conflict("mailbox_taken", f"{body.address} is already connected to a workspace.")
    deployment = (
        await tx.execute(
            select(Deployment.id)
            .where(Deployment.org_id == ctx.org_id)
            .order_by(Deployment.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    mb = Mailbox(
        org_id=ctx.org_id,
        address=body.address,
        provider=body.provider,
        team_label=body.team_label,
        state="observe",
        deployment_id=deployment,
        connection="not_connected",
        sort=count,
    )
    tx.add(mb)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="mailbox.created",
        entity="mailbox",
        entity_id=mb.id,
        summary=f"{ctx.user.name} added the mailbox {body.address} ({body.provider})",
        data={"address": body.address, "provider": body.provider},
    )
    await tx.refresh(mb)
    return await _connection_dto(tx, ctx.org_id, mb)


async def start_test(tx: AsyncSession, ctx: Ctx, mailbox_id: str) -> dto.MailboxConnectionDTO:
    require(ctx, "setup.edit", "test mailboxes")
    mb = await _get(tx, ctx, mailbox_id, lock=True)
    if mb.connection not in ("live", "degraded"):
        raise conflict("not_connected", "Connect the mailbox before testing it.")
    nonce = random_token(16)
    mb.last_test_at = clock.now()
    await enqueue(
        tx, ctx.org_id, "mail_test", {"mailboxId": mb.id, "nonce": nonce}, dedupe_key=f"mail-test:{nonce}"
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="mailbox.test_started",
        entity="mailbox",
        entity_id=mb.id,
        summary=f"{ctx.user.name} started a connection test of {mb.address}",
    )
    return await _connection_dto(tx, ctx.org_id, mb)


async def set_sending(
    tx: AsyncSession, ctx: Ctx, mailbox_id: str, body: MailboxSendingBody
) -> dto.MailboxConnectionDTO:
    require(ctx, "setup.edit", "enable sending")
    mb = await _get(tx, ctx, mailbox_id, lock=True)
    if body.enabled and (mb.last_test_ok_at is None or mb.connection not in ("live", "degraded")):
        raise conflict("test_required", "Run the connection test successfully before turning sending on.")
    if mb.send_enabled != body.enabled:
        mb.send_enabled = body.enabled
        await audit(
            tx,
            ctx.org_id,
            actor=actor_of(ctx),
            action="mailbox.sending_changed",
            entity="mailbox",
            entity_id=mb.id,
            summary=f"{ctx.user.name} turned sending {'on' if body.enabled else 'off'} for {mb.address}",
            data={"enabled": body.enabled},
        )
    return await _connection_dto(tx, ctx.org_id, mb)


async def sync_now(tx: AsyncSession, ctx: Ctx, mailbox_id: str) -> dto.MailboxConnectionDTO:
    require(ctx, "setup.edit", "sync mailboxes")
    mb = await _get(tx, ctx, mailbox_id)
    if mb.connection not in ("live", "degraded", "syncing"):
        raise conflict("not_connected", "Connect the mailbox first.")
    await enqueue_sync(tx, ctx.org_id, mb.id, "manual")
    return await _connection_dto(tx, ctx.org_id, mb)


async def disconnect(tx: AsyncSession, ctx: Ctx, mailbox_id: str) -> dto.MailboxConnectionDTO:
    """Forget the sign-in and stop listening. The provider's own grant is revoked by the bank (remove the
    app's consent); we can only drop our copy."""
    require(ctx, "setup.edit", "disconnect mailboxes")
    mb = await _get(tx, ctx, mailbox_id, lock=True)
    stream_id = mb.stream_id
    mb.credentials_enc = None
    mb.connection = "disconnected"
    mb.send_enabled = False
    mb.stream_id = mb.stream_secret_hash = None
    mb.stream_expires_at = mb.token_expires_at = None
    mb.cursor = None
    tx.add(
        MailSyncEvent(
            org_id=ctx.org_id, mailbox_id=mb.id, kind="disconnected", detail={"hadStream": bool(stream_id)}
        )
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="mailbox.disconnected",
        entity="mailbox",
        entity_id=mb.id,
        summary=f"{ctx.user.name} disconnected {mb.address}",
    )
    return await _connection_dto(tx, ctx.org_id, mb)
