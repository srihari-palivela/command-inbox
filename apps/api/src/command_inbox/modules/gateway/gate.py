"""The approval gateway's read model: what the person looking at a ticket may do right now, and why not.

The same checks run again inside every command (`service.py`), so the UI can never grant more than the
server. `compute_gate` is what the ticket detail builder calls to fill `TicketDetailDTO.gate`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx
from command_inbox.db.models import ActionInstance, ActionTemplate, Draft, Ticket, User
from command_inbox.domain.transitions import is_open
from command_inbox.rbac.clearance import CLEARANCE, CLEARANCE_LABEL, clearance_of
from command_inbox.schemas import dto


@dataclass(slots=True)
class CurrentAction:
    a: ActionInstance
    t: ActionTemplate


async def current_action(
    tx: AsyncSession, org_id: str, ticket_id: str, lock: bool = False
) -> CurrentAction | None:
    """The newest action instance on a ticket, with its template. `lock` row-locks the action only."""
    q = (
        select(ActionInstance, ActionTemplate)
        .join(ActionTemplate, ActionTemplate.id == ActionInstance.template_id)
        .where(ActionInstance.org_id == org_id, ActionInstance.ticket_id == ticket_id)
        .order_by(ActionInstance.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if lock:
        q = q.with_for_update(of=ActionInstance)
    row = (await tx.execute(q)).first()
    return CurrentAction(row[0], row[1]) if row else None


async def current_draft(tx: AsyncSession, org_id: str, ticket_id: str, lock: bool = False) -> Draft | None:
    q = (
        select(Draft)
        .where(Draft.org_id == org_id, Draft.ticket_id == ticket_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        q = q.with_for_update()
    return (await tx.execute(q)).scalars().first()


async def user_ref(tx: AsyncSession, user_id: str | None) -> dto.UserRef | None:
    if not user_id:
        return None
    u = (await tx.execute(select(User.id, User.name, User.initials).where(User.id == user_id))).first()
    return dto.UserRef(id=str(u.id), name=u.name, initials=u.initials) if u else None


async def propose_checker(
    tx: AsyncSession, org_id: str, department_id: str | None, exclude_user_id: str | None
) -> dto.UserRef | None:
    """Least-loaded available checker: lead/admin, approve clearance in the department, not the maker."""
    if not department_id:
        return None
    r = (
        await tx.execute(
            text("""
    select u.id, u.name, u.initials,
           (m.base_load + (select count(*) from tickets t where t.org_id = :org and t.assignee_id = u.id
                             and t.status not in ('resolved','closed')))::float / greatest(m.capacity, 1) as load
      from memberships m
      join users u on u.id = m.user_id
      join clearances c on c.org_id = m.org_id and c.user_id = m.user_id
                       and c.department_id = cast(:dept as uuid) and c.level >= 3
      left join staff_availability a on a.org_id = m.org_id and a.user_id = m.user_id
     where m.org_id = :org and m.role in ('lead','admin')
       and (cast(:ex as uuid) is null or u.id <> cast(:ex as uuid))
       and coalesce(a.status, 'available') <> 'away'
     order by load asc, u.name asc
     limit 1"""),
            {"org": org_id, "dept": department_id, "ex": exclude_user_id},
        )
    ).first()
    return dto.UserRef(id=str(r.id), name=r.name, initials=r.initials) if r else None


async def duplicate_check(tx: AsyncSession, a: ActionInstance) -> tuple[bool, str]:
    """ "No similar action on this account in 90 days" — computed before commitment, not after."""
    since = clock.now() - timedelta(days=90)
    n = (
        await tx.execute(
            select(func.count())
            .select_from(ActionInstance)
            .where(
                ActionInstance.org_id == a.org_id,
                ActionInstance.template_id == a.template_id,
                ActionInstance.account_ref == a.account_ref,
                ActionInstance.id != a.id,
                ActionInstance.state.in_(("executed", "scheduled", "executing")),
                ActionInstance.created_at >= since,
            )
        )
    ).scalar_one()
    if n == 0:
        return True, "No similar action on this account in 90 days"
    return (
        False,
        f"{n} similar action{'s' if n > 1 else ''} on this account in the last 90 days — check for a duplicate",
    )


def approvals_needed(chain: str) -> int:
    return 2 if chain == "dual" else 0 if chain == "auto" else 1


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


async def compute_gate(
    tx: AsyncSession, ctx: Ctx, t: Ticket, action: CurrentAction | None, draft: Draft | None
) -> dto.GateDTO:
    now = clock.now()
    open_ = is_open(t.status)
    waiting = max(0, _js_round((now - t.received_at).total_seconds() / 60)) if open_ else None
    clearance = await clearance_of(tx, ctx.org_id, ctx.user.id, t.department_id)

    def need_clearance(minimum: int, doing: str) -> str | None:
        if clearance >= minimum:
            return None
        return f'Needs "{CLEARANCE_LABEL[minimum]}" clearance for {doing} — you have "{CLEARANCE_LABEL[clearance]}".'

    g = dto.GateDTO(
        mode="manual",
        chain=None,
        state="open",
        maker=None,
        checker=None,
        proposed_checker=None,
        owner=await user_ref(tx, t.assignee_id),
        customer_waiting_minutes=waiting,
        reversible=None,
        money_moves=None,
        duplicate_clear=None,
        can_approve=False,
        blocked_reason=None,
        can_undo=False,
        undo_until=None,
        note=None,
    )

    if t.lane == "auto" and action is not None:
        a, tpl = action.a, action.t
        g.mode = "action"
        g.chain = a.chain  # type: ignore[assignment]
        g.reversible = tpl.reversible
        g.money_moves = tpl.money_moves
        g.maker = await user_ref(tx, a.maker_id)
        g.checker = await user_ref(tx, a.checker_id)
        g.note = a.failure
        g.duplicate_clear, _ = await duplicate_check(tx, a)
        if a.state in ("drafted", "failed"):
            g.state = "open"
            g.proposed_checker = (
                await propose_checker(tx, ctx.org_id, t.department_id, ctx.user.id)
                if a.chain == "dual"
                else None
            )
            if not ctx.can("action.approve_maker"):
                g.blocked_reason = "Your role cannot approve actions."
            else:
                g.blocked_reason = need_clearance(CLEARANCE["resolve"], "this team")
            if not open_:
                g.blocked_reason = "This ticket is closed."
        elif a.state == "awaiting_checker":
            g.state = "awaiting_checker"
            g.proposed_checker = await propose_checker(tx, ctx.org_id, t.department_id, a.maker_id)
            if a.maker_id == ctx.user.id:
                g.blocked_reason = "You approved as maker — a different person must check it."
            elif not ctx.can("action.approve_checker"):
                g.blocked_reason = "Only a team lead or admin can approve as checker."
            else:
                g.blocked_reason = need_clearance(CLEARANCE["approve"], "checking this team’s actions")
        elif a.state == "scheduled":
            g.state = "scheduled"
            g.undo_until = iso_ms(a.execute_after) if a.execute_after else None
            g.can_undo = bool(
                tpl.reversible
                and a.execute_after is not None
                and a.execute_after > now
                and (
                    a.maker_id == ctx.user.id
                    or a.checker_id == ctx.user.id
                    or ctx.can("action.approve_checker")
                )
            )
            g.blocked_reason = "Already approved."
        elif a.state == "executing":
            g.state = "executing"
            g.blocked_reason = "Executing in the core system."
        elif a.state == "executed":
            g.state = "done"
            g.blocked_reason = "Already executed."
        elif a.state in ("rejected", "cancelled"):
            g.state = "rejected"
            g.note = a.rejected_note
            g.blocked_reason = "Sent back."
        g.can_approve = g.state in ("open", "awaiting_checker") and g.blocked_reason is None
        return g

    if t.lane == "draft" and draft is not None:
        g.mode = "draft"
        g.chain = "single_undo"
        g.reversible = True
        g.money_moves = False
        if draft.state == "draft":
            g.state = "open"
            if not ctx.can("ticket.reply"):
                g.blocked_reason = "Your role cannot send replies."
            else:
                g.blocked_reason = need_clearance(CLEARANCE["resolve"], "replying for this team")
            if not draft.current_body.strip():
                g.blocked_reason = "The draft is empty."
            if not open_:
                g.blocked_reason = "This ticket is closed."
        elif draft.state == "scheduled":
            g.state = "scheduled"
            g.maker = await user_ref(tx, draft.sent_by)
            g.undo_until = iso_ms(draft.send_after) if draft.send_after else None
            g.can_undo = bool(
                draft.send_after is not None
                and draft.send_after > now
                and (draft.sent_by == ctx.user.id or ctx.can("ticket.assign"))
            )
            g.blocked_reason = "Sending."
        elif draft.state == "sent":
            g.state = "done"
            g.maker = await user_ref(tx, draft.sent_by)
            g.blocked_reason = "Already sent."
        else:
            g.state = "rejected"
            g.blocked_reason = "Draft discarded."
        g.can_approve = g.state == "open" and g.blocked_reason is None
        return g

    # Manual lane: the AI has stepped back; the move is to take ownership.
    g.state = "taken" if t.accepted_at else "open"
    if t.lane != "manual":
        g.note = (
            "No filled action to approve yet — work it by hand or wait for the agent."
            if t.lane == "auto"
            else "No draft to approve yet — reply by hand or wait for the agent."
        )
    if not open_:
        g.state = "done"
    if g.state == "open":
        if not ctx.can("ticket.work"):
            g.blocked_reason = "Your role cannot work tickets."
        else:
            g.blocked_reason = need_clearance(CLEARANCE["resolve"], "this team")
        g.can_approve = g.blocked_reason is None
    return g


async def gate_for(tx: AsyncSession, ctx: Ctx, t: Ticket) -> dto.GateDTO:
    """Convenience: load the current action and draft, then compute the gate."""
    return await compute_gate(
        tx, ctx, t, await current_action(tx, ctx.org_id, t.id), await current_draft(tx, ctx.org_id, t.id)
    )
