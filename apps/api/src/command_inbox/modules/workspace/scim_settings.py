"""The workspace admin's SCIM settings: the bearer token for the identity provider and group → role mapping."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.crypto import random_token, sha256
from command_inbox.db.models import Membership, Org, ScimGroup, ScimToken
from command_inbox.modules.scim.service import base_url
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import ScimGroupRolesBody


async def get_settings(tx: AsyncSession, ctx: Ctx) -> dto.ScimSettingsDTO:
    require(ctx, "members.manage", "manage provisioning")
    token = (
        await tx.execute(
            select(ScimToken).where(ScimToken.org_id == ctx.org_id, ScimToken.revoked_at.is_(None))
        )
    ).scalar_one_or_none()
    roles = (await tx.execute(select(Org.scim_group_roles).where(Org.id == ctx.org_id))).scalar_one() or {}
    groups = (
        await tx.execute(
            select(ScimGroup).where(ScimGroup.org_id == ctx.org_id).order_by(ScimGroup.display_name)
        )
    ).scalars()
    provisioned = (
        await tx.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == ctx.org_id, Membership.provisioned_by == "scim")
        )
    ).scalar_one()
    return dto.ScimSettingsDTO(
        base_url=base_url(),
        enabled=token is not None,
        token_created_at=iso_ms(token.created_at) if token else None,
        last_used_at=iso_ms(token.last_used_at) if token and token.last_used_at else None,
        group_roles=roles,
        groups=[dto.ScimSettingsDTOGroups(name=g.display_name, members=len(g.members or [])) for g in groups],
        provisioned_members=provisioned,
        can_edit=ctx.can("members.manage"),
    )


async def issue_token(tx: AsyncSession, ctx: Ctx) -> dto.ScimTokenDTO:
    """A new token replaces any previous one at once (the IdP must be updated with it)."""
    require(ctx, "members.manage", "manage provisioning")
    await tx.execute(
        update(ScimToken)
        .where(ScimToken.org_id == ctx.org_id, ScimToken.revoked_at.is_(None))
        .values(revoked_at=clock.now())
    )
    token = "scim_" + random_token(32)
    tx.add(ScimToken(org_id=ctx.org_id, token_hash=sha256(token), created_by=ctx.user.id))
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="scim.token_issued",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} issued a new provisioning (SCIM) token; any earlier one stopped working",
    )
    return dto.ScimTokenDTO(token=token, settings=await get_settings(tx, ctx))


async def revoke_token(tx: AsyncSession, ctx: Ctx) -> dto.ScimSettingsDTO:
    require(ctx, "members.manage", "manage provisioning")
    await tx.execute(
        update(ScimToken)
        .where(ScimToken.org_id == ctx.org_id, ScimToken.revoked_at.is_(None))
        .values(revoked_at=clock.now())
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="scim.token_revoked",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} turned off automatic provisioning (SCIM)",
    )
    return await get_settings(tx, ctx)


async def set_group_roles(tx: AsyncSession, ctx: Ctx, body: ScimGroupRolesBody) -> dto.ScimSettingsDTO:
    require(ctx, "members.manage", "manage provisioning")
    o = (await tx.execute(select(Org).where(Org.id == ctx.org_id).with_for_update())).scalar_one()
    before = dict(o.scim_group_roles or {})
    o.scim_group_roles = dict(body.group_roles)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="scim.group_roles_changed",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} mapped {len(body.group_roles)} directory groups to roles",
        data={"before": before, "after": dict(body.group_roles)},
    )
    # Apply the mapping to everyone now, not only on the next directory change.
    from command_inbox.modules.scim.service import _recompute

    members = set(
        (await tx.execute(select(Membership.user_id).where(Membership.org_id == ctx.org_id))).scalars()
    )
    await _recompute(tx, ctx.org_id, members)
    return await get_settings(tx, ctx)
