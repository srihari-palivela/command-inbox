"""The monitoring dashboard for bank admins and leads (plan §11.1): every number read from records.

The pipeline funnel, reply-time performance against the workspace's targets, what happened to drafts,
per-version and per-agent quality (fallbacks, escalations, latency, cost), model spend against the cap,
knowledge health and mailbox health. Counts carry a link to the list of records behind them.
"""

from __future__ import annotations

import difflib
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.budget import spent_this_month
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx
from command_inbox.db.engine import rows
from command_inbox.db.models import Draft, Mailbox, Org
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto


def _count(key: str, label: str, n: int, href: str | None) -> dto.MonitoringCountDTO:
    return dto.MonitoringCountDTO(key=key, label=label, count=int(n or 0), href=href)


def _r(v: object, digits: int = 2) -> float | None:
    return None if v is None else round(float(v), digits)  # type: ignore[arg-type]


def edit_distance(original: str, final: str) -> float:
    """0 when sent as drafted, 1 when rewritten entirely (1 − difflib's similarity ratio)."""
    if original == final:
        return 0.0
    return round(1 - difflib.SequenceMatcher(None, original, final, autojunk=False).ratio(), 4)


async def monitoring(tx: AsyncSession, ctx: Ctx, days: int = 7) -> dto.MonitoringDTO:
    require(ctx, "insights.view", "view monitoring")
    org = ctx.org_id
    now = clock.now()
    since = now - timedelta(days=days)
    p = {"org": org, "since": since, "now": now}

    [f] = await rows(
        tx,
        """select count(*) as received,
                   count(*) filter (where status <> 'triaging') as triaged,
                   count(*) filter (where original_lane = 'draft') as draft,
                   count(*) filter (where original_lane = 'manual') as manual,
                   count(*) filter (where original_lane = 'auto') as auto,
                   count(*) filter (where status in ('resolved', 'closed')) as resolved,
                   percentile_cont(0.5) within group (order by extract(epoch from first_reply_at - received_at) / 60)
                     filter (where first_reply_at is not null) as fr,
                   percentile_cont(0.5) within group (order by extract(epoch from resolved_at - received_at) / 3600)
                     filter (where resolved_at is not null) as tat
              from tickets where org_id = :org and received_at >= :since and merged_into_id is null""",
        p,
    )
    [d] = await rows(
        tx,
        """select count(*) filter (where state = 'sent' and sent_at >= :since) as sent,
                  count(*) filter (where state = 'sent' and sent_at >= :since
                                     and trim(current_body) = trim(original_body)) as unedited,
                  count(*) filter (where state = 'discarded' and updated_at >= :since) as discarded
             from drafts where org_id = :org""",
        p,
    )
    [sla] = await rows(
        tx,
        """select count(*) filter (where status not in ('resolved', 'closed') and due_at < :now) as late_open,
                   count(*) filter (where status not in ('resolved', 'closed') and due_at >= :now
                                      and due_at < cast(:now as timestamptz) + make_interval(mins => sla_minutes / 2)) as at_risk
              from tickets where org_id = :org and merged_into_id is null""",
        p,
    )
    by_priority = await rows(
        tx,
        """select priority, count(*) as total,
                   count(*) filter (where due_at is not null and ((resolved_at is not null and resolved_at > due_at)
                                      or (status not in ('resolved', 'closed') and due_at < :now))) as breached
              from tickets where org_id = :org and received_at >= :since and merged_into_id is null
             group by priority order by priority""",
        p,
    )
    reasons = await rows(
        tx,
        """select data->>'reason' as reason, count(*) as n from audit_events
            where org_id = :org and action = 'gate.rejected' and at >= :since group by 1 order by 2 desc""",
        p,
    )
    sent = (
        await tx.execute(
            select(Draft.original_body, Draft.current_body)
            .where(Draft.org_id == org, Draft.state == "sent", Draft.sent_at >= since)
            .order_by(Draft.sent_at.desc())
            .limit(500)
        )
    ).all()
    distances = [edit_distance(a.strip(), b.strip()) for a, b in sent]

    versions = await rows(
        tx,
        """select coalesce(dep.name, 'Setup (no deployment)') as deployment, v.version,
                  count(*) as mails,
                  avg(case when r.provider = 'degraded' then 1.0 else 0.0 end) as degraded,
                  avg(case when exists (select 1 from trace_spans s where s.run_id = r.id and s.org_id = r.org_id
                                          and s.action like 'Reviewed an uncertain%') then 1.0 else 0.0 end)
                    as escalation,
                  avg(r.cost_minor) as cost
             from triage_runs r
             join tickets t on t.id = r.ticket_id and t.org_id = r.org_id
             left join deployment_versions v on v.id = t.deployment_version_id
             left join deployments dep on dep.id = v.deployment_id
            where r.org_id = :org and r.created_at >= :since
            group by 1, 2 order by 3 desc""",
        p,
    )
    nodes = await rows(
        tx,
        """select s.agent, s.model, count(*) as calls,
                  percentile_cont(0.5) within group (order by s.latency_ms) as p50,
                  percentile_cont(0.95) within group (order by s.latency_ms) as p95,
                  coalesce(sum(s.cost_minor), 0) as cost,
                  count(*) filter (where s.status = 'flag') as flagged
             from trace_spans s join triage_runs r on r.id = s.run_id and r.org_id = s.org_id
            where s.org_id = :org and r.created_at >= :since
            group by 1, 2 order by 3 desc limit 30""",
        p,
    )
    [k] = await rows(
        tx,
        """select count(*) filter (where status = 'approved') as approved,
                  count(*) filter (where status = 'pending') as pending,
                  count(*) filter (where status = 'stale') as stale,
                  count(*) filter (where status = 'approved' and expires_at is not null
                                     and expires_at < cast(:now as timestamptz) + interval '14 days') as expiring
             from knowledge_docs where org_id = :org""",
        p,
    )
    [gaps] = await rows(
        tx, "select count(*) as n from gap_tickets where org_id = :org and closed_at is null", p
    )
    cited = await rows(
        tx,
        """select c->>'docId' as doc_id, max(c->>'doc') as title, count(*) as n
             from drafts, jsonb_array_elements(citations) c
            where org_id = :org and created_at >= :since and c ? 'docId'
            group by 1 order by 3 desc limit 5""",
        p,
    )
    [alerts] = await rows(
        tx, "select count(*) as n from alerts where org_id = :org and resolved_at is null", p
    )
    org_row = (await tx.execute(select(Org).where(Org.id == org))).scalar_one()

    from command_inbox.modules.mailboxes.service import _connection_dto

    boxes = [
        (mb, await _connection_dto(tx, org, mb))
        for mb in (
            await tx.execute(
                select(Mailbox).where(
                    Mailbox.org_id == org, Mailbox.connection.not_in(("not_connected", "disconnected"))
                )
            )
        ).scalars()
    ]

    return dto.MonitoringDTO(
        days=days,
        since=iso_ms(since),
        funnel=[
            _count("received", "Received", f["received"], "/tickets"),
            _count("triaged", "Triaged", f["triaged"], "/tickets"),
            _count("draft", "Drafted for approval", f["draft"], "/tickets?lane=draft"),
            _count("manual", "Handed to a person", f["manual"], "/tickets?lane=manual"),
            _count("sent", "Replies sent from drafts", d["sent"], None),
            _count("resolved", "Resolved", f["resolved"], "/tickets?status=resolved"),
        ],
        sla=dto.MonitoringDTOSla(
            first_reply_median_min=_r(f["fr"], 1),
            resolve_median_hours=_r(f["tat"], 1),
            breached=_count("breached", "Open past their deadline", sla["late_open"], "/tickets?due=risk"),
            at_risk=_count("at_risk", "Due within half their target", sla["at_risk"], "/tickets?due=risk"),
            by_priority=[
                dto.MonitoringDTOSlaByPriority(
                    priority=r["priority"], total=r["total"], breached=r["breached"]
                )
                for r in by_priority
            ],
        ),
        drafts=dto.MonitoringDTODrafts(
            sent=d["sent"],
            unedited=d["unedited"],
            edited=d["sent"] - d["unedited"],
            discarded=d["discarded"],
            mean_edit_distance=_r(sum(distances) / len(distances), 3) if distances else None,
            reject_reasons=[
                dto.MonitoringDTODraftsRejectReasons(reason=r["reason"] or "unspecified", count=r["n"])
                for r in reasons
            ],
        ),
        versions=[
            dto.VersionQualityDTO(
                deployment=r["deployment"],
                version=r["version"],
                mails=r["mails"],
                degraded_rate=_r(r["degraded"], 3),
                escalation_rate=_r(r["escalation"], 3),
                cost_per_mail_minor=_r(r["cost"], 1),
            )
            for r in versions
        ],
        nodes=[
            dto.NodeQualityDTO(
                agent=r["agent"],
                model=r["model"],
                calls=r["calls"],
                p50_ms=_r(r["p50"], 0),
                p95_ms=_r(r["p95"], 0),
                cost_minor=int(r["cost"]),
                flagged=r["flagged"],
            )
            for r in nodes
        ],
        spend=dto.MonitoringDTOSpend(
            month_minor=await spent_this_month(tx, org, now), cap_minor=org_row.model_budget_monthly_minor
        ),
        knowledge=dto.MonitoringDTOKnowledge(
            approved=k["approved"],
            pending=k["pending"],
            stale=k["stale"],
            expiring_soon=k["expiring"],
            open_gaps=gaps["n"],
            most_cited=[
                dto.MonitoringDTOKnowledgeMostCited(
                    doc_id=r["doc_id"], title=r["title"] or "", citations=r["n"]
                )
                for r in cited
            ],
        ),
        mailboxes=[
            dto.MonitoringDTOMailboxes(
                id=b.id,
                address=b.address,
                level=b.level,
                lag_seconds=mb.lag_seconds,
                messages24h=b.messages24h,
            )
            for mb, b in boxes
        ],
        open_alerts=alerts["n"],
    )
