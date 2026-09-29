"""The platform audit log: one hash chain over every operator action, append-only for the app role.

Tenant-affecting actions are also written to that tenant's own chain (actor kind "system", named for the
operator), so the bank sees in its audit log what the vendor did to its workspace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import GENESIS
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.crypto import canonical_json, sha256
from command_inbox.db.models import PlatformAuditEvent


def _hash(
    prev: str,
    at: datetime,
    operator_id: str | None,
    operator_email: str,
    action: str,
    tenant_id: str | None,
    summary: str,
    data: dict[str, Any],
) -> str:
    return sha256(
        prev
        + canonical_json(
            {
                "at": iso_ms(at),
                "operatorId": operator_id,
                "operatorEmail": operator_email,
                "action": action,
                "tenantId": tenant_id,
                "summary": summary,
                "data": data,
            }
        )
    )


async def platform_audit(
    tx: AsyncSession,
    *,
    operator_id: str | None,
    operator_email: str,
    action: str,
    summary: str,
    tenant_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    await tx.execute(text("select pg_advisory_xact_lock(hashtextextended('platform:audit', 0))"))
    prev = (
        await tx.execute(select(PlatformAuditEvent.hash).order_by(PlatformAuditEvent.seq.desc()).limit(1))
    ).scalar_one_or_none() or GENESIS
    now = clock.now()
    at = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    payload = data or {}
    tid = str(tenant_id) if tenant_id else None
    oid = str(operator_id) if operator_id else None
    tx.add(
        PlatformAuditEvent(
            at=at,
            operator_id=oid,
            operator_email=operator_email,
            action=action,
            tenant_id=tid,
            summary=summary,
            data=payload,
            prev_hash=prev,
            hash=_hash(prev, at, oid, operator_email, action, tid, summary, payload),
        )
    )
    await tx.flush()


async def verify_platform_chain(tx: AsyncSession) -> dict[str, Any]:
    prev, count, cursor = GENESIS, 0, 0
    while True:
        batch = (
            (
                await tx.execute(
                    select(PlatformAuditEvent)
                    .where(PlatformAuditEvent.seq > cursor)
                    .order_by(PlatformAuditEvent.seq)
                    .limit(1000)
                )
            )
            .scalars()
            .all()
        )
        if not batch:
            break
        for r in batch:
            expected = _hash(
                prev,
                r.at,
                str(r.operator_id) if r.operator_id else None,
                r.operator_email,
                r.action,
                str(r.tenant_id) if r.tenant_id else None,
                r.summary,
                r.data or {},
            )
            if r.prev_hash != prev or r.hash != expected:
                return {"ok": False, "events": count, "brokenAt": r.seq}
            prev, count, cursor = r.hash, count + 1, r.seq
    return {"ok": True, "events": count, "brokenAt": None}
