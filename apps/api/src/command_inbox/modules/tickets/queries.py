"""Ticket read models: summaries, the board list with faceted counts, the personal inbox, lookups."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx
from command_inbox.core.errors import not_found
from command_inbox.db.models import (
    ActionInstance,
    Board,
    Department,
    Draft,
    Mailbox,
    Message,
    Org,
    Ticket,
    User,
)
from command_inbox.domain.sla import SlaInput, at_risk, compute_sla
from command_inbox.domain.transitions import STATUS_GROUP, is_open
from command_inbox.schemas import dto
from command_inbox.schemas.requests import TicketFilters

DEFAULT_BAR = 0.78
DAY = timedelta(hours=24)
UUID_RE = re.compile(r"^[0-9a-f-]{36}$", re.I)
NUMBER_RE = re.compile(r"^QRY-(\d+)$", re.I)


def ticket_number(n: int) -> str:
    return f"QRY-{n}"


def initials_of(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z .]", "", name)
    return "".join(p[0].upper() for p in re.split(r"[\s.]+", cleaned) if p)[:2]


@dataclass(slots=True)
class SummaryExtras:
    department_name: str | None
    board_name: str | None
    assignee: dto.UserRef | None
    thread_count: int
    action_state: str | None
    action_chain: str | None
    draft_state: str | None


@dataclass(slots=True)
class Loaded:
    row: Ticket
    x: SummaryExtras


async def org_bar(tx: AsyncSession, org_id: str) -> float:
    bar = (await tx.execute(select(Org.confidence_bar).where(Org.id == org_id))).scalar_one_or_none()
    return float(bar) if bar is not None else DEFAULT_BAR


async def find_ticket(tx: AsyncSession, org_id: str, id_or_number: str) -> Ticket | None:
    """Resolve `QRY-123` or a ticket id within the tenant; anything else is simply not found."""
    m = NUMBER_RE.match(id_or_number)
    if m:
        cond = and_(Ticket.org_id == org_id, Ticket.number == int(m.group(1)))
    elif UUID_RE.match(id_or_number):
        cond = and_(Ticket.org_id == org_id, Ticket.id == id_or_number)
    else:
        return None
    return (await tx.execute(select(Ticket).where(cond))).scalar_one_or_none()


async def load_ticket(tx: AsyncSession, org_id: str, id_or_number: str) -> Ticket:
    """Like `find_ticket`, but a missing ticket is a 404 problem. Reusable by other modules."""
    t = await find_ticket(tx, org_id, id_or_number)
    if t is None:
        raise not_found("Ticket")
    return t


async def load_summaries(
    tx: AsyncSession, org_id: str, where: ColumnElement[bool] | None = None
) -> list[Loaded]:
    """Tickets with the joins every summary needs."""
    q = (
        select(Ticket, Department.name, Board.name, User.id, User.name, User.initials)
        .outerjoin(Department, Department.id == Ticket.department_id)
        .outerjoin(Board, Board.id == Ticket.board_id)
        .outerjoin(User, User.id == Ticket.assignee_id)
        .where(Ticket.org_id == org_id)
        .order_by(Ticket.due_at.asc(), Ticket.number.desc())
    )
    if where is not None:
        q = q.where(where)
    rows = (await tx.execute(q)).all()
    if not rows:
        return []
    ids = [r[0].id for r in rows]
    counts = dict(
        (
            await tx.execute(
                select(Message.ticket_id, func.count())
                .where(Message.org_id == org_id, Message.ticket_id.in_(ids))
                .group_by(Message.ticket_id)
            )
        ).all()
    )
    actions: dict[str, tuple[str, str]] = {}
    for tid, state, chain in (
        await tx.execute(
            select(ActionInstance.ticket_id, ActionInstance.state, ActionInstance.chain)
            .where(ActionInstance.org_id == org_id, ActionInstance.ticket_id.in_(ids))
            .order_by(ActionInstance.created_at.desc())
        )
    ).all():
        actions.setdefault(tid, (state, chain))
    drafts = dict(
        (
            await tx.execute(
                select(Draft.ticket_id, Draft.state).where(Draft.org_id == org_id, Draft.ticket_id.in_(ids))
            )
        ).all()
    )
    out = []
    for t, dept, board, a_id, a_name, a_init in rows:
        act = actions.get(t.id)
        out.append(
            Loaded(
                t,
                SummaryExtras(
                    department_name=dept,
                    board_name=board,
                    assignee=dto.UserRef(id=a_id, name=a_name, initials=a_init) if a_id else None,
                    thread_count=int(counts.get(t.id, 0)),
                    action_state=act[0] if act else None,
                    action_chain=act[1] if act else None,
                    draft_state=drafts.get(t.id),
                ),
            )
        )
    return out


def to_summary(row: Ticket, x: SummaryExtras, now: datetime) -> dto.TicketSummaryDTO:
    open_ = is_open(row.status)
    pending: Any = None
    if open_:
        if x.action_state == "drafted":
            pending = "maker"
        elif x.action_state == "awaiting_checker":
            pending = "checker"
        elif row.lane == "draft" and x.draft_state == "draft" and row.status == "awaiting_approval":
            pending = "send"
    return dto.TicketSummaryDTO(
        id=row.id,
        number=ticket_number(row.number),
        subject=row.subject,
        from_name=row.from_name,
        from_initials=initials_of(row.from_name),
        bucket=row.bucket,
        department_id=row.department_id,
        department=x.department_name or "Unowned",
        lane=row.lane,  # type: ignore[arg-type]
        original_lane=row.original_lane,  # type: ignore[arg-type]
        lane_note=row.lane_note,
        status=row.status,  # type: ignore[arg-type]
        priority=row.priority,  # type: ignore[arg-type]
        segment=row.segment,
        confidence=row.confidence,
        received_at=iso_ms(row.received_at),
        sla=compute_sla(SlaInput(row.status, row.due_at, row.sla_minutes, row.paused_at), now),
        assignee=x.assignee,
        owner_kind=row.owner_kind,  # type: ignore[arg-type]
        next_move=row.next_move,
        board_id=row.board_id,
        board_name=x.board_name or "",
        thread_count=x.thread_count,
        pending_gate=pending,
    )


def _visible_on_board(row: Ticket, now: datetime) -> bool:
    """Everything open, plus what closed in the last 24 h (not old history or merged tickets)."""
    if row.merged_into_id:
        return False
    if row.status == "closed" and (not row.resolved_at or now - row.resolved_at > DAY):
        return False
    return not (row.status == "resolved" and row.resolved_at and now - row.resolved_at > DAY)


FilterFn = Callable[[dto.TicketSummaryDTO], bool]


def matcher(key: str, value: str, ctx: Ctx, bar: float, board_keys: dict[str, str]) -> FilterFn:
    if key == "board":
        return lambda t: bool(t.board_id) and board_keys.get(t.board_id or "") == value
    if key == "status":
        return lambda t: STATUS_GROUP.get(t.status) == value
    if key == "lane":
        return lambda t: t.lane == value
    if key == "team":
        return lambda t: t.department_id == value
    if key == "owner":
        if value == "mine":
            return lambda t: t.assignee is not None and t.assignee.id == ctx.user.id
        if value == "ai":
            return lambda t: t.owner_kind == "ai"
        if value == "unassigned":
            return lambda t: t.owner_kind == "unassigned"
        return lambda t: t.assignee is not None and t.assignee.id == value
    if key == "due":
        if value == "risk":
            return lambda t: at_risk(t.sla.tone)
        if value == "open":
            return lambda t: is_open(t.status)
        return lambda t: not is_open(t.status)
    if key == "conf":
        return (lambda t: t.confidence < bar) if value == "low" else (lambda t: t.confidence >= 0.9)
    if key == "pri":
        return lambda t: t.priority == value
    if key == "bucket":
        return lambda t: t.bucket == value
    if key == "q":
        q = value.lower()

        def text_match(t: dto.TicketSummaryDTO) -> bool:
            owner = t.assignee.name if t.assignee else ("agent" if t.owner_kind == "ai" else "unassigned")
            hay = " ".join([t.number, t.subject, t.bucket, t.department, t.from_name, owner])
            return q in hay.lower()

        return text_match
    return lambda t: True


FACET_KEYS = ("pri", "bucket", "status", "lane", "team", "owner", "due", "conf")
FACET_VALUES: dict[str, list[str]] = {
    "pri": ["P1", "P2", "P3", "P4"],
    "status": ["triage", "approval", "executing", "human", "customer", "resolved"],
    "lane": ["auto", "draft", "manual"],
    "owner": ["mine", "ai", "unassigned"],
    "due": ["risk", "open", "closed"],
    "conf": ["low", "high"],
}


def active_filters(filters: TicketFilters | dict[str, Any] | None) -> list[tuple[str, str]]:
    raw = filters.model_dump() if isinstance(filters, TicketFilters) else dict(filters or {})
    return [(k, str(v)) for k, v in raw.items() if v is not None and v != ""]


async def list_tickets(
    tx: AsyncSession, ctx: Ctx, filters: TicketFilters | dict[str, Any] | None = None
) -> dto.TicketListDTO:
    """Board/list query with faceted counts: each facet value is counted against every *other* active
    filter, so the numbers in a dropdown predict what selecting it will show. Evaluated in memory over
    the open working set (bounded by the 24 h window)."""
    now = clock.now()
    bar = await org_bar(tx, ctx.org_id)
    boards = (
        await tx.execute(
            select(Board.id, Board.key, Board.name, Board.state, Mailbox.address)
            .outerjoin(Mailbox, Mailbox.id == Board.mailbox_id)
            .where(Board.org_id == ctx.org_id)
            .order_by(Board.sort.asc())
        )
    ).all()
    board_keys = {b.id: b.key for b in boards}
    departments = (
        await tx.execute(
            select(Department.id, Department.name)
            .where(Department.org_id == ctx.org_id)
            .order_by(Department.sort.asc())
        )
    ).all()

    loaded = await load_summaries(
        tx,
        ctx.org_id,
        and_(
            Ticket.merged_into_id.is_(None),
            or_(Ticket.status != "closed", Ticket.resolved_at > now - DAY),
        ),
    )
    universe = [to_summary(x.row, x.x, now) for x in loaded if _visible_on_board(x.row, now)]

    active = active_filters(filters)
    fns = {k: matcher(k, v, ctx, bar, board_keys) for k, v in active}

    def passes(t: dto.TicketSummaryDTO, except_: str | None = None) -> bool:
        return all(fn(t) for k, fn in fns.items() if k != except_)

    items = [t for t in universe if passes(t)]
    buckets = list(dict.fromkeys(t.bucket for t in universe))

    facets: dict[str, dict[str, int | float]] = {}
    for key in FACET_KEYS:
        values = (
            buckets
            if key == "bucket"
            else [d.id for d in departments]
            if key == "team"
            else FACET_VALUES.get(key, [])
        )
        pool = [t for t in universe if passes(t, key)]
        facets[key] = {v: sum(1 for t in pool if matcher(key, v, ctx, bar, board_keys)(t)) for v in values}

    tab_pool = [t for t in universe if passes(t, "board")]
    open_items = [t for t in items if is_open(t.status)]
    return dto.TicketListDTO(
        items=items,
        total=len(items),
        all=len(universe),
        facets=facets,
        stats=dto.TicketListDTOStats(
            open=len(open_items),
            awaiting_decision=sum(1 for t in items if t.status == "awaiting_approval"),
            ai_owned=sum(1 for t in items if t.owner_kind == "ai"),
            at_risk=sum(1 for t in open_items if at_risk(t.sla.tone)),
            closed_today=sum(1 for t in items if not is_open(t.status)),
        ),
        boards=[
            dto.TicketListDTOBoards(
                id=b.id,
                key=b.key,
                name=b.name,
                source=b.address or "",
                state=b.state,
                count=sum(1 for t in tab_pool if t.board_id == b.id),
            )
            for b in boards
        ],
        buckets=buckets,
        departments=[dto.TicketListDTODepartments(id=d.id, name=d.name) for d in departments],
    )


PRI_RANK = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


async def inbox(tx: AsyncSession, ctx: Ctx, filter_: str = "all") -> dto.InboxDTO:
    """The personal queue: open work assigned to me, work waiting for my check as a checker, and what
    closed for me today. Ordered by what needs a decision soonest."""
    now = clock.now()
    awaiting_my_check: ColumnElement[bool] = (
        exists().where(
            ActionInstance.ticket_id == Ticket.id,
            ActionInstance.state == "awaiting_checker",
            ActionInstance.maker_id != ctx.user.id,
        )
        if ctx.can("action.approve_checker")
        else false()
    )
    loaded = await load_summaries(
        tx,
        ctx.org_id,
        and_(
            Ticket.merged_into_id.is_(None),
            or_(
                and_(
                    Ticket.assignee_id == ctx.user.id,
                    or_(Ticket.status.not_in(("resolved", "closed")), Ticket.resolved_at > now - DAY),
                ),
                awaiting_my_check,
            ),
        ),
    )
    all_ = [to_summary(x.row, x.x, now) for x in loaded]
    # Stable two-pass sort: number descending as the final tie-break, then the primary keys.
    all_.sort(key=lambda t: t.number, reverse=True)
    all_.sort(
        key=lambda t: (
            0 if is_open(t.status) else 1,
            PRI_RANK.get(t.priority, 9),
            t.sla.minutes_left if t.sla.minutes_left is not None else 1e9,
        )
    )

    def late(t: dto.TicketSummaryDTO) -> bool:
        return is_open(t.status) and at_risk(t.sla.tone)

    # Counts are what still needs someone; closed-today tickets stay listed but are not counted.
    open_ = [t for t in all_ if is_open(t.status)]
    counts = dto.InboxDTOCounts(
        all=len(open_),
        auto=sum(1 for t in open_ if t.lane == "auto"),
        draft=sum(1 for t in open_ if t.lane == "draft"),
        manual=sum(1 for t in open_ if t.lane == "manual"),
        late=sum(1 for t in all_ if late(t)),
    )
    if filter_ == "all":
        items = all_
    elif filter_ == "late":
        items = [t for t in all_ if late(t)]
    else:
        items = [t for t in all_ if t.lane == filter_]
    return dto.InboxDTO(items=items, counts=counts)
