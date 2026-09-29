"""Workspace: personal settings, the admin overview and audit-chain verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.workspace import onboarding, profile, service
from command_inbox.modules.workspace.schemas import AdminOut
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import SettingsBody, WorkspaceProfileBody, WorkspaceSsoBody

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


@router.get("/workspace/profile", response_model=dto.WorkspaceProfileDTO)
async def get_profile(ctx: Ctx = Depends(current_ctx)) -> dto.WorkspaceProfileDTO:
    return await in_tenant(ctx, lambda tx: profile.get_profile(tx, ctx))


@router.put("/workspace/profile", response_model=dto.WorkspaceProfileDTO)
async def update_profile(
    body: WorkspaceProfileBody, ctx: Ctx = Depends(current_ctx)
) -> dto.WorkspaceProfileDTO:
    return await in_tenant(ctx, lambda tx: profile.update_profile(tx, ctx, body))


@router.put("/workspace/sso", response_model=dto.WorkspaceProfileDTO)
async def connect_sso(body: WorkspaceSsoBody, ctx: Ctx = Depends(current_ctx)) -> dto.WorkspaceProfileDTO:
    return await in_tenant(ctx, lambda tx: profile.connect_sso(tx, ctx, body))


@router.get("/onboarding", response_model=dto.OnboardingDTO)
async def get_onboarding(ctx: Ctx = Depends(current_ctx)) -> dto.OnboardingDTO:
    return await in_tenant(ctx, lambda tx: onboarding.checklist(tx, ctx))
