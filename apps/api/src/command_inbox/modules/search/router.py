"""HTTP: global search."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from command_inbox.core.context import Ctx
from command_inbox.core.errors import bad_request
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.search.service import search
from command_inbox.schemas import dto

router = APIRouter(prefix="/v1", tags=["search"])


@router.get("/search", response_model=dto.SearchResultDTO)
async def get_search(
    ctx: Annotated[Ctx, Depends(current_ctx)], q: Annotated[str, Query(max_length=1000)] = ""
) -> dto.SearchResultDTO:
    term = q.strip()
    if len(term) > 200:
        raise bad_request("validation", "Invalid request", "/q String should have at most 200 characters")
    return await in_tenant(ctx, lambda tx: search(tx, ctx, term))
