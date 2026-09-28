"""Ticket use cases: status moves, assignment, lane override, notes, sub-tasks, watch, escalate, split, merge."""

from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core import jobs, outbox
from command_inbox.core.audit import Feed
from command_inbox.core.clock import clock
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import bad_request, conflict, forbidden, not_found
from command_inbox.db.models import (
    ActionInstance,
    Brief,
    Department,
    Draft,
    Message,
    Subtask,
    Ticket,
    TicketLink,
    User,
    Watcher,
)
from command_inbox.domain.transitions import can_transition
from command_inbox.modules.gateway.gate import current_action, current_draft
from command_inbox.modules.gateway.service import bucketer_for, store_feedback
from command_inbox.modules.people.routing import pick_assignee
from command_inbox.modules.tickets.ops import (
    add_comment,
    lock_ticket,
    next_number,
    record_ticket_event,
    set_subtask,
    system_note,
    update_ticket,
)
from command_inbox.modules.tickets.queries import find_ticket, load_ticket, ticket_number
from command_inbox.rbac.clearance import CLEARANCE, clearance_of, require_clearance
from command_inbox.rbac.policy import require

STATUS_LABEL = {
    "triaging": "Triaging",
    "awaiting_approval": "Waiting on approval",
    "executing": "In progress",
    "with_human": "With a human",
    "waiting_customer": "Waiting on customer",
    "resolved": "Resolved",
    "closed": "Closed",
}
LANE_NAME = {
    "auto": "Auto — the AI does it",
    "draft": "Draft — you send it",
    "manual": "You — the AI steps back",
}
LANE_AUTONOMY = {"manual": 0, "draft": 1, "auto": 2}
HIGHER_PRIORITY = {"P4": "P3", "P3": "P2", "P2": "P1", "P1": "P1"}


async def require_ticket(tx: AsyncSession, ctx: Ctx, id_or_number: str) -> Ticket:
    """Find (by id or number) and row-lock a ticket for the rest of the transaction."""
    t = await load_ticket(tx, ctx.org_id, id_or_number)
    return await lock_ticket(tx, ctx.org_id, t.id)


async def transition(
    tx: AsyncSession, ctx: Ctx, id_: str, to: str, expect_version: int | None = None
) -> None:
    require(ctx, "ticket.work", "change ticket status")
    t = await require_ticket(tx, ctx, id_)
    frm = t.status
    if not can_transition(frm, to):
        raise conflict(
            "illegal_transition",
            f'A ticket cannot move from "{STATUS_LABEL[frm]}" to "{STATUS_LABEL[to]}" directly.',
        )
    await require_clearance(tx, ctx, t.department_id, CLEARANCE["resolve"], "change this ticket")
    now = clock.now()
    patch: dict[str, Any] = {"status": to}
    if to == "waiting_customer":
        patch["paused_at"] = now
    if frm == "waiting_customer" and t.paused_at and t.due_at:
        # The clock was paused: push the deadline out by the paused duration.
        patch["due_at"] = t.due_at + (now - t.paused_at)
        patch["paused_at"] = None
    if to == "resolved":
        patch["resolved_at"] = now
        patch["resolution"] = t.resolution or f"Resolved by {ctx.user.name}"
    if to == "closed":
        patch["closed_at"] = now
    if frm in ("resolved", "closed") and to == "with_human":
        patch.update(reopen_count=t.reopen_count + 1, resolved_at=None, closed_at=None, resolution=None)
    patch["next_move"] = (
        "Waiting on the customer — clock paused"
        if to == "waiting_customer"
        else f"Resolved by {ctx.user.name}"
        if to == "resolved"
        else t.next_move
    )
    number = t.number
    await update_ticket(tx, t, patch, expect_version)
    await system_note(
        tx,
        t.org_id,
        t.id,
        f"{ctx.user.name} moved the ticket from {STATUS_LABEL[frm]} to {STATUS_LABEL[to]}.",
    )
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="ticket.transitioned",
        summary=f"QRY-{number}: {frm} → {to}",
        data={"from": frm, "to": to},
    )


async def assign(tx: AsyncSession, ctx: Ctx, id_: str, user_id: str | None) -> dict[str, str]:
    require(ctx, "ticket.assign", "reassign a ticket to someone else")
    t = await require_ticket(tx, ctx, id_)
    if user_id:
        u = (await tx.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if u is None:
            raise not_found("Person")
        level = await clearance_of(tx, ctx.org_id, u.id, t.department_id)
        if level < CLEARANCE["resolve"]:
            raise forbidden(f"{u.name} is not cleared to resolve work for this team.", "clearance_required")
        target_id, target_name, reason = u.id, u.name, "assigned by a team lead"
    else:
        pick = await pick_assignee(tx, ctx.org_id, t.department_id, t.bucket, t.assignee_id)
        if pick is None:
            raise conflict(
                "no_candidate", "Nobody available is cleared for this team. Escalate to the team lead."
            )
        target_id, target_name, reason = pick["id"], pick["name"], pick["reason"]
    await update_ticket(tx, t, {"assignee_id": target_id, "owner_kind": "user"})
    await system_note(tx, t.org_id, t.id, f"Assigned to {target_name} — {reason}.")
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="ticket.assigned",
        summary=f"QRY-{t.number} assigned to {target_name}",
        data={"to": target_id},
    )
    return {"assignee": target_name, "reason": reason}


