"""System alerts: raised by the per-minute sweep, one open alert per condition, resolved when it clears.

Conditions watched per workspace:
- mail past its deadline, and mail due within the last fifth of its target, grouped by owning team;
- a connected mailbox degraded or down (its health model, §7.5);
- model spend at 80% and 100% of the monthly budget;
- approved knowledge expiring within 14 days.

A new alert lands in the in-app notifications; the serious ones (a mailbox down, the budget spent, mail
newly past its deadline) are also emailed to the workspace's admins. While a condition holds, its alert's
text is kept current; when it clears, the alert is resolved by the system (no person is recorded).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.budget import month_of, spent_this_month
from command_inbox.core.clock import clock
from command_inbox.core.email import queue_email
from command_inbox.core.jobs import JobRow
from command_inbox.core.outbox import publish
from command_inbox.db.engine import rows, tenant_tx
from command_inbox.db.models import Alert, Mailbox, Membership, Notification, Org, User

log = structlog.get_logger(__name__)
SWEPT_PREFIXES = ("sla:", "mailbox:", "budget:", "knowledge:")


@dataclass(frozen=True, slots=True)
class Condition:
    key: str
    kind: str  # late | capacity | health | budget | knowledge
    sev_label: str
    bucket: str
    text: str
    action_label: str
    owner: str
    ref: str | None
    email: bool = False
    urgent: bool = False


async def _admins(tx: AsyncSession, org_id: str) -> list[tuple[str, str]]:
    return [
        (u.email, u.name)
        for u in (
            await tx.execute(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.org_id == org_id, Membership.role == "admin")
            )
        ).scalars()
    ]


async def apply(tx: AsyncSession, org_id: str, conditions: list[Condition]) -> dict[str, int]:
    """Raise new alerts, refresh open ones, resolve swept alerts whose condition no longer holds."""
    now = clock.now()
    open_alerts = {
        a.key: a
        for a in (
            await tx.execute(
                select(Alert).where(
                    Alert.org_id == org_id, Alert.resolved_at.is_(None), Alert.key.is_not(None)
                )
            )
        ).scalars()
    }
    raised = refreshed = resolved = 0
    active = {c.key for c in conditions}
    for c in conditions:
        a = open_alerts.get(c.key)
        if a is not None:
            if a.text_ != c.text or a.sev_label != c.sev_label:
                a.text_, a.sev_label, a.updated_at = c.text, c.sev_label, now
                refreshed += 1
            continue
        tx.add(
            Alert(
                org_id=org_id,
                key=c.key,
                kind=c.kind,
                sev_label=c.sev_label,
                sev_kind=c.kind,
                bucket=c.bucket,
                text_=c.text,
                action_label=c.action_label,
                owner=c.owner,
                ref=c.ref,
                created_at=now,
                updated_at=now,
            )
        )
        tx.add(
            Notification(
                org_id=org_id,
                kind="alert",
                source="Monitoring",
                title=f"{c.sev_label} — {c.bucket}",
                body=c.text,
                urgent=c.urgent,
                created_at=now,
            )
        )
        raised += 1
        if c.email:
            for email, _name in await _admins(tx, org_id):
                await queue_email(
                    tx,
                    tenant_id=org_id,
                    template="alert",
                    to=email,
                    subject=f"[Command Inbox] {c.sev_label}: {c.bucket}",
                    text=f"{c.text}\n\n{c.action_label}.\n",
                )
    for key, a in open_alerts.items():
        if key.startswith(SWEPT_PREFIXES) and key not in active:
            a.resolved_at, a.updated_at = now, now
            resolved += 1
    await tx.flush()
    if raised or resolved:
        await publish(tx, org_id, "notification.created", {})
    return {"raised": raised, "refreshed": refreshed, "resolved": resolved}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def conditions(tx: AsyncSession, org_id: str) -> list[Condition]:
    now = clock.now()
    out: list[Condition] = []

    # Deadlines, grouped by owning team (unowned mail under "No team").
    for r in await rows(
        tx,
        """select coalesce(d.name, 'No team') as team, coalesce(d.id::text, 'none') as team_id,
                  count(*) filter (where t.due_at < :now) as late,
                  count(*) filter (where t.due_at >= :now
                                     and t.due_at < cast(:now as timestamptz) + make_interval(mins => t.sla_minutes / 5))
                    as soon
             from tickets t left join departments d on d.id = t.department_id
            where t.org_id = :org and t.status not in ('resolved', 'closed', 'waiting_customer')
              and t.merged_into_id is null and t.paused_at is null and t.due_at is not null
            group by 1, 2""",
        {"org": org_id, "now": now},
    ):
        if r["late"]:
            out.append(
                Condition(
                    key=f"sla:late:{r['team_id']}",
                    kind="late",
                    sev_label="Past deadline",
                    bucket=r["team"],
                    text=f"{_plural(r['late'], 'ticket')} in {r['team']} past the reply deadline.",
                    action_label="Reassign or reply now",
                    owner=r["team"],
                    ref="/tickets?due=risk",
                    email=True,
                    urgent=True,
                )
            )
        if r["soon"]:
            out.append(
                Condition(
                    key=f"sla:soon:{r['team_id']}",
                    kind="capacity",
                    sev_label="Due soon",
                    bucket=r["team"],
                    text=f"{_plural(r['soon'], 'ticket')} in {r['team']} will miss the deadline without action.",
                    action_label="Auto-assign or take them",
                    owner=r["team"],
                    ref="/tickets?due=risk",
                )
            )

    # Mailbox health.
    from command_inbox.modules.mailboxes.service import _connection_dto

    for mb in (
        await tx.execute(
            select(Mailbox).where(
                Mailbox.org_id == org_id, Mailbox.connection.not_in(("not_connected", "disconnected"))
            )
        )
    ).scalars():
        c = await _connection_dto(tx, org_id, mb)
        if c.level in ("degraded", "down"):
            worst = [s for s in c.signals if s.level == c.level]
            detail = "; ".join(f"{s.label}: {s.value}" for s in worst) or (c.last_error or "")
            out.append(
                Condition(
                    key=f"mailbox:{mb.id}",
                    kind="health",
                    sev_label="Mailbox down" if c.level == "down" else "Mailbox degraded",
                    bucket=mb.address,
                    text=f"{mb.address} is {c.level}. {detail}".strip(),
                    action_label="Open Where mail arrives",
                    owner="Admins",
                    ref="/setup/mailboxes",
                    email=c.level == "down",
                    urgent=c.level == "down",
                )
            )

    # Model budget.
    cap = (await tx.execute(select(Org.model_budget_monthly_minor).where(Org.id == org_id))).scalar_one()
    if cap:
        spent = await spent_this_month(tx, org_id, now)
        month = month_of(now).isoformat()
        for threshold in (100, 80):
            if spent * 100 >= cap * threshold:
                out.append(
                    Condition(
                        key=f"budget:{month}:{threshold}",
                        kind="budget",
                        sev_label="Budget spent" if threshold == 100 else "Budget 80% spent",
                        bucket="Model spend",
                        text=(
                            "The monthly model budget is spent: drafting has stopped and new mail goes to people."
                            if threshold == 100
                            else "80% of the monthly model budget is spent."
                        ),
                        action_label="Review the model policy",
                        owner="Admins",
                        ref="/admin/organisation",
                        email=threshold == 100,
                        urgent=threshold == 100,
                    )
                )
                break

    # Knowledge about to expire.
    [k] = await rows(
        tx,
        """select count(*) as n from knowledge_docs where org_id = :org and status = 'approved'
              and expires_at is not null and expires_at < :soon""",
        {"org": org_id, "soon": now + timedelta(days=14)},
    )
    if k["n"]:
        out.append(
            Condition(
                key="knowledge:expiring",
                kind="knowledge",
                sev_label="Knowledge expiring",
                bucket="Knowledge",
                text=f"{_plural(k['n'], 'approved document')} expire within 14 days; once expired they are no "
                "longer cited.",
                action_label="Upload new versions",
                owner="Knowledge managers",
                ref="/setup/knowledge",
            )
        )
    return out


async def sweep(org_id: str) -> dict[str, int]:
    async with tenant_tx(org_id) as tx:
        result = await apply(tx, org_id, await conditions(tx, org_id))
    if result["raised"] or result["resolved"]:
        log.info("alerts swept", org_id=org_id, **result)
    return result


async def run_sla_sweep(job: JobRow) -> None:
    await sweep(job.org_id)


async def resolve(tx: AsyncSession, org_id: str, alert_id: str, user_id: str) -> None:
    await tx.execute(
        update(Alert)
        .where(Alert.org_id == org_id, Alert.id == alert_id)
        .values(resolved_at=clock.now(), resolved_by=user_id)
    )
