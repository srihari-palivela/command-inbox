"""Global ⌘K search (tickets, customers, knowledge, policies) and natural-language → ticket filters."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import false, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.db.models import ActionTemplate, BucketRule, Department, KnowledgeDoc, PriorityRule, Ticket
from command_inbox.domain.nl_filter import parse_natural_filters
from command_inbox.modules.copilot.system2 import model_filters
from command_inbox.modules.tickets.queries import ticket_number
from command_inbox.schemas import dto
from command_inbox.schemas.requests import TicketFilters

NUM_RE = re.compile(r"^(?:qry-)?(\d{3,})$", re.I)


def _empty() -> dto.SearchResultDTO:
    return dto.SearchResultDTO(tickets=[], customers=[], knowledge=[], policies=[])


async def search(tx: AsyncSession, ctx: Ctx, q: str) -> dto.SearchResultDTO:
    term = q.strip()
    if len(term) < 2:
        return _empty()
    literal = re.sub(r"[%_]", "", term)
    if not literal:  # TS searched for "%%" here and matched everything
        return _empty()
    like = f"%{literal}%"
    num = NUM_RE.match(term)
    org = ctx.org_id
    tickets = (
        await tx.execute(
            select(Ticket.id, Ticket.number, Ticket.subject, Ticket.lane)
            .where(
                Ticket.org_id == org,
                or_(
                    Ticket.subject.ilike(like),
                    Ticket.from_name.ilike(like),
                    Ticket.bucket.ilike(like),
                    Ticket.number == int(num.group(1)) if num else false(),
                ),
            )
            .order_by(Ticket.received_at.desc())
            .limit(6)
        )
    ).all()
    customers = (
        await tx.execute(
            text(
                """
    select c.id::text as id, c.cif, c.name,
           (select count(*) from tickets t where t.org_id = c.org_id and t.customer_id = c.id)::int as tickets,
           (select t.id::text from tickets t where t.org_id = c.org_id and t.customer_id = c.id
             order by t.received_at desc limit 1) as latest
      from customers c
     where c.org_id = :org and (c.name ilike :like or c.cif ilike :like or c.email ilike :like)
     order by c.name limit 5"""
            ),
            {"org": org, "like": like},
        )
    ).all()
    knowledge = (
        (
            await tx.execute(
                select(KnowledgeDoc.id, KnowledgeDoc.title, KnowledgeDoc.section, KnowledgeDoc.status)
                .where(
                    KnowledgeDoc.org_id == org,
                    or_(
                        KnowledgeDoc.title.ilike(like),
                        KnowledgeDoc.section.ilike(like),
                        KnowledgeDoc.body.ilike(like),
                    ),
                )
                .order_by(KnowledgeDoc.title.asc())
                .limit(5)
            )
        ).all()
        if ctx.can("ticket.work")
        else []
    )
    rules = (
        await tx.execute(
            select(BucketRule.id, BucketRule.description)
            .where(BucketRule.org_id == org, BucketRule.description.ilike(like))
            .limit(3)
        )
    ).all()
    pri = (
        await tx.execute(
            select(PriorityRule.id, PriorityRule.description)
            .where(PriorityRule.org_id == org, PriorityRule.description.ilike(like))
            .limit(3)
        )
    ).all()
    tpl = (
        await tx.execute(
            select(ActionTemplate.id, ActionTemplate.name, ActionTemplate.code)
            .where(
                ActionTemplate.org_id == org,
                or_(ActionTemplate.name.ilike(like), ActionTemplate.code.ilike(like)),
            )
            .limit(3)
        )
    ).all()
    return dto.SearchResultDTO(
        tickets=[
            dto.SearchResultDTOTickets(
                id=t.id, number=ticket_number(t.number), subject=t.subject, lane=t.lane
            )
            for t in tickets
        ],
        customers=[
            dto.SearchResultDTOCustomers(
                id=c.id, cif=c.cif, name=c.name, tickets=c.tickets, latest_ticket_id=c.latest
            )
            for c in customers
        ],
        knowledge=[
            dto.SearchResultDTOKnowledge(id=k.id, title=k.title, section=k.section, status=k.status)
            for k in knowledge
        ],
        policies=[
            *(dto.SearchResultDTOPolicies(id=r.id, text=r.description, kind="Bucketing rule") for r in rules),
            *(dto.SearchResultDTOPolicies(id=r.id, text=r.description, kind="Priority rule") for r in pri),
            *(
                dto.SearchResultDTOPolicies(id=r.id, text=f"{r.code} · {r.name}", kind="Action template")
                for r in tpl
            ),
        ],
    )


STATUS_TEXT = {
    "triage": "Agent triaging",
    "approval": "Awaiting approval",
    "executing": "Executing",
    "human": "With a human",
    "customer": "Waiting on customer",
    "resolved": "Resolved",
}
LANE_TEXT = {"auto": "Auto", "draft": "Draft", "manual": "You"}
OWNER_TEXT = {"mine": "me", "ai": "the AI", "unassigned": "nobody"}
DUE_TEXT = {"risk": "Running late", "open": "Open only", "closed": "Closed only"}


def chip_text(key: str, v: str, departments: list[dict[str, str]]) -> str:
    if key == "status":
        return STATUS_TEXT.get(v, v)
    if key == "lane":
        return f"Handled: {LANE_TEXT.get(v, v)}"
    if key == "team":
        return f"Team: {next((d['name'] for d in departments if d['id'] == v), v)}"
    if key == "owner":
        return f"Owner: {OWNER_TEXT.get(v, v)}"
    if key == "due":
        return DUE_TEXT.get(v, v)
    if key == "conf":
        return "Below the bar" if v == "low" else "High confidence"
    if key == "pri":
        return f"Priority: {v}"
    if key == "bucket":
        return f"Type: {v}"
    if key == "board":
        return f"Board: {v}"
    return f"Text: “{v}”"


async def nl_filter(tx: AsyncSession, ctx: Ctx, query: str) -> dto.NlFilterDTO:
    """The model (when configured) maps language onto the filter schema; the parser is the fallback."""
    departments = [
        {"id": d.id, "name": d.name}
        for d in (
            await tx.execute(select(Department.id, Department.name).where(Department.org_id == ctx.org_id))
        ).all()
    ]
    filters: TicketFilters | None = None
    used = "heuristic"
    raw = await model_filters(query, departments)
    if raw is not None:
        try:
            filters = TicketFilters.model_validate(raw)
            used = "claude"
        except ValidationError:
            filters = None
    if filters is None:
        filters = TicketFilters.model_validate(parse_natural_filters(query, departments).filters)
    values: dict[str, Any] = filters.model_dump(exclude_none=True)
    chips = [
        dto.NlFilterDTOChips(key=k, value=str(v), text=chip_text(k, str(v), departments))  # type: ignore[arg-type]
        for k, v in values.items()
        if v
    ]
    return dto.NlFilterDTO(filters=filters, chips=chips, understood=bool(chips), provider=used)
