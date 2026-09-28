"""HTTP routes for calls started from a ticket."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.calls import service
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import UUID_RE, SaveCallBody

router = APIRouter(prefix="/v1", tags=["calls"])

TicketId = Annotated[str, Path(min_length=3, max_length=64)]
CallId = Annotated[str, Path(pattern=UUID_RE)]


@router.post("/tickets/{id}/calls", response_model=dto.CallDTO)
async def start_call(id: TicketId, ctx: Ctx = Depends(current_ctx)) -> dto.CallDTO:
    return await in_tenant(ctx, lambda tx: service.start_call(tx, ctx, id))


@router.get("/calls/{id}", response_model=dto.CallDTO)
async def get_call(id: CallId, ctx: Ctx = Depends(current_ctx)) -> dto.CallDTO:
    return await in_tenant(ctx, lambda tx: service.get_call(tx, ctx, id))


@router.post("/calls/{id}/end", response_model=dto.CallDTO)
async def end_call(id: CallId, ctx: Ctx = Depends(current_ctx)) -> dto.CallDTO:
    return await in_tenant(ctx, lambda tx: service.end_call(tx, ctx, id))


@router.post("/calls/{id}/save", response_model=Ok)
async def save_call(id: CallId, body: SaveCallBody, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(ctx, lambda tx: service.save_call(tx, ctx, id, body.discard))
    return Ok()
