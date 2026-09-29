"""HTTP: the inbox, the ticket board and workspace, ticket commands.

`/v1/activity` and `/v1/shift` are served by the insights module (same TS source, `insights/service.ts`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.search.service import nl_filter
from command_inbox.modules.tickets import commands as cmd
from command_inbox.modules.tickets.detail import get_ticket_detail
from command_inbox.modules.tickets.queries import inbox, list_tickets
from command_inbox.modules.tickets.schemas import AssignResult, EscalateResult, SplitResult
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import (
    AssignBody,
    CommentBody,
    InboxFilter,
    MergeBody,
    NlFilterBody,
    OverrideLaneBody,
    SubtaskBody,
    TicketFilters,
    TransitionBody,
    WatchBody,
)

router = APIRouter(prefix="/v1", tags=["tickets"])

TicketId = Annotated[str, Path(min_length=3, max_length=64)]
CtxDep = Annotated[Ctx, Depends(current_ctx)]


@router.get("/inbox", response_model=dto.InboxDTO)
async def get_inbox(ctx: CtxDep, filter: InboxFilter = "all") -> dto.InboxDTO:
    return await in_tenant(ctx, lambda tx: inbox(tx, ctx, filter))


@router.get("/tickets", response_model=dto.TicketListDTO)
async def get_tickets(ctx: CtxDep, filters: Annotated[TicketFilters, Query()]) -> dto.TicketListDTO:
    return await in_tenant(ctx, lambda tx: list_tickets(tx, ctx, filters))


@router.post("/tickets/nl-filter", response_model=dto.NlFilterDTO, response_model_exclude_none=True)
async def post_nl_filter(body: NlFilterBody, ctx: CtxDep) -> dto.NlFilterDTO:
    return await in_tenant(ctx, lambda tx: nl_filter(tx, ctx, body.query))


@router.get("/tickets/{id}", response_model=dto.TicketDetailDTO)
async def get_ticket(id: TicketId, ctx: CtxDep) -> dto.TicketDetailDTO:
    return await in_tenant(ctx, lambda tx: get_ticket_detail(tx, ctx, id))


@router.post("/tickets/{id}/transition", response_model=Ok)
async def post_transition(
    id: TicketId,
    body: TransitionBody,
    ctx: CtxDep,
    if_match: Annotated[str | None, Header()] = None,
) -> Ok:
    version = cmd.parse_if_match(if_match)
    await in_tenant(ctx, lambda tx: cmd.transition(tx, ctx, id, body.to, version))
    return Ok()


@router.post("/tickets/{id}/assign", response_model=AssignResult)
async def post_assign(id: TicketId, body: AssignBody, ctx: CtxDep) -> AssignResult:
    r = await in_tenant(ctx, lambda tx: cmd.assign(tx, ctx, id, body.user_id))
    return AssignResult(**r)


@router.post("/tickets/{id}/override-lane", response_model=Ok)
async def post_override_lane(id: TicketId, body: OverrideLaneBody, ctx: CtxDep) -> Ok:
    await in_tenant(ctx, lambda tx: cmd.override_lane(tx, ctx, id, body.lane))
    return Ok()


@router.post("/tickets/{id}/comments", response_model=Ok)
async def post_comment(id: TicketId, body: CommentBody, ctx: CtxDep) -> Ok:
    await in_tenant(ctx, lambda tx: cmd.comment(tx, ctx, id, body.kind, body.body))
    return Ok()


@router.patch("/tickets/{id}/subtasks/{key}", response_model=Ok)
async def patch_subtask(
    id: TicketId,
    key: Annotated[str, Path(max_length=20)],
    body: SubtaskBody,
    ctx: CtxDep,
) -> Ok:
    await in_tenant(ctx, lambda tx: cmd.toggle_subtask(tx, ctx, id, key, body.done))
    return Ok()


@router.put("/tickets/{id}/watch", response_model=Ok)
async def put_watch(id: TicketId, body: WatchBody, ctx: CtxDep) -> Ok:
    await in_tenant(ctx, lambda tx: cmd.watch(tx, ctx, id, body.watching))
    return Ok()


@router.post("/tickets/{id}/escalate", response_model=EscalateResult)
async def post_escalate(id: TicketId, ctx: CtxDep) -> EscalateResult:
    return EscalateResult(**await in_tenant(ctx, lambda tx: cmd.escalate(tx, ctx, id)))


@router.post("/tickets/{id}/split", response_model=SplitResult)
async def post_split(id: TicketId, ctx: CtxDep) -> SplitResult:
    return SplitResult(**await in_tenant(ctx, lambda tx: cmd.split(tx, ctx, id)))


@router.post("/tickets/{id}/merge", response_model=Ok)
async def post_merge(id: TicketId, body: MergeBody, ctx: CtxDep) -> Ok:
    await in_tenant(ctx, lambda tx: cmd.merge(tx, ctx, id, body.into_number))
    return Ok()


@router.post("/tickets/{id}/suggestions/{index}/start", response_model=Ok)
async def post_start_suggestion(
    id: TicketId,
    index: Annotated[int, Path(ge=0, le=10)],
    ctx: CtxDep,
) -> Ok:
    await in_tenant(ctx, lambda tx: cmd.start_suggestion(tx, ctx, id, index))
    return Ok()
