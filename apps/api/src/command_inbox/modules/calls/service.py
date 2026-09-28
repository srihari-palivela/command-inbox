"""Calls: start from a ticket, watch the live transcript, end (wrap-up), then save to the ticket or discard.

A call id in a path is looked up inside the caller's tenant (RLS), so another tenant's call is a 404.
Ending and saving need `ticket.work` (TS checked it only when starting a call).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import replace as dc_replace

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import AI_ACTOR, Ctx, actor_of
from command_inbox.core.errors import conflict, not_found
from command_inbox.db.models import Attachment, Call, Customer, Ticket
from command_inbox.modules.calls.scripts import DIAL_MS, LINE_MS, outcome_for, script_for
from command_inbox.modules.tickets.ops import (
    add_comment,
    lock_ticket,
    record_ticket_event,
    set_subtask,
    update_ticket,
)
from command_inbox.modules.tickets.queries import initials_of, load_ticket
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def _fmt(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def _view(c: Call, t: Ticket, phone: str) -> dto.CallDTO:
    """Live view: dialing → live, transcript revealed as the call progresses."""
    elapsed_ms = (clock.now() - c.started_at).total_seconds() * 1000
    state = c.state
    script = list(c.script or [])
    lines = len(script)
    duration = int(c.duration_sec)
    if state in ("dialing", "live"):
        state = "dialing" if elapsed_ms < DIAL_MS else "live"
        live_ms = max(0.0, elapsed_ms - DIAL_MS)
        lines = min(len(script), int(live_ms // LINE_MS) + 1) if state == "live" else 0
        duration = int(live_ms // 1000)
    digits = "".join(ch for ch in phone if ch.isdigit())
    return dto.CallDTO(
        id=c.id,
        ticket_id=c.ticket_id,
        state=state,  # type: ignore[arg-type]
        started_at=iso_ms(c.started_at),
        duration_sec=duration,
        number="+91 •••• ••" + digits[-4:],
        customer_name=t.from_name,
        customer_initials=initials_of(t.from_name),
        transcript=[dto.CallDTOTranscript(**turn) for turn in script[:lines]],
        script_length=len(script),
        summary=c.summary,
        updates=list(c.updates or []),
        recording=f"call-qry-{t.number}.mp3 · {_fmt(duration)}" if c.recording_key else None,
    )


async def _phone(tx: AsyncSession, customer_id: str | None) -> str:
    if not customer_id:
        return "0000"
    phone = (await tx.execute(select(Customer.phone).where(Customer.id == customer_id))).scalar()
    return phone if phone is not None else "0000"


@dataclass(slots=True)
class _Loaded:
    c: Call
    t: Ticket
    phone: str


async def _load(tx: AsyncSession, ctx: Ctx, call_id: str) -> _Loaded:
    c = (
        await tx.execute(
            select(Call)
            .where(Call.org_id == ctx.org_id, Call.id == call_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if c is None:
        raise not_found("Call")
    t = (
        await tx.execute(select(Ticket).where(Ticket.org_id == ctx.org_id, Ticket.id == c.ticket_id))
    ).scalar_one()
    return _Loaded(c, t, await _phone(tx, t.customer_id))


async def start_call(tx: AsyncSession, ctx: Ctx, ticket_id_or_number: str) -> dto.CallDTO:
    require(ctx, "ticket.work", "call customers")
    found = await load_ticket(tx, ctx.org_id, ticket_id_or_number)
    t = await lock_ticket(tx, ctx.org_id, found.id)
    c = Call(
        org_id=ctx.org_id,
        ticket_id=t.id,
        started_by=ctx.user.id,
        state="live",
        started_at=clock.now(),
        script=script_for(t.number, t.subject),
    )
    tx.add(c)
    await tx.flush()
    await tx.refresh(c)
    return _view(c, t, await _phone(tx, t.customer_id))


async def get_call(tx: AsyncSession, ctx: Ctx, call_id: str) -> dto.CallDTO:
    x = await _load(tx, ctx, call_id)
    return _view(x.c, x.t, x.phone)


async def end_call(tx: AsyncSession, ctx: Ctx, call_id: str) -> dto.CallDTO:
    require(ctx, "ticket.work", "end calls")
    x = await _load(tx, ctx, call_id)
    c, t = x.c, x.t
    if c.state not in ("live", "dialing"):
        raise conflict("not_live", "This call has already ended.")
    live = _view(c, t, x.phone)
    outcome = outcome_for(t.number)
    await tx.execute(
        update(Call)
        .where(Call.id == c.id)
        .values(
            state="wrap",
            ended_at=clock.now(),
            duration_sec=int(live.duration_sec),
            script=[turn.model_dump() for turn in live.transcript],
            summary=outcome["summary"],
            updates=outcome["updates"],
            recording_key=f"calls/{t.org_id}/{c.id}.mp3",
        )
        .execution_options(synchronize_session=False)
    )
    updated = (
        await tx.execute(select(Call).where(Call.id == c.id).execution_options(populate_existing=True))
    ).scalar_one()
    return _view(updated, t, x.phone)


async def save_call(tx: AsyncSession, ctx: Ctx, call_id: str, discard: bool) -> None:
    require(ctx, "ticket.work", "save calls")
    x = await _load(tx, ctx, call_id)
    c, t = x.c, x.t
    if c.state != "wrap":
        raise conflict("not_wrapped", "End the call before saving it.")
    if discard:
        await tx.execute(
            update(Call)
            .where(Call.id == c.id)
            .values(state="discarded")
            .execution_options(synchronize_session=False)
        )
        return
    locked = await lock_ticket(tx, ctx.org_id, t.id)
    outcome = outcome_for(t.number)
    dur = _fmt(int(c.duration_sec))
    turns = len(c.script or [])
    await tx.execute(
        update(Call).where(Call.id == c.id).values(state="saved").execution_options(synchronize_session=False)
    )
    await add_comment(
        tx,
        t.org_id,
        t.id,
        dc_replace(actor_of(ctx), name="Call recording"),
        "call",
        f"Recording saved ({dur}) with full transcript, {turns} turns. {c.summary or ''}",
    )
    await add_comment(
        tx,
        t.org_id,
        t.id,
        AI_ACTOR,
        "system",
        f"Ticket context updated from the call: {' · '.join(outcome['updates'])}",
    )
    tx.add_all(
        [
            Attachment(
                org_id=t.org_id,
                ticket_id=t.id,
                ext="MP3",
                name=f"Call recording · {dur}",
                size=f"{dur} min",
                storage_key=c.recording_key,
            ),
            Attachment(
                org_id=t.org_id,
                ticket_id=t.id,
                ext="TXT",
                name=f"Call transcript · {turns} turns",
                size="4 KB",
                storage_key=f"calls/{t.org_id}/{c.id}.txt",
            ),
        ]
    )
    await tx.flush()
    if outcome.get("completeSubtask"):
        await set_subtask(tx, t.org_id, t.id, outcome["completeSubtask"], True, ctx.user.id)
    locked = await update_ticket(
        tx,
        locked,
        {
            "logged_minutes": locked.logged_minutes + max(1, _js_round(c.duration_sec / 60)),
            "sentiment": "de-escalating" if t.number == 48199 else locked.sentiment,
        },
    )
    await record_ticket_event(
        tx,
        locked,
        actor=actor_of(ctx),
        action="call.saved",
        summary=f"{ctx.user.name} saved a {dur} call on QRY-{t.number}",
        data={"callId": c.id},
    )
