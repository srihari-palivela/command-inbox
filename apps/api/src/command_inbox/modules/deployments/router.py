"""HTTP routes for deployments: `/v1/deployments/...`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, idempotent, in_tenant
from command_inbox.modules.deployments import service
from command_inbox.modules.deployments.schemas import (
    BindMailboxesBody,
    CreateDeploymentBody,
    DraftConfigBody,
    NewDraftBody,
    PromoteBody,
    RollbackBody,
)
from command_inbox.schemas import dto

router = APIRouter(prefix="/v1/deployments", tags=["deployments"])


@router.get("", response_model=list[dto.DeploymentDTO])
async def list_deployments(ctx: Ctx = Depends(current_ctx)) -> list[dto.DeploymentDTO]:
    return await in_tenant(ctx, lambda tx: service.list_deployments(tx, ctx))


@router.post("", response_model=dto.DeploymentDetailDTO)
async def create_deployment(
    body: CreateDeploymentBody, request: Request, response: Response, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentDetailDTO:
    return await idempotent(
        request,
        response,
        ctx,
        body.model_dump(mode="json"),
        lambda tx: service.create_deployment(tx, ctx, body),
    )


@router.get("/{deployment_id}", response_model=dto.DeploymentDetailDTO)
async def get_deployment(deployment_id: str, ctx: Ctx = Depends(current_ctx)) -> dto.DeploymentDetailDTO:
    return await in_tenant(ctx, lambda tx: service.get_deployment(tx, ctx, _uuid(deployment_id)))


@router.get("/{deployment_id}/versions/{version_id}", response_model=dto.DeploymentVersionDTO)
async def get_version(
    deployment_id: str, version_id: str, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentVersionDTO:
    return await in_tenant(
        ctx, lambda tx: service.get_version(tx, ctx, _uuid(deployment_id), _uuid(version_id))
    )


@router.post("/{deployment_id}/versions", response_model=dto.DeploymentVersionDTO)
async def new_draft(
    deployment_id: str, body: NewDraftBody, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentVersionDTO:
    return await in_tenant(ctx, lambda tx: service.new_draft(tx, ctx, _uuid(deployment_id), body))


@router.put("/{deployment_id}/versions/{version_id}/config", response_model=dto.DeploymentVersionDTO)
async def save_draft_config(
    deployment_id: str, version_id: str, body: DraftConfigBody, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentVersionDTO:
    return await in_tenant(
        ctx, lambda tx: service.save_draft_config(tx, ctx, _uuid(deployment_id), _uuid(version_id), body)
    )


@router.put("/{deployment_id}/mailboxes", response_model=dto.DeploymentDTO)
async def bind_mailboxes(
    deployment_id: str, body: BindMailboxesBody, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentDTO:
    return await in_tenant(ctx, lambda tx: service.bind_mailboxes(tx, ctx, _uuid(deployment_id), body))


@router.post("/{deployment_id}/versions/{version_id}/promote", response_model=dto.DeploymentVersionDTO)
async def promote(
    deployment_id: str, version_id: str, body: PromoteBody, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentVersionDTO:
    return await in_tenant(
        ctx, lambda tx: service.promote(tx, ctx, _uuid(deployment_id), _uuid(version_id), body)
    )


@router.post("/{deployment_id}/versions/{version_id}/withdraw", response_model=dto.DeploymentVersionDTO)
async def withdraw(
    deployment_id: str, version_id: str, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentVersionDTO:
    return await in_tenant(ctx, lambda tx: service.withdraw(tx, ctx, _uuid(deployment_id), _uuid(version_id)))


@router.post("/{deployment_id}/rollback", response_model=dto.DeploymentDetailDTO)
async def rollback(
    deployment_id: str, body: RollbackBody, ctx: Ctx = Depends(current_ctx)
) -> dto.DeploymentDetailDTO:
    return await in_tenant(ctx, lambda tx: service.rollback(tx, ctx, _uuid(deployment_id), body))


def _uuid(value: str) -> str:
    """Path ids are UUIDs; anything else is simply not found (never a database error)."""
    import uuid

    from command_inbox.core.errors import not_found

    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise not_found("Deployment") from None
