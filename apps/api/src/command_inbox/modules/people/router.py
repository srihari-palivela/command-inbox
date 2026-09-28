"""People: the skills and clearance matrix, clearance edits, auto-assign."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.people import service
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import ClearanceBody

router = APIRouter(prefix="/v1", tags=["people"])


@router.get("/people", response_model=dto.PeopleDTO)
async def people(ctx: Ctx = Depends(current_ctx)) -> dto.PeopleDTO:
    return await in_tenant(ctx, lambda tx: service.people(tx, ctx))


@router.put("/clearances", response_model=Ok)
async def set_clearance(body: ClearanceBody, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(
        ctx, lambda tx: service.set_clearance(tx, ctx, body.user_id, body.department_id, body.level)
    )
    return Ok()


@router.post("/assignments/auto", response_model=dto.AutoAssignResultDTO)
async def auto_assign(ctx: Ctx = Depends(current_ctx)) -> dto.AutoAssignResultDTO:
    return await in_tenant(ctx, lambda tx: service.auto_assign(tx, ctx))
