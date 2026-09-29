"""Workspace: personal settings, the admin overview and audit-chain verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Response

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.workspace import models, onboarding, operations, profile, scim_settings, service
from command_inbox.modules.workspace.schemas import AdminOut
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import (
    ModelPolicyBody,
    OperationsBody,
    ScimGroupRolesBody,
    SettingsBody,
    WorkspaceProfileBody,
    WorkspaceSsoBody,
)

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


@router.get("/workspace/model-policy", response_model=dto.ModelPolicyDTO)
async def get_model_policy(ctx: Ctx = Depends(current_ctx)) -> dto.ModelPolicyDTO:
    return await in_tenant(ctx, lambda tx: models.get_policy(tx, ctx))


@router.put("/workspace/model-policy", response_model=dto.ModelPolicyDTO)
async def update_model_policy(body: ModelPolicyBody, ctx: Ctx = Depends(current_ctx)) -> dto.ModelPolicyDTO:
    return await in_tenant(ctx, lambda tx: models.update_policy(tx, ctx, body))


@router.get("/workspace/operations", response_model=dto.OperationsDTO)
async def get_operations(ctx: Ctx = Depends(current_ctx)) -> dto.OperationsDTO:
    return await in_tenant(ctx, lambda tx: operations.get_operations(tx, ctx))


@router.put("/workspace/operations", response_model=dto.OperationsDTO)
async def update_operations(body: OperationsBody, ctx: Ctx = Depends(current_ctx)) -> dto.OperationsDTO:
    return await in_tenant(ctx, lambda tx: operations.update_operations(tx, ctx, body))


@router.get("/audit/export")
async def export_audit(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    ctx: Ctx = Depends(current_ctx),
) -> Response:
    data, manifest = await in_tenant(ctx, lambda tx: operations.export_audit(tx, ctx, since, until))
    name = f"audit-{manifest['tenant']}-{manifest['generatedAt'][:10]}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="{name}"',
            "x-audit-manifest-signature": manifest["signature"],
        },
    )


@router.post("/audit/export/verify", response_model=dto.AuditVerifyDTO)
async def verify_export(
    manifest: dict[str, Any] = Body(...), ctx: Ctx = Depends(current_ctx)
) -> dto.AuditVerifyDTO:
    """Whether a manifest handed back later is one this workspace signed, unchanged."""
    from command_inbox.rbac.policy import require

    require(ctx, "audit.verify", "verify an audit export")
    ok = operations.verify_manifest(ctx.org_id, manifest)
    return dto.AuditVerifyDTO(ok=ok, events=int(manifest.get("events") or 0), broken_at=None)


@router.get("/workspace/scim", response_model=dto.ScimSettingsDTO)
async def get_scim(ctx: Ctx = Depends(current_ctx)) -> dto.ScimSettingsDTO:
    return await in_tenant(ctx, lambda tx: scim_settings.get_settings(tx, ctx))


@router.post("/workspace/scim/token", response_model=dto.ScimTokenDTO)
async def issue_scim_token(ctx: Ctx = Depends(current_ctx)) -> dto.ScimTokenDTO:
    return await in_tenant(ctx, lambda tx: scim_settings.issue_token(tx, ctx))


@router.delete("/workspace/scim/token", response_model=dto.ScimSettingsDTO)
async def revoke_scim_token(ctx: Ctx = Depends(current_ctx)) -> dto.ScimSettingsDTO:
    return await in_tenant(ctx, lambda tx: scim_settings.revoke_token(tx, ctx))


@router.put("/workspace/scim/roles", response_model=dto.ScimSettingsDTO)
async def set_scim_roles(body: ScimGroupRolesBody, ctx: Ctx = Depends(current_ctx)) -> dto.ScimSettingsDTO:
    return await in_tenant(ctx, lambda tx: scim_settings.set_group_roles(tx, ctx, body))
