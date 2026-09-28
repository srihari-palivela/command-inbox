"""Transactional outbox: record a domain event in the same transaction as the change, notify on commit.

NOTIFY is transactional in Postgres, so a rolled-back change emits nothing to browsers.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.db.models import OutboxEvent

Topic = Literal[
    "ticket.updated",
    "ticket.created",
    "activity.created",
    "notification.created",
    "gate.updated",
    "call.updated",
    "setup.updated",
    "people.updated",
    "insights.updated",
    "learning.updated",
    "deployment.updated",
    "rbac.updated",
    "eval.updated",
]
EVENTS_CHANNEL = "ci_events"


async def publish(tx: AsyncSession, org_id: str, topic: Topic, payload: dict[str, Any] | None = None) -> None:
    body = payload or {}
    tx.add(OutboxEvent(org_id=org_id, topic=topic, payload=body))
    # NOTIFY carries identifiers only (clients refetch), and never more than Postgres's 8000-byte limit:
    # an oversized payload would raise and roll back the business change it describes.
    small = {k: v for k, v in body.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 64}
    message = json.dumps({"orgId": str(org_id), "topic": topic, **small}, default=str, ensure_ascii=True)
    if len(message.encode()) > 7900:
        message = json.dumps({"orgId": str(org_id), "topic": topic})
    await tx.execute(text("select pg_notify(:ch, :msg)"), {"ch": EVENTS_CHANNEL, "msg": message})
