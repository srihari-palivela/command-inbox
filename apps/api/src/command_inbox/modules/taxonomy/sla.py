"""Reply-time targets: a workspace's own SLA policies, else the built-in defaults.

A new target applies to tickets triaged from then on; tickets already in the queue keep the deadline they
were given (changing a promise retroactively would silently make work late).
"""

from __future__ import annotations

from sqlalchemy import delete, distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import unprocessable
from command_inbox.db.models import SlaPolicy, Ticket
from command_inbox.domain.sla import DEFAULT_SLA_RULES, SlaRule
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import SlaPoliciesBody


async def load_sla_rules(tx: AsyncSession, org_id: str) -> tuple[SlaRule, ...]:
    rows = (
        await tx.execute(select(SlaPolicy).where(SlaPolicy.org_id == org_id).order_by(SlaPolicy.sort))
    ).scalars()
    return tuple(SlaRule(r.minutes, r.priority, r.segment, r.escalation, r.name) for r in rows)


async def get_policies(tx: AsyncSession, ctx: Ctx) -> dto.SlaPoliciesDTO:
    require(ctx, "setup.view", "view reply-time targets")
    rows = list(
        (
            await tx.execute(select(SlaPolicy).where(SlaPolicy.org_id == ctx.org_id).order_by(SlaPolicy.sort))
        ).scalars()
    )
    segments = sorted(
        s
        for s in (
            await tx.execute(select(distinct(Ticket.segment)).where(Ticket.org_id == ctx.org_id))
        ).scalars()
        if s
    )
    policies = (
        [
            dto.SlaPolicyDTO(
                id=r.id,
                name=r.name,
                priority=r.priority,  # type: ignore[arg-type]
                segment=r.segment,
                escalation=r.escalation,
                minutes=r.minutes,
            )
            for r in rows
        ]
        if rows
        else [
            dto.SlaPolicyDTO(
                id=f"default-{i}",
                name=r.name,
                priority=r.priority,  # type: ignore[arg-type]
                segment=r.segment,
                escalation=r.escalation,
                minutes=r.minutes,
            )
            for i, r in enumerate(DEFAULT_SLA_RULES)
        ]
    )
    return dto.SlaPoliciesDTO(
        policies=policies,
        using_defaults=not rows,
        segments=sorted(set(segments) | {"Retail", "Corporate"}),
        can_edit=ctx.can("setup.edit"),
    )


async def replace_policies(tx: AsyncSession, ctx: Ctx, body: SlaPoliciesBody) -> dto.SlaPoliciesDTO:
    """Replace the whole table (small, ordered, edited as one); audited with before and after."""
    require(ctx, "setup.edit", "change reply-time targets")
    seen: set[tuple[str | None, str | None, bool]] = set()
    for p in body.policies:
        key = (p.priority, p.segment.lower() if p.segment else None, p.escalation)
        if key in seen:
            raise unprocessable(
                "duplicate_policy",
                "Two targets match the same mail.",
                f"{p.name!r} repeats a priority, segment and escalation combination.",
            )
        seen.add(key)
    if not any(p.priority is None and p.segment is None and not p.escalation for p in body.policies):
        raise unprocessable(
            "catch_all_required",
            "Add a target for everything else.",
            "One target must match any priority and any segment, so no mail is left without a deadline.",
        )
    before = [
        {
            "name": r.name,
            "priority": r.priority,
            "segment": r.segment,
            "escalation": r.escalation,
            "minutes": r.minutes,
        }
        for r in await load_sla_rules(tx, ctx.org_id)
    ]
    await tx.execute(delete(SlaPolicy).where(SlaPolicy.org_id == ctx.org_id))
    tx.add_all(
        [
            SlaPolicy(
                org_id=ctx.org_id,
                name=p.name,
                priority=p.priority,
                segment=p.segment,
                escalation=p.escalation,
                minutes=p.minutes,
                sort=i,
            )
            for i, p in enumerate(body.policies)
        ]
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="sla.policies_changed",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} set {len(body.policies)} reply-time targets (they apply to newly triaged mail)",
        data={"before": before, "after": [p.model_dump(mode="json", by_alias=True) for p in body.policies]},
    )
    return await get_policies(tx, ctx)
