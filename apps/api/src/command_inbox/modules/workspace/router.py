"""Workspace: personal settings, the admin overview and audit-chain verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.workspace import service
from command_inbox.modules.workspace.schemas import AdminOut
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import SettingsBody

router = APIRouter(prefix="/v1", tags=["workspace"])


@router.put("/settings", response_model=Ok)
async def update_settings(body: SettingsBody, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(ctx, lambda tx: service.update_settings(tx, ctx, body))
    return Ok()


@router.get("/admin", response_model=AdminOut)
async def admin_overview(ctx: Ctx = Depends(current_ctx)) -> AdminOut:
    return await in_tenant(ctx, lambda tx: service.admin_overview(tx, ctx))


@router.get("/audit/verify", response_model=dto.AuditVerifyDTO)
async def verify_audit(ctx: Ctx = Depends(current_ctx)) -> dto.AuditVerifyDTO:
    return await in_tenant(ctx, lambda tx: service.verify_audit(tx, ctx))
