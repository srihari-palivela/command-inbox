"""The metrics rollup: every number on the performance and results screens, computed from records.

Runs hourly per workspace (a `metrics_rollup` job). It recomputes the last 12 weeks, in the workspace's
time zone, from tickets, drafts, approvals and triage runs, and writes:

- `weekly.<metric>` points (one per week that has records): first reply (median minutes), time to resolve
  (median hours), missed deadlines (count), reopen rate, drafts sent unedited, handled end to end by AI,
  model spend per triaged mail and approvals without opening the evidence;
- `exec.actual` daily points (median hours to resolve) for the results screen, next to the imported
  pre-AI baseline;
- each query type's last-30-day volume, median hours to resolve and late count;
- each mailbox's inbound volume over the last 24 hours (connected mailboxes; from received mail).

Weeks without records are not written, so a chart never shows an invented zero. CSAT has no source yet
(it arrives with the survey integration) and is left as imported.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.clock import clock
from command_inbox.core.jobs import JobRow
from command_inbox.db.engine import rows, tenant_tx
from command_inbox.db.models import DailyMetric, Org

log = structlog.get_logger(__name__)
WEEKS = 12

_WEEKLY_TICKETS = """
with t as (
  select date_trunc('week', received_at at time zone :tz)::date as wk, *
    from tickets
   where org_id = :org and received_at >= :since and merged_into_id is null and status <> 'triaging'
)
select wk,
       percentile_cont(0.5) within group (order by extract(epoch from first_reply_at - received_at) / 60)
         filter (where first_reply_at is not null) as fr,
       percentile_cont(0.5) within group (order by extract(epoch from resolved_at - received_at) / 3600)
         filter (where resolved_at is not null) as tat,
       count(*) filter (where due_at is not null and ((resolved_at is not null and resolved_at > due_at)
                                                     or (resolved_at is null and due_at < :now))) as missed,
       100.0 * count(*) filter (where reopen_count > 0 and resolved_at is not null)
             / nullif(count(*) filter (where resolved_at is not null), 0) as reopen,
       100.0 * count(*) filter (where original_lane = 'auto' and resolved_at is not null)
             / nullif(count(*), 0) as auto
  from t group by wk
"""

_WEEKLY_DRAFTS = """
select date_trunc('week', sent_at at time zone :tz)::date as wk,
       100.0 * count(*) filter (where trim(current_body) = trim(original_body)) / count(*) as accept
  from drafts where org_id = :org and state = 'sent' and sent_at >= :since group by wk
"""

_WEEKLY_COST = """
select date_trunc('week', created_at at time zone :tz)::date as wk, avg(cost_minor) as cost
  from triage_runs where org_id = :org and created_at >= :since group by wk
"""

_WEEKLY_AWO = """
select date_trunc('week', created_at at time zone :tz)::date as wk,
       100.0 * count(*) filter (where not opened_evidence) / count(*) as awo
  from approvals where org_id = :org and created_at >= :since group by wk
"""

_DAILY_RESOLVE = """
select (resolved_at at time zone :tz)::date as day,
       percentile_cont(0.5) within group (order by extract(epoch from resolved_at - received_at) / 3600) as h
  from tickets where org_id = :org and resolved_at >= :since and merged_into_id is null group by day
"""

_QUERY_TYPES = """
update query_types q set
  monthly_volume = coalesce(s.n, 0),
  actual_hours = s.h,
  late_count = coalesce(s.late, 0)
  from query_types q2
  left join (
    select query_type_id,
           count(*) as n,
           percentile_cont(0.5) within group (order by extract(epoch from resolved_at - received_at) / 3600)
             filter (where resolved_at is not null) as h,
           count(*) filter (where due_at is not null and ((resolved_at is not null and resolved_at > due_at)
                                                         or (resolved_at is null and due_at < :now))) as late
      from tickets
     where org_id = :org and received_at >= :month and merged_into_id is null and status <> 'triaging'
     group by query_type_id
  ) s on s.query_type_id = q2.id
 where q.id = q2.id and q.org_id = :org
"""

_MAILBOXES = """
update mailboxes m set volume_24h = coalesce(s.n, 0)
  from mailboxes m2
  left join (
    select mailbox_id, count(*) as n from mail_messages
     where org_id = :org and direction = 'inbound' and received_at >= :day group by mailbox_id
  ) s on s.mailbox_id = m2.id
 where m.id = m2.id and m.org_id = :org and m.connection is not null
"""


async def _upsert(tx: AsyncSession, org_id: str, metric: str, day: Any, value: float) -> None:
    await tx.execute(
        insert(DailyMetric)
        .values(org_id=org_id, metric=metric, day=day, value=round(float(value), 2))
        .on_conflict_do_update(
            index_elements=[DailyMetric.org_id, DailyMetric.metric, DailyMetric.day],
            set_={"value": round(float(value), 2)},
        )
    )


async def rollup(tx: AsyncSession, org_id: str, now: datetime | None = None) -> int:
    """Recompute this workspace's metrics; returns the number of points written."""
    now = now or clock.now()
    tz = (await tx.execute(select(Org.time_zone).where(Org.id == org_id))).scalar_one() or "UTC"
    since = now - timedelta(weeks=WEEKS)
    params = {"org": org_id, "tz": tz, "since": since, "now": now}
    written = 0
    for sql, keys in (
        (_WEEKLY_TICKETS, ("fr", "tat", "missed", "reopen", "auto")),
        (_WEEKLY_DRAFTS, ("accept",)),
        (_WEEKLY_COST, ("cost",)),
        (_WEEKLY_AWO, ("awo",)),
    ):
        for r in await rows(tx, sql, params):
            for k in keys:
                if r[k] is not None:
                    await _upsert(tx, org_id, f"weekly.{k}", r["wk"], r[k])
                    written += 1
    for r in await rows(tx, _DAILY_RESOLVE, {**params, "since": now - timedelta(days=14)}):
        if r["h"] is not None:
            await _upsert(tx, org_id, "exec.actual", r["day"], r["h"])
            written += 1
    await tx.execute(_text(_QUERY_TYPES), {"org": org_id, "now": now, "month": now - timedelta(days=30)})
    await tx.execute(_text(_MAILBOXES), {"org": org_id, "day": now - timedelta(hours=24)})
    return written


def _text(sql: str) -> Any:
    from sqlalchemy import text

    return text(sql)


async def run_rollup_job(job: JobRow) -> None:
    async with tenant_tx(job.org_id) as tx:
        n = await rollup(tx, job.org_id)
    log.info("metrics rollup", org_id=job.org_id, points=n)
