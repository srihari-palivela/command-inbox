"""The metrics rollup computes the performance numbers from records, and the scheduler enqueues it once."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from command_inbox.core.clock import clock
from command_inbox.db.engine import rows, tenant_tx
from command_inbox.db.models import DailyMetric, QueryType
from command_inbox.modules.insights.rollup import rollup
from tests.integration.admin_support import admin_conn, org_id

pytestmark = pytest.mark.integration


async def test_rollup_matches_the_records(app):
    oid = await org_id("apex")
    now = clock.now()
    async with tenant_tx(oid) as tx:
        written = await rollup(tx, oid, now)
        assert written > 0
        tz = (await rows(tx, "select time_zone from orgs where id = :o", {"o": oid}))[0]["time_zone"]
        [expected] = await rows(
            tx,
            """select date_trunc('week', received_at at time zone :tz)::date as wk,
                      percentile_cont(0.5) within group (order by extract(epoch from first_reply_at - received_at) / 60)
                        filter (where first_reply_at is not null) as fr,
                      count(*) as n
                 from tickets where org_id = :o and merged_into_id is null and status <> 'triaging'
                  and received_at >= :since
                group by 1 order by 1 desc limit 1""",
            {"o": oid, "tz": tz, "since": now - timedelta(weeks=12)},
        )
        point = (
            await tx.execute(
                select(DailyMetric.value).where(
                    DailyMetric.org_id == oid,
                    DailyMetric.metric == "weekly.fr",
                    DailyMetric.day == expected["wk"],
                )
            )
        ).scalar_one_or_none()
        if expected["fr"] is not None:
            assert point == pytest.approx(round(expected["fr"], 2))
        qts = (await tx.execute(select(QueryType).where(QueryType.org_id == oid))).scalars().all()
        [counted] = await rows(
            tx,
            """select count(*) as n from tickets where org_id = :o and received_at >= :m
                  and merged_into_id is null and status <> 'triaging' and query_type_id is not null""",
            {"o": oid, "m": now - timedelta(days=30)},
        )
        assert sum(q.monthly_volume for q in qts) == counted["n"]


async def test_the_performance_digest_is_computed_not_written(admin):
    r = await admin.get("/v1/insights/performance")
    assert r.status_code == 200, r.text
    for tile in r.json()["tiles"]:
        assert tile["digest"].startswith(("Down", "Up", "Unchanged", "One week", "No data")), tile["digest"]


async def test_the_scheduler_enqueues_each_recurring_job_once_per_window(app):
    from command_inbox.worker import schedule_tick

    now = clock.now()
    await schedule_tick(now, {})
    await schedule_tick(now, {})
    oid = await org_id("meridian")
    conn = await admin_conn()
    try:
        n = await conn.fetchval(
            "select count(*) from jobs where org_id = $1 and kind = 'metrics_rollup' and dedupe_key = $2",
            uuid.UUID(oid),
            f"metrics_rollup:{int(now.timestamp()) // 3600}",
        )
    finally:
        await conn.close()
    assert n == 1
