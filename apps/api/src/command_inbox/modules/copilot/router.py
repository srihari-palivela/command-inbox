"""HTTP: the ⌘K copilot."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.copilot.service import ask
from command_inbox.schemas import dto
from command_inbox.schemas.requests import AskBody

router = APIRouter(prefix="/v1", tags=["copilot"])


# exclude_none: an action carries either `filters` or `to`, never a null for the other (as in TS).
@router.post("/copilot/ask", response_model=dto.CopilotAnswerDTO, response_model_exclude_none=True)
async def post_ask(body: AskBody, ctx: Annotated[Ctx, Depends(current_ctx)]) -> dto.CopilotAnswerDTO:
    return await in_tenant(ctx, lambda tx: ask(tx, ctx, body.question))
