"""Skills and clearance matrix, clearance edits, and auto-assignment of at-risk work."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import Feed, audit
from command_inbox.core.clock import clock
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import not_found
from command_inbox.core.outbox import publish
from command_inbox.db.engine import rows
from command_inbox.db.models import Clearance, Comment, Department, Membership, Ticket, User
from command_inbox.domain.sla import SlaInput, at_risk, compute_sla
from command_inbox.modules.people.routing import candidates, load_ratio
from command_inbox.rbac.clearance import CLEARANCE_LABEL
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto


async def list_staff(tx: AsyncSession, ctx: Ctx) -> list[dto.StaffDTO]:
    found = await rows(
        tx,
        """
        select u.id::text as id, u.name, u.initials, m.role, m.title, m.pod, m.years, m.capacity,
               (m.base_load + (select count(*) from tickets t where t.org_id = :org and t.assignee_id = u.id
                                 and t.status not in ('resolved','closed')))::int as open,
               a.status, a.checkin, a.calendar
          from memberships m
          join users u on u.id = m.user_id
          left join staff_availability a on a.org_id = m.org_id and a.user_id = u.id
         where m.org_id = :org
         order by case m.role when 'staff' then 0 when 'lead' then 1 else 2 end, m.joined_at""",
        {"org": ctx.org_id},
    )
    clear = (await tx.execute(select(Clearance).where(Clearance.org_id == ctx.org_id))).scalars().all()
    return [
        dto.StaffDTO(
            id=r["id"],
            name=r["name"],
            initials=r["initials"],
            role=r["role"],
            title=r["title"],
            pod=r["pod"],
            years=r["years"],
            open=r["open"],
            capacity=r["capacity"],
            availability=r["status"] or "available",
            checkin=r["checkin"] or "",
            calendar=r["calendar"] or "",
            clearances={c.department_id: c.level for c in clear if c.user_id == r["id"]},
            is_me=r["id"] == ctx.user.id,
        )
        for r in found
    ]


async def people(tx: AsyncSession, ctx: Ctx) -> dto.PeopleDTO:
    # Everyone on the team can see who is cleared for what (read-only); only leads and admins edit it.
    require(ctx, "ticket.work", "view skills and clearance")
    departments = (
        await tx.execute(
            select(Department.id, Department.name)
            .where(Department.org_id == ctx.org_id, Department.in_matrix.is_(True))
            .order_by(Department.sort)
        )
    ).all()
    return dto.PeopleDTO(
        staff=await list_staff(tx, ctx),
        departments=[dto.PeopleDTODepartments(id=d.id, name=d.name) for d in departments],
        editable=ctx.can("people.edit_clearance"),
    )


async def set_clearance(tx: AsyncSession, ctx: Ctx, user_id: str, department_id: str, level: int) -> None:
    require(ctx, "people.edit_clearance", "change someone’s clearance")
    # The person must belong to this workspace: a user id from another org is "not found", never written.
    name = (
        await tx.execute(
            select(User.name)
            .join(Membership, (Membership.user_id == User.id) & (Membership.org_id == ctx.org_id))
            .where(User.id == user_id)
        )
    ).scalar_one_or_none()
    dept = (
        await tx.execute(
            select(Department).where(Department.org_id == ctx.org_id, Department.id == department_id)
        )
    ).scalar_one_or_none()
    if name is None or dept is None:
        raise not_found("Person or team")
    await tx.execute(
        insert(Clearance)
        .values(org_id=ctx.org_id, user_id=user_id, department_id=department_id, level=level)
        .on_conflict_do_update(
            index_elements=[Clearance.org_id, Clearance.user_id, Clearance.department_id],
            set_={"level": level},
        )
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="clearance.changed",
        entity="clearance",
        entity_id=f"{user_id}:{department_id}",
        summary=f"{name} · {dept.name} → {CLEARANCE_LABEL[level]}",
        data={"level": level},
    )
    await publish(tx, ctx.org_id, "people.updated", {})


# ── Ticket side effects of auto-assign (kept local: the tickets module owns the general versions) ───────
async def _system_note(tx: AsyncSession, org_id: str, ticket_id: str, body: str) -> None:
    tx.add(
        Comment(
            org_id=org_id,
            ticket_id=ticket_id,
            kind="system",
            author_name="Command Inbox",
            author_initials="AI",
            body=body,
            created_at=clock.now(),
        )
    )
    await tx.flush()


async def _assign(tx: AsyncSession, t: Ticket, user_id: str) -> None:
    await tx.execute(
        update(Ticket)
        .where(Ticket.org_id == t.org_id, Ticket.id == t.id)
        .values(assignee_id=user_id, owner_kind="user", version=Ticket.version + 1, updated_at=clock.now())
    )


async def auto_assign(tx: AsyncSession, ctx: Ctx) -> dto.AutoAssignResultDTO:
    """Open P1/P2 or at-risk tickets owned by the AI or nobody go to the least-loaded available person
    cleared to resolve for that team. Every move writes its reason."""
    require(ctx, "people.auto_assign", "run auto-assignment")
    now = clock.now()
    open_rows = (
        (
            await tx.execute(
                select(Ticket)
                .where(
                    Ticket.org_id == ctx.org_id,
                    Ticket.status.not_in(("resolved", "closed", "waiting_customer")),
                    Ticket.owner_kind.in_(("ai", "unassigned")),
                )
                .order_by(Ticket.due_at.asc().nulls_last())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )

    def sla(t: Ticket) -> dto.SlaDTO:
        return compute_sla(SlaInput(t.status, t.due_at, t.sla_minutes, t.paused_at), now)

    risky = [t for t in open_rows if t.priority in ("P1", "P2") or at_risk(sla(t).tone)]
    lead = (
        await tx.execute(
            select(User.name)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == ctx.org_id, Membership.role == "lead")
            .order_by(Membership.joined_at)
            .limit(1)
        )
    ).scalar_one_or_none() or "the team lead"
    loads: dict[str, int] = {}
    moves: list[dto.AutoAssignResultDTOMoves] = []

    for t in risky:
        number = f"QRY-{t.number}"
        minutes_left = sla(t).minutes_left
        if not t.department_id:
            moves.append(
                dto.AutoAssignResultDTOMoves(
                    ticket_number=number,
                    priority=t.priority,
                    minutes_left=minutes_left,
                    to=None,
                    reason=f"no team owns {t.bucket.lower()} — escalated to {lead}",
                )
            )
            await _system_note(
                tx, ctx.org_id, t.id, f"Auto-assign: no team owns this query type — escalated to {lead}."
            )
            continue
        pool = [
            (c, c.open + loads.get(c.id, 0))
            for c in await candidates(tx, ctx.org_id, t.department_id)
            if c.availability == "available"
        ]
        pool.sort(key=lambda p: load_ratio(p[1], p[0].capacity))
        if not pool:
            moves.append(
                dto.AutoAssignResultDTOMoves(
                    ticket_number=number,
                    priority=t.priority,
                    minutes_left=minutes_left,
                    to=None,
                    reason=f"no available staff cleared for {t.bucket.lower()} — escalated to {lead}",
                )
            )
            continue
        pick, load = pool[0]
        loads[pick.id] = loads.get(pick.id, 0) + 1
        reason = f"available, cleared for {pick.department}, lightest load ({load}/{pick.capacity})"
        await _assign(tx, t, pick.id)
        await _system_note(tx, ctx.org_id, t.id, f"Auto-assigned to {pick.name} — {reason}.")
        await audit(
            tx,
            ctx.org_id,
            actor=actor_of(ctx),
            action="ticket.auto_assigned",
            entity="ticket",
            entity_id=t.id,
            ticket_id=t.id,
            summary=f"{number} → {pick.name}",
            data={"reason": reason},
        )
        await publish(tx, ctx.org_id, "ticket.updated", {"ticketId": t.id, "number": number})
        moves.append(
            dto.AutoAssignResultDTOMoves(
                ticket_number=number,
                priority=t.priority,
                minutes_left=minutes_left,
                to=pick.name,
                reason=reason,
            )
        )

    n = len(risky)
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="auto_assign.ran",
        entity="queue",
        summary=f"Auto-assign checked {n} at-risk ticket{'' if n == 1 else 's'}",
        feed=Feed("info", "queue · auto-assign"),
    )
    await publish(tx, ctx.org_id, "activity.created", {})
    return dto.AutoAssignResultDTO(checked=n, moves=moves)
