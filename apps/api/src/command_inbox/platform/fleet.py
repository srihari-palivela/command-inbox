"""Fleet health for operators (plan §11.2): the job queue, and each tenant's mail, AI and spend at a glance.

Read across tenants with the global (RLS-bypassing, operator-only) connection, and only aggregates: no
customer content, no people. Alerting on the same signals runs in Prometheus (infra/prometheus/rules.yml).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from command_inbox.core.clock import clock, iso_ms
from command_inbox.db.engine import global_tx, rows
from command_inbox.db.models import Org
from command_inbox.platform.rbac import OperatorCtx, require_platform
from command_inbox.platform.tenants import _limits
from command_inbox.schemas import platform_dto as pdto


async def fleet(op: OperatorCtx) -> pdto.FleetDTO:
    require_platform(op, "tenants.view", "view fleet health")
    now = clock.now()
    day = now - timedelta(hours=24)
    p = {"now": now, "day": day, "soon": now + timedelta(hours=24), "stale": now - timedelta(minutes=15)}
    async with global_tx() as g:
        queue = await rows(
            g,
            """select kind,
                      count(*) filter (where state = 'queued' and run_at <= :now) as due,
                      count(*) filter (where state = 'running') as running,
                      count(*) filter (where state = 'failed' and created_at >= :day) as failed,
                      extract(epoch from :now - min(run_at) filter (where state = 'queued' and run_at <= :now))
                        as oldest
                 from jobs group by kind order by kind""",
            p,
        )
        orgs = list((await g.execute(select(Org).order_by(Org.created_at, Org.slug))).scalars())
        mail = {
            r["org_id"]: r
            for r in await rows(
                g,
                """select org_id, count(*) filter (where connection not in ('not_connected', 'disconnected')) as n,
                          count(*) filter (where connection = 'reauth_required'
                             or (connection not in ('not_connected', 'disconnected')
                                 and coalesce(last_sync_at, created_at) < :stale)) as bad,
                          count(*) filter (where stream_expires_at is not null and stream_expires_at < :soon) as exp,
                          max(last_message_at) as last
                     from mailboxes group by org_id""",
                p,
            )
        }
        runs = {
            r["org_id"]: r
            for r in await rows(
                g,
                """select org_id, count(*) as n,
                          avg(case when provider = 'degraded' then 1.0 else 0.0 end) as degraded
                     from triage_runs where created_at >= :day group by org_id""",
                p,
            )
        }
        alerts = {
            r["org_id"]: r["n"]
            for r in await rows(
                g, "select org_id, count(*) as n from alerts where resolved_at is null group by org_id", p
            )
        }
        spend = {
            r["org_id"]: int(r["n"])
            for r in await rows(
                g,
                """select org_id, sum(spent_minor) as n from model_spend
                    where month = date_trunc('month', cast(:now as timestamptz) at time zone 'UTC')::date
                    group by org_id""",
                p,
            )
        }
    return pdto.FleetDTO(
        generated_at=iso_ms(now),
        queue=[
            pdto.FleetQueueDTO(
                kind=q["kind"],
                due=q["due"],
                running=q["running"],
                failed24h=q["failed"],
                oldest_due_seconds=int(q["oldest"]) if q["oldest"] is not None else None,
            )
            for q in queue
        ],
        tenants=[
            pdto.FleetTenantDTO(
                id=o.id,
                name=o.name,
                slug=o.slug,
                status=o.status,  # type: ignore[arg-type]
                currency=o.currency,
                locale=o.locale,
                mailboxes=int(mail[o.id]["n"]) if o.id in mail else 0,
                mailboxes_unhealthy=int(mail[o.id]["bad"]) if o.id in mail else 0,
                streams_expiring=int(mail[o.id]["exp"]) if o.id in mail else 0,
                last_mail_at=iso_ms(mail[o.id]["last"]) if o.id in mail and mail[o.id]["last"] else None,
                triaged24h=int(runs[o.id]["n"]) if o.id in runs else 0,
                degraded_rate24h=round(float(runs[o.id]["degraded"]), 3) if o.id in runs else None,
                open_alerts=int(alerts.get(o.id, 0)),
                spend_month_minor=spend.get(o.id, 0),
                spend_cap_minor=_limits(o.limits or {}).model_spend_cap_minor,
                siem_failing=bool(o.siem_url and o.siem_last_error),
            )
            for o in orgs
        ],
    )