async def override_lane(tx: AsyncSession, ctx: Ctx, id_: str, lane: str) -> None:
    """Moving toward less autonomy is open to anyone who works the ticket; toward more needs a lead."""
    require(ctx, "ticket.work", "override the handling")
    t = await require_ticket(tx, ctx, id_)
    old = t.lane
    if old == lane:
        return
    if LANE_AUTONOMY[lane] > LANE_AUTONOMY[old]:
        require(ctx, "ticket.override_up", "give the AI more autonomy on a ticket")
    agent = "Guardrail Sentinel" if "manual" in (lane, old) else await bucketer_for(tx, t)
    await store_feedback(
        tx,
        ctx,
        t,
        agent,
        "override",
        f"Staff overrode the handling: {LANE_NAME[old]} → {LANE_NAME[lane]}. "
        "The thread re-queues down the new path.",
        "prompt" if lane == "manual" else "context",
    )

    # Park whatever was waiting at the gate on the old path.
    cur = await current_action(tx, ctx.org_id, t.id, lock=True)
    if cur is not None and cur.a.state in ("drafted", "awaiting_checker"):
        await tx.execute(
            update(ActionInstance)
            .where(ActionInstance.id == cur.a.id)
            .values(state="cancelled", rejected_note="Lane overridden")
            .execution_options(synchronize_session=False)
        )
        await jobs.cancel_job(tx, t.org_id, f"esc:{cur.a.id}")
    d = await current_draft(tx, ctx.org_id, t.id, lock=True)
    if d is not None and d.state == "draft" and lane != "draft":
        await tx.execute(
            update(Draft)
            .where(Draft.id == d.id)
            .values(state="discarded")
            .execution_options(synchronize_session=False)
        )

    note = f"Overridden by {ctx.user.name} · was {old}"
    if lane == "manual":
        await update_ticket(
            tx,
            t,
            {
                "lane": lane,
                "lane_note": note,
                "status": "with_human",
                "assignee_id": t.assignee_id or ctx.user.id,
                "owner_kind": "user",
                "next_move": "Handle it yourself — the AI stood down",
            },
        )
    else:
        await update_ticket(
            tx,
            t,
            {
                "lane": lane,
                "lane_note": note,
                "status": "triaging",
                "next_move": "Re-running down the new path",
            },
        )
        await jobs.enqueue(
            tx,
            t.org_id,
            "triage",
            {"ticketId": t.id, "forceLane": lane},
            dedupe_key=f"triage:{t.id}:{int(clock.now().timestamp() * 1000)}",
        )
    await system_note(
        tx,
        t.org_id,
        t.id,
        f"{ctx.user.name} overrode the handling to {LANE_NAME[lane]}. Override stored against {agent}.",
    )
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="ticket.lane_overridden",
        summary=f"QRY-{t.number} re-queued as {lane}",
        data={"from": old, "to": lane, "agent": agent},
        feed=Feed("muted", f"QRY-{t.number} · override"),
    )


async def comment(tx: AsyncSession, ctx: Ctx, id_: str, kind: Literal["note", "public"], body: str) -> None:
    if kind == "public":
        require(ctx, "ticket.reply", "reply to customers")
    else:
        require(ctx, "ticket.work", "add notes")
    t = await require_ticket(tx, ctx, id_)
    await add_comment(tx, t.org_id, t.id, actor_of(ctx), kind, body)
    await update_ticket(tx, t, {"logged_minutes": t.logged_minutes + 2})
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="ticket.noted" if kind == "note" else "ticket.replied",
        summary=f"{ctx.user.name} added a {'note' if kind == 'note' else 'reply'} on QRY-{t.number}",
    )


