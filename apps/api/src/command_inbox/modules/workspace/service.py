"""Personal settings, the admin overview (where mail arrives, connectors) and audit verification."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import verify_audit_chain
from command_inbox.core.context import Ctx
from command_inbox.db.models import Connector, Department, Mailbox, UserSetting
from command_inbox.modules.workspace.schemas import AdminOut, MailboxOut
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import SettingsBody

GUARDRAILS = [
    "PII masked before any text reaches the classifier; unmasked values are re-bound only at execution.",
    "Outbound email is only ever sent under a named approver — the AI holds no send scope of its own.",
    "Hard stop keywords (ombudsman, legal notice, regulator, fraud) suspend all generation on the thread.",
    "Every action carries an idempotency key; a duplicate approval cannot execute twice.",
    "Immutable, hash-chained audit log with actor, timestamp, source and confidence for every decision.",
]


async def update_settings(tx: AsyncSession, ctx: Ctx, body: SettingsBody) -> None:
    """Merge preference flags; keep the signature unless a new one is sent."""
    cur = (
        await tx.execute(
            select(UserSetting)
            .where(UserSetting.org_id == ctx.org_id, UserSetting.user_id == ctx.user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    prefs = {**((cur.prefs if cur else None) or {}), **(body.prefs or {})}
    signature = body.signature if body.signature is not None else (cur.signature if cur else "")
    await tx.execute(
        insert(UserSetting)
        .values(org_id=ctx.org_id, user_id=ctx.user.id, prefs=prefs, signature=signature)
        .on_conflict_do_update(
            index_elements=[UserSetting.org_id, UserSetting.user_id],
            set_={"prefs": prefs, "signature": signature},
        )
    )


async def admin_overview(tx: AsyncSession, ctx: Ctx) -> AdminOut:
    require(ctx, "setup.view", "view mailboxes and connectors")
    mailboxes = (
        (
            await tx.execute(
                select(Mailbox).where(Mailbox.org_id == ctx.org_id).order_by(Mailbox.sort, Mailbox.created_at)
            )
        )
        .scalars()
        .all()
    )
    dept_names = dict(
        (
            await tx.execute(select(Department.id, Department.name).where(Department.org_id == ctx.org_id))
        ).all()
    )
    connectors = (
        (await tx.execute(select(Connector).where(Connector.org_id == ctx.org_id).order_by(Connector.sort)))
        .scalars()
        .all()
    )
    return AdminOut(
        mailboxes=[
            MailboxOut(
                id=m.id,
                address=m.address,
                department=m.team_label or dept_names.get(m.department_id or "") or "Unassigned",
                permissions=m.permissions or [],
                volume24h=m.volume_24h,
                state=m.state,
                provider=m.provider,
            )
            for m in mailboxes
        ],
        connectors=[
            dto.ConnectorDTO(id=c.id, abbr=c.abbr, name=c.name, scope=c.scope, state=c.state)
            for c in connectors
        ],
        guardrails=GUARDRAILS,
    )


async def verify_audit(tx: AsyncSession, ctx: Ctx) -> dto.AuditVerifyDTO:
    require(ctx, "audit.verify", "verify the audit log")
    result = await verify_audit_chain(tx, ctx.org_id)
    return dto.AuditVerifyDTO(ok=result["ok"], events=result["events"], broken_at=result["brokenAt"])
