"""HTTP routes for mailbox connections."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.mailboxes import service
from command_inbox.schemas import dto
from command_inbox.schemas.requests import UUID_RE, CreateMailboxBody, MailboxSendingBody

router = APIRouter(prefix="/v1", tags=["mailboxes"])
MailboxId = Annotated[str, Path(pattern=UUID_RE)]


@router.get("/mailbox-connections", response_model=dto.MailConnectorsDTO)
async def overview(ctx: Ctx = Depends(current_ctx)) -> dto.MailConnectorsDTO:
    return await in_tenant(ctx, lambda tx: service.overview(tx, ctx))


@router.post("/mailbox-connections", response_model=dto.MailboxConnectionDTO)
async def create(body: CreateMailboxBody, ctx: Ctx = Depends(current_ctx)) -> dto.MailboxConnectionDTO:
    return await in_tenant(ctx, lambda tx: service.create(tx, ctx, body))


@router.post("/mailboxes/{id}/test", response_model=dto.MailboxConnectionDTO)
async def test(id: MailboxId, ctx: Ctx = Depends(current_ctx)) -> dto.MailboxConnectionDTO:
    return await in_tenant(ctx, lambda tx: service.start_test(tx, ctx, id))


@router.put("/mailboxes/{id}/sending", response_model=dto.MailboxConnectionDTO)
async def sending(
    id: MailboxId, body: MailboxSendingBody, ctx: Ctx = Depends(current_ctx)
) -> dto.MailboxConnectionDTO:
    return await in_tenant(ctx, lambda tx: service.set_sending(tx, ctx, id, body))


@router.post("/mailboxes/{id}/sync", response_model=dto.MailboxConnectionDTO)
async def sync(id: MailboxId, ctx: Ctx = Depends(current_ctx)) -> dto.MailboxConnectionDTO:
    return await in_tenant(ctx, lambda tx: service.sync_now(tx, ctx, id))


@router.post("/mailboxes/{id}/disconnect", response_model=dto.MailboxConnectionDTO)
async def disconnect(id: MailboxId, ctx: Ctx = Depends(current_ctx)) -> dto.MailboxConnectionDTO:
    return await in_tenant(ctx, lambda tx: service.disconnect(tx, ctx, id))
