"""HTTP routes for tenant administration: `/v1/members`, `/v1/invitations`, `/v1/permissions`."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response

from command_inbox.core.context import Ctx
from command_inbox.core.errors import not_found
from command_inbox.core.http import current_ctx, idempotent, in_tenant
from command_inbox.modules.members import service
from command_inbox.modules.members.schemas import InvitationBody, MemberRoleBody, PermissionOverridesBody
from command_inbox.rbac.policy import policies
from command_inbox.schemas import dto
from command_inbox.schemas.base import CamelModel

router = APIRouter(prefix="/v1", tags=["members"])


class Removed(CamelModel):
    ok: bool = True
    revoked_sessions: int


def _id(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise not_found(what) from None


@router.get("/members", response_model=list[dto.MemberDTO])
async def list_members(ctx: Ctx = Depends(current_ctx)) -> list[dto.MemberDTO]:
    return await in_tenant(ctx, lambda tx: service.list_members(tx, ctx))


@router.put("/members/{user_id}/role", response_model=dto.MemberDTO)
async def change_role(user_id: str, body: MemberRoleBody, ctx: Ctx = Depends(current_ctx)) -> dto.MemberDTO:
    return await in_tenant(ctx, lambda tx: service.change_role(tx, ctx, _id(user_id, "Member"), body))


@router.delete("/members/{user_id}", response_model=Removed)
async def remove_member(user_id: str, ctx: Ctx = Depends(current_ctx)) -> Removed:
    n = await in_tenant(ctx, lambda tx: service.remove_member(tx, ctx, _id(user_id, "Member")))
    return Removed(revoked_sessions=n)


@router.get("/invitations", response_model=list[dto.InvitationDTO])
async def list_invitations(ctx: Ctx = Depends(current_ctx)) -> list[dto.InvitationDTO]:
    return await in_tenant(ctx, lambda tx: service.list_invitations(tx, ctx))


@router.post("/invitations", response_model=dto.InvitationDTO)
async def create_invitation(
    body: InvitationBody, request: Request, response: Response, ctx: Ctx = Depends(current_ctx)
) -> dto.InvitationDTO:
    return await idempotent(
        request,
        response,
        ctx,
        body.model_dump(mode="json"),
        lambda tx: service.create_invitation(tx, ctx, body),
    )


@router.delete("/invitations/{invitation_id}", response_model=dto.InvitationDTO)
async def revoke_invitation(invitation_id: str, ctx: Ctx = Depends(current_ctx)) -> dto.InvitationDTO:
    return await in_tenant(
        ctx, lambda tx: service.revoke_invitation(tx, ctx, _id(invitation_id, "Invitation"))
    )


@router.get("/permissions", response_model=dto.PermissionMatrixDTO)
async def permission_matrix(ctx: Ctx = Depends(current_ctx)) -> dto.PermissionMatrixDTO:
    return await in_tenant(ctx, lambda tx: service.permission_matrix(tx, ctx))


@router.put("/permissions", response_model=dto.PermissionMatrixDTO)
async def set_overrides(
    body: PermissionOverridesBody, ctx: Ctx = Depends(current_ctx)
) -> dto.PermissionMatrixDTO:
    await in_tenant(ctx, lambda tx: service.set_overrides(tx, ctx, body))
    # After commit: this process reloads now; others reload on the rbac.updated notification.
    policies.invalidate(ctx.org_id)
    return await in_tenant(ctx, lambda tx: service.permission_matrix(tx, ctx))
