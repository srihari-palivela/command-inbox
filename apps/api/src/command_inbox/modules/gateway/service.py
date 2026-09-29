"""Approval gateway commands.

Every command runs in one tenant transaction with the ticket and its action/draft row-locked, re-checks
capabilities and per-department clearance, and writes audit + outbox events in the same transaction.
Execution and sending happen in idempotent jobs (`jobs.py`) after an undo/recall window.

Separation of duties (enforced here, again in the execution job, and in the RBAC policy):
- The **maker** needs `action.approve_maker` and "Can resolve" clearance for the ticket's department.
- The **checker** needs `action.approve_checker` (never grantable to staff) and "Can approve" clearance,
  and must be a different person from the maker (`maker_checker`).
- Undo inside the window is for the maker, the checker, or anyone holding `action.approve_checker`.
- A queued draft can be recalled by its sender or anyone holding `ticket.assign` (TS showed this in the
  gate but did not enforce it in the command; fixed here).

Recall window for replies (identical to TS): a reply is queued with `send_after = now + 60 s`; only its
author may recall it, only while it is still `scheduled` and `send_after > now`; otherwise 409
`window_closed` / `not_undoable`, and 403 for anyone else.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core import outbox
from command_inbox.core.audit import Feed
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.crypto import canonical_json, sha256
from command_inbox.core.errors import conflict, forbidden, unprocessable
from command_inbox.core.jobs import cancel_job, enqueue
from command_inbox.db.models import (
    ActionInstance,
    ActionTemplate,
    Agent,
    Approval,
    Department,
    Draft,
    Feedback,
    PredictionOutcome,
    ProposedRule,
    Reply,
    Ticket,
)
from command_inbox.domain.risk import CHECKER_ESCALATION_MIN, RECALL_WINDOW_SEC, UNDO_WINDOW_SEC, cell_of
from command_inbox.domain.transitions import is_open
from command_inbox.modules.gateway.gate import current_action, current_draft
from command_inbox.modules.gateway.schemas import ApproveResult, ReplyScheduled
from command_inbox.modules.pilot.service import require_sends_allowed
from command_inbox.modules.tickets.ops import (
    lock_ticket,
    record_ticket_event,
    set_subtask,
    system_note,
    update_ticket,
)
from command_inbox.rbac.clearance import CLEARANCE, require_clearance
from command_inbox.rbac.policy import require
from command_inbox.schemas.requests import ActionField

RejectReason = Literal["wrong_type", "bad_field", "tone", "needs_human"]

REJECT_NOTES: dict[str, str] = {
    "wrong_type": "Correction stored: this pattern should not be filed as that query type. Reassigned to you.",
    "bad_field": "Correction stored against the extraction rule. Reassigned to you.",
    "tone": "Correction stored as a tone example for this customer segment. Reassigned to you.",
    "needs_human": (
        "Proposed a hard stop rule for this pattern — it applies once an Admin approves it. Reassigned to you."
    ),
}


def _num(t: Ticket) -> str:
    return f"QRY-{t.number}"


def exec_key(action_id: str, at: datetime) -> str:
    """Dedupe key of one scheduled execution (epoch milliseconds, same as TS)."""
    return f"exec:{action_id}:{int(at.timestamp() * 1000)}"


def send_key(draft_id: str, at: datetime) -> str:
    return f"send:{draft_id}:{int(at.timestamp() * 1000)}"


async def bucketer_for(tx: AsyncSession, t: Ticket) -> str:
    if not t.department_id:
        return "Retail Bucketer"
    name = (await tx.execute(select(Department.name).where(Department.id == t.department_id))).scalar()
    return "Trade Bucketer" if name == "Trade & Payments" else "Retail Bucketer"


async def store_feedback(
    tx: AsyncSession,
    ctx: Ctx | None,
    t: Ticket,
    agent_name: str,
    kind: Literal["override", "edit", "rejection"],
    text: str,
    fix: Literal["prompt", "context"],
    diff: dict[str, str] | None = None,
) -> None:
    agent_id = (
        await tx.execute(select(Agent.id).where(Agent.org_id == t.org_id, Agent.name == agent_name))
    ).scalar()
    tx.add(
        Feedback(
            org_id=t.org_id,
            agent_id=agent_id,
            agent_name=agent_name,
            ticket_id=t.id,
            ticket_number=_num(t),
            kind=kind,
            text_=text,
            fix=fix,
            diff=diff,
            created_by=ctx.user.id if ctx else None,
            created_at=clock.now(),
        )
    )
    tx.add(
        PredictionOutcome(
            org_id=t.org_id, agent_name=agent_name, ticket_id=t.id, confidence=t.confidence, correct=False
        )
    )
    await tx.flush()


async def _record_approval(
    tx: AsyncSession,
    ctx: Ctx,
    t: Ticket,
    kind: Literal["action", "draft", "reply"],
    subject_id: str,
    step: Literal["maker", "checker", "sender"],
    opened_evidence: bool,
) -> None:
    tx.add(
        Approval(
            org_id=t.org_id,
            ticket_id=t.id,
            subject_kind=kind,
            subject_id=subject_id,
            user_id=ctx.user.id,
            step=step,
            opened_evidence=opened_evidence,
        )
    )
    await tx.flush()


async def _schedule_execution(
    tx: AsyncSession, t: Ticket, a: ActionInstance, tpl: ActionTemplate
) -> datetime:
    """Queue execution after the undo window (reversible) or immediately (irreversible)."""
    now = clock.now()
    execute_after = now + timedelta(seconds=UNDO_WINDOW_SEC) if tpl.reversible else now
    await tx.execute(
        update(ActionInstance)
        .where(ActionInstance.id == a.id)
        .values(state="scheduled", execute_after=execute_after, failure=None)
        .execution_options(synchronize_session=False)
    )
    await enqueue(
        tx,
        t.org_id,
        "execute_action",
        {"actionId": a.id},
        run_at=execute_after,
        dedupe_key=exec_key(a.id, execute_after),
    )
    return execute_after


async def _set_action(tx: AsyncSession, action_id: str, **values: Any) -> None:
    await tx.execute(
        update(ActionInstance)
        .where(ActionInstance.id == action_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


async def _set_draft(tx: AsyncSession, draft_id: str, **values: Any) -> None:
    await tx.execute(
        update(Draft)
        .where(Draft.id == draft_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


async def approve(tx: AsyncSession, ctx: Ctx, ticket_id: str, opened_evidence: bool) -> ApproveResult:
    await require_sends_allowed(tx, ctx.org_id)
    t = await lock_ticket(tx, ctx.org_id, ticket_id)
    if not is_open(t.status):
        raise conflict("ticket_closed", "This ticket is already closed.")
    actor = actor_of(ctx)

    # ── Lane A: action ────────────────────────────────────────────────────
    if t.lane == "auto":
        cur = await current_action(tx, ctx.org_id, t.id, lock=True)
        if cur is None:
            raise unprocessable("no_action", "There is no filled action on this ticket.")
        a, tpl = cur.a, cur.t
        cell = cell_of(tpl.reversible, tpl.money_moves)

        if a.state in ("drafted", "failed"):
            require(ctx, "action.approve_maker", "approve actions")
            await require_clearance(tx, ctx, t.department_id, CLEARANCE["resolve"], "approve this action")
            await _set_action(tx, a.id, maker_id=ctx.user.id, maker_at=clock.now())
            await _record_approval(tx, ctx, t, "action", a.id, "maker", opened_evidence)
            await set_subtask(tx, t.org_id, t.id, "a3", True, ctx.user.id)

            if a.chain == "dual":
                await _set_action(tx, a.id, state="awaiting_checker")
                t = await update_ticket(
                    tx, t, {"status": "awaiting_approval", "next_move": "Waiting for the checker"}
                )
                await enqueue(
                    tx,
                    t.org_id,
                    "escalate_checker",
                    {"actionId": a.id},
                    run_at=clock.now() + timedelta(minutes=CHECKER_ESCALATION_MIN),
                    dedupe_key=f"esc:{a.id}",
                )
                await system_note(
                    tx,
                    t.org_id,
                    t.id,
                    f"{ctx.user.name} approved {tpl.code} as maker. Waiting for a checker with approve clearance.",
                )
                await record_ticket_event(
                    tx,
                    t,
                    actor=actor,
                    action="action.maker_approved",
                    summary=f"{ctx.user.name} approved {tpl.code} as maker on {_num(t)}",
                    data={"actionId": a.id, "cell": cell, "openedEvidence": opened_evidence},
                    feed=Feed("info", f"{_num(t)} · maker"),
                )
                await outbox.publish(tx, t.org_id, "gate.updated", {"ticketId": t.id})
                return ApproveResult(outcome="awaiting_checker")

            at = await _schedule_execution(tx, t, a, tpl)
            t = await update_ticket(
                tx,
                t,
                {
                    "status": "executing",
                    "next_move": "Executing after the undo window" if tpl.reversible else "Executing now",
                },
            )
            await record_ticket_event(
                tx,
                t,
                actor=actor,
                action="action.approved",
                summary=f"{ctx.user.name} approved {tpl.code} on {_num(t)}",
                data={
                    "actionId": a.id,
                    "cell": cell,
                    "executeAfter": iso_ms(at),
                    "openedEvidence": opened_evidence,
                },
            )
            return ApproveResult(outcome="scheduled")

        if a.state == "awaiting_checker":
            require(ctx, "action.approve_checker", "approve as checker")
            if a.maker_id == ctx.user.id:
                raise forbidden("The maker cannot also be the checker.", "maker_checker")
            await require_clearance(tx, ctx, t.department_id, CLEARANCE["approve"], "check this action")
            await _set_action(tx, a.id, checker_id=ctx.user.id, checker_at=clock.now())
            await _record_approval(tx, ctx, t, "action", a.id, "checker", opened_evidence)
            await set_subtask(tx, t.org_id, t.id, "a4", True, ctx.user.id)
            await cancel_job(tx, t.org_id, f"esc:{a.id}")
            at = await _schedule_execution(tx, t, a, tpl)
            t = await update_ticket(tx, t, {"status": "executing", "next_move": "Executing in core banking"})
            await record_ticket_event(
                tx,
                t,
                actor=actor,
                action="action.checker_approved",
                summary=f"{ctx.user.name} approved {tpl.code} as checker on {_num(t)}",
                data={
                    "actionId": a.id,
                    "cell": cell,
                    "executeAfter": iso_ms(at),
                    "openedEvidence": opened_evidence,
                },
                feed=Feed("info", f"{_num(t)} · checker"),
            )
            return ApproveResult(outcome="scheduled")
        raise conflict("not_pending", "This action is not waiting for an approval.")

    # ── Lane B: draft ─────────────────────────────────────────────────────
    if t.lane == "draft":
        d = await current_draft(tx, ctx.org_id, t.id, lock=True)
        if d is None or d.state != "draft":
            raise conflict("not_pending", "There is no draft waiting to be sent.")
        require(ctx, "ticket.reply", "send replies")
        await require_clearance(tx, ctx, t.department_id, CLEARANCE["resolve"], "reply for this team")
        if not d.current_body.strip():
            raise unprocessable("empty_draft", "The draft is empty.")
        send_after = clock.now() + timedelta(seconds=RECALL_WINDOW_SEC)
        await _set_draft(
            tx, d.id, state="scheduled", send_after=send_after, sent_by=ctx.user.id, updated_at=clock.now()
        )
        await _record_approval(tx, ctx, t, "draft", d.id, "sender", opened_evidence)
        await set_subtask(tx, t.org_id, t.id, "b3", True, ctx.user.id)
        await enqueue(
            tx,
            t.org_id,
            "send_draft",
            {"draftId": d.id},
            run_at=send_after,
            dedupe_key=send_key(d.id, send_after),
        )
        t = await update_ticket(tx, t, {"next_move": "Sending — recallable for 60s"})
        await record_ticket_event(
            tx,
            t,
            actor=actor,
            action="draft.approved",
            summary=f"{ctx.user.name} approved the reply on {_num(t)}",
            data={
                "draftId": d.id,
                "edited": d.current_body != d.original_body,
                "openedEvidence": opened_evidence,
            },
        )
        return ApproveResult(outcome="sending")

    # ── Lane C: take ownership ────────────────────────────────────────────
    require(ctx, "ticket.work", "work tickets")
    await require_clearance(tx, ctx, t.department_id, CLEARANCE["resolve"], "take this ticket")
    if t.accepted_at and t.assignee_id == ctx.user.id:
        raise conflict("already_taken", "You already own this ticket.")
    t = await update_ticket(
        tx,
        t,
        {
            "assignee_id": ctx.user.id,
            "owner_kind": "user",
            "accepted_at": clock.now(),
            "status": "with_human" if t.status in ("triaging", "awaiting_approval") else t.status,
            "next_move": "You own this — agree a dated next step",
        },
    )
    await system_note(
        tx,
        t.org_id,
        t.id,
        f"{ctx.user.name} took this on. The AI's summary and context stay attached to the thread.",
    )
    await record_ticket_event(
        tx, t, actor=actor, action="ticket.taken", summary=f"{ctx.user.name} took {_num(t)}"
    )
    return ApproveResult(outcome="taken")


async def reject(tx: AsyncSession, ctx: Ctx, ticket_id: str, reason: RejectReason) -> None:
    require(ctx, "ticket.work", "send work back")
    t = await lock_ticket(tx, ctx.org_id, ticket_id)
    if not is_open(t.status):
        raise conflict("ticket_closed", "This ticket is already closed.")
    note = REJECT_NOTES[reason]
    agent_name = {
        "bad_field": "Field Extractor",
        "tone": "Reply Drafter",
        "needs_human": "Guardrail Sentinel",
    }.get(reason) or await bucketer_for(tx, t)

    if t.lane == "auto":
        cur = await current_action(tx, ctx.org_id, t.id, lock=True)
        if cur is None or cur.a.state not in ("drafted", "awaiting_checker", "failed"):
            raise conflict("not_pending", "Nothing is waiting at the gate to send back.")
        await _set_action(tx, cur.a.id, state="rejected", rejected_note=note)
        await cancel_job(tx, t.org_id, f"esc:{cur.a.id}")
    elif t.lane == "draft":
        d = await current_draft(tx, ctx.org_id, t.id, lock=True)
        if d is None or d.state != "draft":
            raise conflict("not_pending", "Nothing is waiting at the gate to send back.")
        await _set_draft(tx, d.id, state="discarded", updated_at=clock.now())
    else:
        raise conflict("not_pending", "The AI has already stepped back on this ticket.")

    await store_feedback(
        tx,
        ctx,
        t,
        agent_name,
        "rejection",
        note,
        "prompt" if reason in ("tone", "needs_human") else "context",
    )
    if reason == "needs_human":
        tx.add(
            ProposedRule(
                org_id=t.org_id,
                text_=f"Always route to a person: queries like {_num(t)} (“{t.subject[:80]}”)",
                ticket_id=t.id,
                ticket_number=_num(t),
                proposed_by=ctx.user.id,
                proposed_by_name=ctx.user.name,
            )
        )
        await tx.flush()
    t = await update_ticket(
        tx,
        t,
        {
            "lane": "manual",
            "lane_note": f"Sent back by {ctx.user.name}",
            "status": "with_human",
            "assignee_id": ctx.user.id,
            "owner_kind": "user",
            "accepted_at": clock.now(),
            "next_move": "Handle it yourself — the AI stood down",
        },
    )
    await system_note(
        tx, t.org_id, t.id, f"{ctx.user.name} sent the AI's work back ({reason.replace('_', ' ', 1)}). {note}"
    )
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="gate.rejected",
        summary=f"{ctx.user.name} sent {_num(t)} back: {reason}",
        data={"reason": reason, "agent": agent_name},
        feed=Feed("muted", f"{_num(t)} · correction"),
    )


async def undo(tx: AsyncSession, ctx: Ctx, ticket_id: str) -> None:
    """Undo inside the window: nothing has reached the core system or the customer yet."""
    t = await lock_ticket(tx, ctx.org_id, ticket_id)
    now = clock.now()
    if t.lane == "auto":
        cur = await current_action(tx, ctx.org_id, t.id, lock=True)
        if cur is None or cur.a.state != "scheduled":
            raise conflict("not_undoable", "There is nothing to undo.")
        if not cur.t.reversible:
            raise conflict("irreversible", "This action cannot be undone.")
        execute_after = cur.a.execute_after
        if execute_after is None or execute_after <= now:
            raise conflict("window_closed", "The undo window has closed.")
        mine = ctx.user.id in (cur.a.maker_id, cur.a.checker_id)
        if not mine and not ctx.can("action.approve_checker"):
            raise forbidden("Only an approver or a team lead can undo.")
        await _set_action(
            tx,
            cur.a.id,
            state="drafted",
            maker_id=None,
            maker_at=None,
            checker_id=None,
            checker_at=None,
            execute_after=None,
        )
        # Cancel exactly this scheduled execution. (The job also re-checks state, so a race cannot execute it.)
        await cancel_job(tx, t.org_id, exec_key(cur.a.id, execute_after))
        await set_subtask(tx, t.org_id, t.id, "a3", False, None)
        await set_subtask(tx, t.org_id, t.id, "a4", False, None)
        t = await update_ticket(
            tx, t, {"status": "awaiting_approval", "next_move": "Back at the gate after undo"}
        )
        await record_ticket_event(
            tx,
            t,
            actor=actor_of(ctx),
            action="action.undone",
            summary=f"{ctx.user.name} undid {cur.t.code} inside the window",
        )
        return
    if t.lane == "draft":
        d = await current_draft(tx, ctx.org_id, t.id, lock=True)
        if d is None or d.state != "scheduled":
            raise conflict("not_undoable", "There is nothing to recall.")
        send_after = d.send_after
        if send_after is None or send_after <= now:
            raise conflict("window_closed", "The recall window has closed.")
        if d.sent_by != ctx.user.id and not ctx.can("ticket.assign"):
            raise forbidden("Only the sender or a team lead can recall this reply.")
        await _set_draft(tx, d.id, state="draft", send_after=None, updated_at=now)
        await cancel_job(tx, t.org_id, send_key(d.id, send_after))
        await set_subtask(tx, t.org_id, t.id, "b3", False, None)
        t = await update_ticket(tx, t, {"next_move": "Recalled — review & send draft"})
        await record_ticket_event(
            tx,
            t,
            actor=actor_of(ctx),
            action="draft.recalled",
            summary=f"{ctx.user.name} recalled the reply on {_num(t)}",
        )
        return
    raise conflict("not_undoable", "There is nothing to undo.")


async def edit_action_fields(tx: AsyncSession, ctx: Ctx, ticket_id: str, edits: list[ActionField]) -> None:
    require(ctx, "action.approve_maker", "amend action fields")
    t = await lock_ticket(tx, ctx.org_id, ticket_id)
    await require_clearance(tx, ctx, t.department_id, CLEARANCE["resolve"], "amend this action")
    cur = await current_action(tx, ctx.org_id, t.id, lock=True)
    if cur is None or cur.a.state != "drafted":
        raise conflict("not_editable", "Fields can only be amended before approval.")
    changed: list[str] = []
    fields: list[dict[str, Any]] = []
    for f in cur.a.fields or []:
        e = next((x for x in edits if x.label == f.get("label")), None)
        if e is None or e.value == f.get("value"):
            fields.append(f)
            continue
        changed.append(f"{f.get('label')}: {f.get('value')} → {e.value}")
        fields.append(
            {
                "label": f.get("label"),
                "value": e.value,
                "source": f"edited by {ctx.user.name}",
                "inferred": False,
            }
        )
    if not changed:
        return
    idempotency_key = sha256(
        canonical_json(
            {
                "orgId": t.org_id,
                "code": cur.t.code,
                "fields": [[f.get("label"), f.get("value")] for f in fields],
            }
        )
    )
    clash = (
        await tx.execute(
            select(ActionInstance.id).where(
                ActionInstance.org_id == t.org_id, ActionInstance.idempotency_key == idempotency_key
            )
        )
    ).scalar()
    if clash is not None and clash != cur.a.id:
        raise conflict("duplicate_action", "An identical action already exists — this would execute twice.")
    await _set_action(tx, cur.a.id, fields=fields, idempotency_key=idempotency_key)
    n = len(changed)
    await store_feedback(
        tx,
        ctx,
        t,
        "Field Extractor",
        "edit",
        f"Staff amended {n} field{'s' if n > 1 else ''}: {'; '.join(changed)}.",
        "context",
    )
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="action.fields_edited",
        summary=f"{ctx.user.name} amended {cur.t.code}",
        data={"changed": changed},
    )


async def edit_draft(tx: AsyncSession, ctx: Ctx, ticket_id: str, body: str) -> None:
    """Edit the AI draft before it is sent. Needs "Can resolve" clearance, like sending it (TS skipped this)."""
    require(ctx, "ticket.reply", "edit replies")
    t = await lock_ticket(tx, ctx.org_id, ticket_id)
    await require_clearance(tx, ctx, t.department_id, CLEARANCE["resolve"], "edit this reply")
    d = await current_draft(tx, ctx.org_id, t.id, lock=True)
    if d is None or d.state != "draft":
        raise conflict("not_editable", "The draft can only be edited before it is sent.")
    await _set_draft(tx, d.id, current_body=body, updated_at=clock.now())
    await outbox.publish(tx, t.org_id, "ticket.updated", {"ticketId": t.id})


async def reply(tx: AsyncSession, ctx: Ctx, ticket_id: str, body: str) -> ReplyScheduled:
    """A free-text reply from the composer: sent after the same 60 s recall window as drafts."""
    require(ctx, "ticket.reply", "send replies")
    await require_sends_allowed(tx, ctx.org_id)
    t = await lock_ticket(tx, ctx.org_id, ticket_id)
    await require_clearance(tx, ctx, t.department_id, CLEARANCE["resolve"], "reply for this team")
    send_after = clock.now() + timedelta(seconds=RECALL_WINDOW_SEC)
    r = Reply(
        org_id=t.org_id,
        ticket_id=t.id,
        body=body,
        state="scheduled",
        send_after=send_after,
        author_id=ctx.user.id,
    )
    tx.add(r)
    await tx.flush()
    await _record_approval(tx, ctx, t, "reply", r.id, "sender", True)
    await enqueue(
        tx, t.org_id, "send_reply", {"replyId": r.id}, run_at=send_after, dedupe_key=f"reply:{r.id}"
    )
    await record_ticket_event(
        tx, t, actor=actor_of(ctx), action="reply.scheduled", summary=f"{ctx.user.name} replied on {_num(t)}"
    )
    return ReplyScheduled(reply_id=r.id, send_after=iso_ms(send_after))


async def recall_reply(tx: AsyncSession, ctx: Ctx, reply_id: str) -> None:
    r = (
        await tx.execute(
            select(Reply)
            .where(Reply.org_id == ctx.org_id, Reply.id == reply_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if r is None or r.state != "scheduled":
        raise conflict("not_undoable", "There is nothing to recall.")
    if r.author_id != ctx.user.id:
        raise forbidden("Only the author can recall a reply.")
    if r.send_after <= clock.now():
        raise conflict("window_closed", "The recall window has closed.")
    await tx.execute(
        update(Reply)
        .where(Reply.id == r.id)
        .values(state="recalled")
        .execution_options(synchronize_session=False)
    )
    await cancel_job(tx, ctx.org_id, f"reply:{r.id}")
    await outbox.publish(tx, ctx.org_id, "ticket.updated", {"ticketId": r.ticket_id})
