"""Performance, results, custom KPIs, alerts, the activity rail and the shift summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Literal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import Feed, audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import forbidden, not_found
from command_inbox.core.outbox import publish
from command_inbox.db.engine import rows
from command_inbox.db.models import (
    Alert,
    AuditEvent,
    DailyMetric,
    Department,
    Kpi,
    Notification,
    Org,
    QueryType,
    Ticket,
)
from command_inbox.modules.insights.jsfmt import grouped, js_fixed, js_round, js_str, num
from command_inbox.modules.people.service import list_staff
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import KpiBody

METRIC_LABEL: dict[str, str] = {
    "fr": "First reply time (min)",
    "tat": "Time to resolve (h)",
    "accept": "Drafts sent unedited (%)",
    "reopen": "Reopen rate (%)",
    "auto": "Handled end-to-end by AI (%)",
    "csat": "CSAT after close (of 5)",
    "cost": "Model spend per query",
    "awo": "Approved without opening the evidence (%)",
}
LOWER_IS_BETTER = {"fr", "tat", "reopen", "cost", "awo"}


async def series(tx: AsyncSession, org_id: str, metric: str) -> list[float]:
    found = await tx.execute(
        select(DailyMetric.value)
        .where(DailyMetric.org_id == org_id, DailyMetric.metric == metric)
        .order_by(DailyMetric.day)
    )
    return [float(v) for v in found.scalars().all()]


def _week_ago() -> datetime:
    return clock.now() - timedelta(days=7)


async def approve_without_open_pct(tx: AsyncSession, org_id: str) -> float:
    [awo] = await rows(
        tx,
        """select count(*)::int as n, count(*) filter (where not opened_evidence)::int as without
             from approvals where org_id = :org and created_at >= :since""",
        {"org": org_id, "since": _week_ago()},
    )
    if awo["n"] > 0:
        return js_round(awo["without"] / awo["n"] * 100)
    hist = await series(tx, org_id, "weekly.awo")
    return hist[-1] if hist else 0


async def _live_metrics(tx: AsyncSession, org_id: str) -> dict[str, float]:
    """Live values computed from tickets where there is enough data; otherwise the latest weekly point."""
    out: dict[str, float] = {}
    [drafts] = await rows(
        tx,
        """select count(*)::int as sent,
                  count(*) filter (where trim(current_body) = trim(original_body))::int as unedited
             from drafts where org_id = :org and state = 'sent' and sent_at >= :since""",
        {"org": org_id, "since": _week_ago()},
    )
    if drafts["sent"] >= 20:
        out["accept"] = js_round(drafts["unedited"] / drafts["sent"] * 100)
    out["awo"] = await approve_without_open_pct(tx, org_id)
    return out


def trend_digest(values: list[float], *, lower_better: bool) -> str:
    """A factual one-line reading of a weekly series: direction, size and span. Never a narrative."""
    points = len(values)
    if points == 0:
        return "No data yet: this fills in as mail is handled."
    if points == 1:
        return "One week of data so far."
    first, last = values[0], values[-1]
    if first == last:
        return f"Unchanged over {points} weeks."
    rising = sum(1 for a, b in pairwise(values) if b > a)
    falling = sum(1 for a, b in pairwise(values) if b < a)
    direction = "Down" if last < first else "Up"
    good = (last < first) == lower_better
    size = f"{abs((last - first) / first * 100):.0f}%" if first else f"{abs(last - first):g}"
    streak = ""
    run = 0
    for a, b in zip(reversed(values[:-1]), reversed(values[1:]), strict=False):
        if (b < a) == (last < first) and a != b:
            run += 1
        else:
            break
    if run >= 3:
        streak = f"; {run} weeks in a row"
    return (
        f"{direction} {size} over {points} weeks ({falling} weeks down, {rising} up){streak}"
        f" — {'better' if good else 'worse'}."
    )


def _tile(
    key: str,
    label: str,
    values: list[float],
    unit: str,
    digest: str,
    *,
    decimals: int | None = None,
    bad: bool = False,
    lower_better: bool = False,
) -> dto.KpiTileDTO:
    last = values[-1] if values else 0.0
    first = values[0] if values else last
    change = (last - first) / first * 100 if first else 0.0
    # Small percentages (reopen rate) read better as a change in points than as a relative change.
    if unit == "%" and abs(last) < 10:
        trend = f"{'+' if last - first >= 0 else '−'}{js_fixed(abs(last - first), 1)}pt"
    else:
        trend = f"{'+' if change >= 0 else '−'}{abs(js_round(change))}%"
    return dto.KpiTileDTO(
        key=key,
        label=label,
        value=js_fixed(last, decimals) if decimals is not None else js_str(last),
        unit=unit,
        trend_pct=trend,
        trend_good=last <= first if lower_better else last >= first,
        spark=[num(v) for v in values],
        digest=digest,
        tone="bad" if bad else "neutral",
    )


async def performance(tx: AsyncSession, ctx: Ctx) -> dto.PerformanceDTO:
    require(ctx, "insights.view", "view performance")
    org = ctx.org_id
    locale = (await tx.execute(select(Org.locale).where(Org.id == org))).scalar_one()
    [vol] = await rows(
        tx, "select coalesce(sum(volume_24h), 0)::int as n from mailboxes where org_id = :org", {"org": org}
    )
    weekly = {k: await series(tx, org, f"weekly.{k}") for k in ("fr", "tat", "missed", "reopen")}
    tiles = [
        _tile(
            "fr",
            "Time to first reply",
            weekly["fr"],
            "min typical",
            trend_digest(weekly["fr"], lower_better=True),
            lower_better=True,
        ),
        _tile(
            "tat",
            "Time to fully resolve",
            weekly["tat"],
            "hours",
            trend_digest(weekly["tat"], lower_better=True),
            decimals=1,
            lower_better=True,
        ),
        _tile(
            "missed",
            "Missed deadlines",
            weekly["missed"],
            f"of {grouped(vol['n'], locale)}",
            trend_digest(weekly["missed"], lower_better=True),
            bad=True,
            lower_better=True,
        ),
        _tile(
            "reopen",
            "Reopen rate",
            weekly["reopen"],
            "%",
            trend_digest(weekly["reopen"], lower_better=True),
            decimals=1,
            lower_better=True,
        ),
    ]

    qts = (
        await tx.execute(
            select(QueryType, Department.name)
            .outerjoin(Department, Department.id == QueryType.department_id)
            .where(QueryType.org_id == org, QueryType.show_on_speed.is_(True))
            .order_by(QueryType.sort)
        )
    ).all()
    query_types = [
        dto.QueryTypeSpeedDTO(
            id=q.id,
            name=q.name,
            department=dept or "Unowned",
            lane=q.default_lane,
            volume=q.monthly_volume,
            baseline_hours=num(q.baseline_hours or 0),
            actual_hours=num(q.actual_hours or 0),
            late=q.late_count,
            owner=q.owner_label,
        )
        for q, dept in qts
    ]

    alerts = (
        (
            await tx.execute(
                select(Alert)
                .where(Alert.org_id == org, Alert.resolved_at.is_(None))
                .order_by(Alert.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    kpis = (
        (
            await tx.execute(
                select(Kpi)
                .where(Kpi.org_id == org, (Kpi.scope == "team") | (Kpi.owner_id == ctx.user.id))
                .order_by(Kpi.created_at)
            )
        )
        .scalars()
        .all()
    )
    live = await _live_metrics(tx, org)
    metric_series: dict[str, list[float]] = {}
    for k in METRIC_LABEL:
        ser = await series(tx, org, f"weekly.{k}")
        metric_series[k] = ser if k not in live else [*ser[:-1], live[k]]

    def current(k: str) -> float:
        ser = metric_series.get(k) or []
        return ser[-1] if ser else 0

    custom = []
    for k in kpis:
        lower = k.metric in LOWER_IS_BETTER
        cur = current(k.metric)
        custom.append(
            dto.CustomKpiDTO(
                id=k.id,
                name=k.name,
                metric=k.metric,
                metric_label=METRIC_LABEL[k.metric],
                viz=k.viz,
                scope=k.scope,
                target=num(k.target),
                current=num(cur),
                lower_is_better=lower,
                on_track=cur <= k.target if lower else cur >= k.target,
                spark=[num(v) for v in metric_series.get(k.metric, [])[-7:]],
            )
        )

    return dto.PerformanceDTO(
        tiles=tiles,
        query_types=query_types,
        alerts=[
            dto.AlertDTO(
                id=a.id,
                sev_label=a.sev_label,
                sev_kind=a.sev_kind,
                bucket=a.bucket,
                text=a.text_,
                action_label=a.action_label,
                owner=a.owner,
                at=iso_ms(a.updated_at or a.created_at),
                ref=a.ref,
            )
            for a in alerts
        ],
        staff=await list_staff(tx, ctx),
        custom_kpis=custom,
        metrics=[
            dto.PerformanceDTOMetrics(key=k, label=label, current=num(current(k)))
            for k, label in METRIC_LABEL.items()
        ],
        approve_without_open_pct=num(await approve_without_open_pct(tx, org)),
    )


async def results(tx: AsyncSession, ctx: Ctx) -> dto.ResultsDTO:
    require(ctx, "insights.view", "view results")
    baseline = await series(tx, ctx.org_id, "exec.baseline")
    actual = await series(tx, ctx.org_id, "exec.actual")
    avg = sum(baseline) / len(baseline) if baseline else 0.0
    baseline_hours = js_round(avg * 10) / 10
    now_hours = actual[-1] if actual else 0.0
    # Coverage is derived from the taxonomy: each query type's monthly volume by its handling lane.
    cov = await rows(
        tx,
        """select default_lane as lane, sum(monthly_volume)::int as volume from query_types
            where org_id = :org and show_on_map group by default_lane""",
        {"org": ctx.org_id},
    )
    by_lane = {r["lane"]: r["volume"] for r in cov}
    total = sum(by_lane.values()) or 1
    return dto.ResultsDTO(
        baseline_hours=num(baseline_hours),
        now_hours=num(now_hours),
        delta_pct=js_round((now_hours - baseline_hours) / baseline_hours * 100) if baseline_hours else 0,
        capacity_multiple=num(js_round(baseline_hours / now_hours * 10) / 10) if now_hours else 0,
        days=[
            dto.ResultsDTODays(
                label=f"d{i + 1}", baseline=num(b), actual=num(actual[i] if i < len(actual) else 0)
            )
            for i, b in enumerate(baseline)
        ],
        coverage=[
            dto.ResultsDTOCoverage(
                lane=lane, pct=js_round(by_lane.get(lane, 0) / total * 100), volume=by_lane.get(lane, 0)
            )
            for lane in ("auto", "draft", "manual")
        ],
        pools=[
            dto.ResultsDTOPools(
                label="Capacity released",
                metric=f"{js_fixed(baseline_hours / now_hours, 1) if now_hours else '—'}× per FTE",
                note="Same headcount, more queries closed per person per day at the current coverage mix.",
            ),
        ],
    )


async def create_kpi(tx: AsyncSession, ctx: Ctx, body: KpiBody) -> None:
    require(ctx, "kpi.manage", "create a KPI")
    tx.add(
        Kpi(
            org_id=ctx.org_id,
            owner_id=ctx.user.id,
            name=body.name,
            metric=body.metric,
            viz=body.viz,
            scope=body.scope,
            target=body.target,
        )
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="kpi.created",
        entity="kpi",
        summary=f'KPI "{body.name}" created on the {"team" if body.scope == "team" else "personal"} dashboard',
    )
    await publish(tx, ctx.org_id, "insights.updated", {"area": "kpis"})


async def delete_kpi(tx: AsyncSession, ctx: Ctx, kpi_id: str) -> None:
    k = (await tx.execute(select(Kpi).where(Kpi.org_id == ctx.org_id, Kpi.id == kpi_id))).scalar_one_or_none()
    if k is None:
        raise not_found("KPI")
    if k.owner_id != ctx.user.id and not ctx.can("kpi.manage"):
        raise forbidden("Only the owner or a team lead can remove this KPI.")
    await tx.execute(delete(Kpi).where(Kpi.org_id == ctx.org_id, Kpi.id == kpi_id))
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="kpi.deleted",
        entity="kpi",
        entity_id=kpi_id,
        summary=f'KPI "{k.name}" removed',
    )
    await publish(tx, ctx.org_id, "insights.updated", {"area": "kpis"})


async def act_on_alert(tx: AsyncSession, ctx: Ctx, alert_id: str, mode: Literal["act", "notify"]) -> str:
    require(ctx, "insights.view", "act on alerts")
    a = (
        await tx.execute(select(Alert).where(Alert.org_id == ctx.org_id, Alert.id == alert_id))
    ).scalar_one_or_none()
    if a is None:
        raise not_found("Alert")
    act = mode == "act"
    if act:
        await tx.execute(
            update(Alert)
            .where(Alert.org_id == ctx.org_id, Alert.id == alert_id)
            .values(resolved_at=clock.now(), resolved_by=ctx.user.id)
        )
    tx.add(
        Notification(
            org_id=ctx.org_id,
            kind="message",
            source=f"{ctx.user.name} · alert",
            title=f"{a.action_label} — {a.bucket}" if act else f"Heads-up for {a.owner}: {a.bucket}",
            body=a.text_,
            urgent=a.sev_kind == "late",
            created_by=ctx.user.id,
            created_at=clock.now(),
        )
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="alert.actioned" if act else "alert.notified",
        entity="alert",
        entity_id=alert_id,
        summary=f"{a.action_label} — {a.bucket} alert cleared"
        if act
        else f"{a.owner} notified about {a.bucket.lower()}",
        feed=Feed("info", "performance"),
    )
    await publish(tx, ctx.org_id, "activity.created", {})
    await publish(tx, ctx.org_id, "notification.created", {})
    if act:
        return f"{a.action_label} — done. {a.bucket} alert cleared."
    return f"{a.owner} notified about {a.bucket.lower()}."


async def activity(tx: AsyncSession, ctx: Ctx, limit: int = 20) -> list[dto.ActivityDTO]:
    found = (
        await tx.execute(
            select(AuditEvent, Ticket.number)
            .outerjoin(Ticket, Ticket.id == AuditEvent.ticket_id)
            .where(AuditEvent.org_id == ctx.org_id, AuditEvent.feed_tone.is_not(None))
            .order_by(AuditEvent.seq.desc())
            .limit(limit)
        )
    ).all()
    return [
        dto.ActivityDTO(
            id=e.id,
            text=e.summary,
            meta=e.feed_meta or "",
            tone=e.feed_tone,
            at=iso_ms(e.at),
            ticket_number=f"QRY-{number}" if number else None,
        )
        for e, number in found
    ]


async def shift(tx: AsyncSession, ctx: Ctx) -> dto.ShiftDTO:
    now = clock.now()
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    [r] = await rows(
        tx,
        """
        select
          (select count(*) from tickets where org_id = :org and resolved_at >= :start)::int as closed,
          (select count(*) from tickets where org_id = :org and resolved_at >= :start
                                          and owner_kind = 'ai')::int as ai,
          (select count(*) from drafts where org_id = :org and state = 'sent' and sent_at >= :start)::int
            as drafts,
          (select count(*) from tickets where org_id = :org
              and status not in ('resolved','closed','waiting_customer') and due_at < :now)::int as missed""",
        {"org": ctx.org_id, "start": start, "now": now},
    )
    accept = await series(tx, ctx.org_id, "weekly.accept")
    live = await _live_metrics(tx, ctx.org_id)
    # Work closed on other channels today and the time-and-motion baseline arrive with the daily import.
    imported = (
        await tx.execute(
            select(DailyMetric.metric, DailyMetric.value).where(
                DailyMetric.org_id == ctx.org_id,
                DailyMetric.day == now.date(),
                DailyMetric.metric.like("shift.%"),
            )
        )
    ).all()
    imp = {m: v for m, v in imported}
    sent_pct = live.get("accept", accept[-1] if accept else 0)
    return dto.ShiftDTO(
        closed_today=num(imp.get("shift.closed_imported", 0) + r["closed"]),
        sent_as_drafted_pct=num(sent_pct),
        # 12 min per AI-resolved ticket, 6 min per drafted reply (time-and-motion baseline).
        saved_minutes=num(imp.get("shift.saved_imported", 0) + r["ai"] * 12 + r["drafts"] * 6),
        missed_deadlines=r["missed"],
    )
