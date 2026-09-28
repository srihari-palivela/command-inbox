"""Hash-chained, append-only audit log.

Each tenant's events form a chain: every row stores the previous row's hash and its own hash over a
canonical form of the event. Editing, inserting or deleting a row breaks the chain at that point, and the
database role the app runs as cannot UPDATE or DELETE audit rows at all (trigger + revoked grants).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Actor
from command_inbox.core.crypto import canonical_json, sha256
from command_inbox.db.models import AuditEvent

GENESIS = "0" * 64
FeedTone = Literal["ok", "stop", "flag", "info", "muted"]


@dataclass(slots=True)
class Feed:
    """Also show this event in the AI activity rail."""

    tone: FeedTone
    meta: str


def _hash(
    prev: str,
    org_id: str,
    at: datetime,
    *,
    actor_kind: str,
    actor_id: str | None,
    actor_name: str,
    action: str,
    entity: str,
    entity_id: str | None,
    ticket_id: str | None,
    summary: str,
    data: dict[str, Any],
) -> str:
    return sha256(
        prev
        + canonical_json(
            {
                "orgId": org_id,
                "at": iso_ms(at),
                "actorKind": actor_kind,
                "actorId": actor_id,
                "actorName": actor_name,
                "action": action,
                "entity": entity,
                "entityId": entity_id,
                "ticketId": ticket_id,
                "summary": summary,
                "data": data,
            }
        )
    )


async def audit(
    tx: AsyncSession,
    org_id: str,
    *,
    actor: Actor,
    action: str,
    entity: str,
    summary: str,
    entity_id: str | None = None,
    ticket_id: str | None = None,
    data: dict[str, Any] | None = None,
    feed: Feed | None = None,
    at: datetime | None = None,
) -> None:
    """Append to the tenant's chain. A transaction-scoped advisory lock serialises writers per tenant."""
    await tx.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"{org_id}:audit"})
    prev = (
        await tx.execute(
            select(AuditEvent.hash)
            .where(AuditEvent.org_id == org_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none() or GENESIS
    at = at or clock.now()
    # Stored and hashed at millisecond precision so the chain verifies identically everywhere.
    at = at.replace(microsecond=(at.microsecond // 1000) * 1000)
    payload = data or {}
    eid = str(entity_id) if entity_id else None
    tid = str(ticket_id) if ticket_id else None
    tx.add(
        AuditEvent(
            org_id=org_id,
            at=at,
            actor_kind=actor.kind,
            actor_id=actor.id,
            actor_name=actor.name,
            action=action,
            entity=entity,
            entity_id=eid,
            ticket_id=tid,
            summary=summary,
            feed_tone=feed.tone if feed else None,
            feed_meta=feed.meta if feed else None,
            data=payload,
            prev_hash=prev,
            hash=_hash(
                prev,
                str(org_id),
                at,
                actor_kind=actor.kind,
                actor_id=actor.id,
                actor_name=actor.name,
                action=action,
                entity=entity,
                entity_id=eid,
                ticket_id=tid,
                summary=summary,
                data=payload,
            ),
        )
    )
    await tx.flush()


async def verify_audit_chain(tx: AsyncSession, org_id: str) -> dict[str, Any]:
    """Recompute the chain. Any edited, inserted or removed row breaks it at that sequence number."""
    prev, count, cursor = GENESIS, 0, 0
    while True:
        batch = (
            (
                await tx.execute(
                    select(AuditEvent)
                    .where(AuditEvent.org_id == org_id, AuditEvent.seq > cursor)
                    .order_by(AuditEvent.seq)
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
                str(org_id),
                r.at,
                actor_kind=r.actor_kind,
                actor_id=str(r.actor_id) if r.actor_id else None,
                actor_name=r.actor_name,
                action=r.action,
                entity=r.entity,
                entity_id=r.entity_id,
                ticket_id=str(r.ticket_id) if r.ticket_id else None,
                summary=r.summary,
                data=r.data or {},
            )
            if r.prev_hash != prev or r.hash != expected:
                return {"ok": False, "events": count, "brokenAt": r.seq}
            prev, count, cursor = r.hash, count + 1, r.seq
    return {"ok": True, "events": count, "brokenAt": None}
