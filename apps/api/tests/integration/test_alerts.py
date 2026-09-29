"""System alerts: raised once per condition, kept current, resolved when the condition clears."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from command_inbox.core.clock import clock
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import Alert, Notification, Org
from command_inbox.modules.insights.alerts import Condition, apply, sweep
from tests.integration.admin_support import admin_conn, org_id

pytestmark = pytest.mark.integration


def cond(key: str, text: str = "x", email: bool = False) -> Condition:
    return Condition(key, "late", "Past deadline", "Team", text, "Act", "Team", "/tickets?due=risk", email)


async def _open(oid: str, key: str) -> list[Alert]:
    async with tenant_tx(oid) as tx:
        return list(
            (
                await tx.execute(
                    select(Alert).where(Alert.org_id == oid, Alert.key == key, Alert.resolved_at.is_(None))
                )
            )
            .scalars()
            .all()
        )


async def test_one_alert_per_condition_refreshed_then_resolved(app):
    oid = await org_id("meridian")
    key = f"sla:late:{uuid.uuid4()}"
    async with tenant_tx(oid) as tx:
        before = len(
            (await tx.execute(select(Notification).where(Notification.org_id == oid))).scalars().all()
        )
        r1 = await apply(tx, oid, [cond(key, "2 tickets past", email=True)])
    async with tenant_tx(oid) as tx:
        r2 = await apply(tx, oid, [cond(key, "3 tickets past")])
    assert r1["raised"] == 1 and r2 == {"raised": 0, "refreshed": 1, "resolved": 0}
    [a] = await _open(oid, key)
    assert a.text_ == "3 tickets past" and a.ref == "/tickets?due=risk"
    async with tenant_tx(oid) as tx:
        after = len(
            (await tx.execute(select(Notification).where(Notification.org_id == oid))).scalars().all()
        )
    assert after == before + 1  # notified once, not on every refresh
    conn = await admin_conn()
    try:
        emails = await conn.fetchval(
            "select count(*) from email_messages where tenant_id = $1 and template = 'alert'", uuid.UUID(oid)
        )
    finally:
        await conn.close()
    assert emails >= 1
    async with tenant_tx(oid) as tx:
        r3 = await apply(tx, oid, [])
    assert r3["resolved"] >= 1 and await _open(oid, key) == []


async def test_the_sweep_watches_deadlines_and_the_budget(app):
    oid = await org_id("apex")
    conn = await admin_conn()
    try:
        # One open ticket well past its deadline.
        tid = await conn.fetchval(
            """select id from tickets where org_id = $1 and status not in ('resolved','closed','waiting_customer')
                  and merged_into_id is null and paused_at is null order by number limit 1""",
            uuid.UUID(oid),
        )
        await conn.execute(
            "update tickets set due_at = $2 where id = $1", tid, clock.now() - timedelta(hours=2)
        )
    finally:
        await conn.close()
    async with tenant_tx(oid) as tx:
        org = await tx.get(Org, oid)
        org.model_budget_monthly_minor = 1
        from command_inbox.agents.budget import month_of
        from command_inbox.db.models import ModelSpend

        tx.add(ModelSpend(org_id=oid, month=month_of(clock.now()), provider="openai", spent_minor=5, calls=1))
    await sweep(oid)
    async with tenant_tx(oid) as tx:
        keys = {
            a.key
            for a in (
                await tx.execute(select(Alert).where(Alert.org_id == oid, Alert.resolved_at.is_(None)))
            ).scalars()
        }
        org = await tx.get(Org, oid)
        org.model_budget_monthly_minor = None
    keys.discard(None)
    assert any(k.startswith("sla:late:") for k in keys)
    assert any(k.startswith("budget:") and k.endswith(":100") for k in keys)
    await sweep(oid)  # the budget cap is gone: its alert resolves
    async with tenant_tx(oid) as tx:
        still = [
            a.key
            for a in (
                await tx.execute(select(Alert).where(Alert.org_id == oid, Alert.resolved_at.is_(None)))
            ).scalars()
            if a.key and a.key.startswith("budget:")
        ]
    assert still == []


async def test_alerts_show_on_performance_with_a_link(admin):
    r = await admin.get("/v1/insights/performance")
    assert r.status_code == 200
    assert all("ref" in a for a in r.json()["alerts"])