async def toggle_subtask(tx: AsyncSession, ctx: Ctx, id_: str, key: str, done: bool) -> None:
    require(ctx, "ticket.work", "update sub-tasks")
    t = await require_ticket(tx, ctx, id_)
    row = (
        await tx.execute(
            select(Subtask).where(Subtask.org_id == ctx.org_id, Subtask.ticket_id == t.id, Subtask.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Sub-task")
    if row.owner == "AI":
        raise forbidden("This sub-task is completed by the AI pipeline.")
    label = row.label
    await set_subtask(tx, ctx.org_id, t.id, key, done, ctx.user.id)
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="subtask.toggled",
        summary=f"{label}: {'done' if done else 'reopened'}",
    )


async def watch(tx: AsyncSession, ctx: Ctx, id_: str, watching: bool) -> None:
    t = await require_ticket(tx, ctx, id_)
    if watching:
        await tx.execute(
            insert(Watcher)
            .values(org_id=ctx.org_id, ticket_id=t.id, user_id=ctx.user.id)
            .on_conflict_do_nothing()
        )
    else:
        await tx.execute(
            delete(Watcher).where(
                Watcher.org_id == ctx.org_id, Watcher.ticket_id == t.id, Watcher.user_id == ctx.user.id
            )
        )


async def escalate(tx: AsyncSession, ctx: Ctx, id_: str) -> dict[str, str]:
    require(ctx, "ticket.work", "escalate tickets")
    t = await require_ticket(tx, ctx, id_)
    owner = None
    if t.department_id:
        owner_id = (
            await tx.execute(select(Department.owner_id).where(Department.id == t.department_id))
        ).scalar_one_or_none()
        if owner_id:
            owner = (await tx.execute(select(User).where(User.id == owner_id))).scalar_one_or_none()
    await update_ticket(tx, t, {"priority": HIGHER_PRIORITY.get(t.priority, t.priority)})
    if owner is not None:
        await tx.execute(
            insert(Watcher).values(org_id=t.org_id, ticket_id=t.id, user_id=owner.id).on_conflict_do_nothing()
        )
    to = owner.name if owner else "the team lead"
    compliance = ". Compliance notified (regulatory flag on the thread)" if t.regulatory_flag else ""
    await system_note(tx, t.org_id, t.id, f"{ctx.user.name} escalated to {to}{compliance}.")
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="ticket.escalated",
        summary=f"QRY-{t.number} escalated to {to}",
        feed=Feed("stop", f"QRY-{t.number} · escalation"),
    )
    return {"to": to}


PIVOT_RE = re.compile(r"\b(Separately|Also|Second(?:ly)?|In addition)[,:]?\s", re.I)
SENTENCE_RE = re.compile(r"(?<=[.?!])\s+")
TWO_THINGS_RE = re.compile(r"^(Two things\.\s*)", re.I)


def split_parts(body: str) -> list[str]:
    """Split at the pivot the customer used ("Separately", "Also", "Second"), else by sentence halves."""
    pivot = PIVOT_RE.search(body)
    if pivot:
        return [body[: pivot.start()].strip(), body[pivot.start() :].strip()]
    sentences = SENTENCE_RE.split(body)
    mid = -(-len(sentences) // 2)
    return [" ".join(sentences[:mid]), " ".join(sentences[mid:])]


async def split(tx: AsyncSession, ctx: Ctx, id_: str) -> dict[str, list[str]]:
    """Split a multi-intent ticket into child tickets; each child is triaged on its own."""
    require(ctx, "ticket.work", "split tickets")
    t = await require_ticket(tx, ctx, id_)
    first = (
        await tx.execute(
            select(Message)
            .where(Message.org_id == ctx.org_id, Message.ticket_id == t.id)
            .order_by(Message.sent_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    body = first.body if first else t.subject
    parts = split_parts(body)
    if any(not p for p in parts):
        raise bad_request("cannot_split", "This email does not contain two separable requests.")
    now = clock.now()
    number, subject_line = t.number, t.subject
    children: list[str] = []
    for i, part in enumerate(parts):
        n = await next_number(tx, ctx.org_id, "ticket")
        # TS took the first sentence before dropping the "Two things." preamble, which left an empty
        # subject whenever the email opened with it; drop the preamble first.
        head = re.split(r"(?<=[.?!])\s", TWO_THINGS_RE.sub("", part))[0][:110]
        child = Ticket(
            org_id=t.org_id,
            number=n,
            board_id=t.board_id,
            mailbox_id=t.mailbox_id,
            customer_id=t.customer_id,
            subject=f"{head} (split from QRY-{number})",
            from_name=t.from_name,
            from_email=t.from_email,
            received_at=t.received_at,
            lane="manual",
            original_lane="manual",
            lane_note="Split — being triaged",
            status="triaging",
            priority=t.priority,
            segment=t.segment,
            owner_kind="ai",
            sla_minutes=t.sla_minutes,
            due_at=t.due_at,
            parent_id=t.id,
            next_move="Triaging",
        )
        tx.add(child)
        await tx.flush()
        tx.add(
            Message(
                org_id=t.org_id,
                ticket_id=child.id,
                direction="inbound",
                from_name=t.from_name,
                from_addr=t.from_email,
                to_addr=first.to_addr if first else "",
                body=TWO_THINGS_RE.sub("", part),
                sent_at=first.sent_at if first else now,
            )
        )
        tx.add_all(
            [
                TicketLink(
                    org_id=t.org_id,
                    ticket_id=child.id,
                    kind="PARENT",
                    label=f"QRY-{number} · {subject_line[:60]}",
                    ref=f"QRY-{number}",
                    sort=0,
                ),
                TicketLink(
                    org_id=t.org_id,
                    ticket_id=t.id,
                    kind="CHILD",
                    label=f"{ticket_number(n)} · part {i + 1}",
                    ref=ticket_number(n),
                    sort=10 + i,
                ),
            ]
        )
        await tx.flush()
        await jobs.enqueue(tx, t.org_id, "triage", {"ticketId": child.id}, dedupe_key=f"triage:{child.id}")
        children.append(ticket_number(n))
    joined = " and ".join(children)
    await update_ticket(
        tx,
        t,
        {
            "status": "resolved",
            "resolved_at": now,
            "split_proposed": False,
            "resolution": f"Split into {joined}",
            "next_move": f"Split into {joined}",
        },
    )
    await set_subtask(tx, t.org_id, t.id, "c2", True, ctx.user.id)
    await system_note(
        tx,
        t.org_id,
        t.id,
        f"{ctx.user.name} split this into {joined}. Each is triaged and routed separately.",
    )
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="ticket.split",
        summary=f"QRY-{number} split into {', '.join(children)}",
        data={"children": children},
    )
    return {"children": children}


async def merge(tx: AsyncSession, ctx: Ctx, id_: str, into_number: str) -> None:
    require(ctx, "ticket.work", "merge tickets")
    t = await require_ticket(tx, ctx, id_)
    target = await find_ticket(tx, ctx.org_id, into_number)
    if target is None:
        raise not_found(into_number)
    if target.id == t.id:
        raise bad_request("same_ticket", "A ticket cannot be merged into itself.")
    if target.customer_id != t.customer_id:
        raise conflict("different_customer", "Only tickets from the same customer can be merged.")
    target_id, target_no = target.id, target.number
    await tx.execute(
        update(Message)
        .where(Message.org_id == ctx.org_id, Message.ticket_id == t.id)
        .values(ticket_id=target_id)
        .execution_options(synchronize_session=False)
    )
    await update_ticket(
        tx,
        t,
        {
            "status": "closed",
            "closed_at": clock.now(),
            "merged_into_id": target_id,
            "resolution": f"Merged into {into_number}",
        },
    )
    tx.add(
        TicketLink(
            org_id=t.org_id,
            ticket_id=target_id,
            kind="MERGED",
            label=f"QRY-{t.number} merged in",
            ref=f"QRY-{t.number}",
            sort=20,
        )
    )
    await system_note(
        tx,
        t.org_id,
        target_id,
        f"{ctx.user.name} merged QRY-{t.number} into this ticket; its messages are now on this thread.",
    )
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="ticket.merged",
        summary=f"QRY-{t.number} merged into {into_number}",
    )
    # The target's thread changed too; the TS service did not tell its viewers.
    await outbox.publish(
        tx, t.org_id, "ticket.updated", {"ticketId": target_id, "number": ticket_number(target_no)}
    )


async def start_suggestion(tx: AsyncSession, ctx: Ctx, id_: str, index: int) -> None:
    """Start one of the brief's suggested moves: it becomes a sub-task owned by the person who started it."""
    require(ctx, "ticket.work", "work tickets")
    t = await require_ticket(tx, ctx, id_)
    brief = (
        await tx.execute(select(Brief).where(Brief.org_id == ctx.org_id, Brief.ticket_id == t.id))
    ).scalar_one_or_none()
    suggestions = brief.suggestions if brief else []
    sug = suggestions[index] if 0 <= index < len(suggestions) else None
    if not sug:
        raise not_found("Suggestion")
    label, meta = sug.get("label", ""), sug.get("meta", "")
    await tx.execute(
        insert(Subtask)
        .values(org_id=t.org_id, ticket_id=t.id, key=f"s{index}", label=label, owner="You", sort=20 + index)
        .on_conflict_do_nothing()
    )
    await add_comment(
        tx, t.org_id, t.id, actor_of(ctx), "note", f"Started: {label}{f' ({meta})' if meta else ''}."
    )
    await record_ticket_event(
        tx,
        t,
        actor=actor_of(ctx),
        action="suggestion.started",
        summary=f'{ctx.user.name} started "{label}" on QRY-{t.number}',
    )


def parse_if_match(header: str | None) -> int | None:
    return int(header) if header and re.fullmatch(r"[0-9]+", header) else None
