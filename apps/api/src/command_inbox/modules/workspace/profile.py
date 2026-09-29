"""The organisation profile and the tenant's own single sign-on, managed by its admin.

SSO: the admin registers an app in their Entra tenant (or an OAuth client in their Google Cloud project),
enters its IDs and secret here, and we create the brokered identity provider in Keycloak and link it to the
tenant's organization. The secret is sealed with the tenant's data key; it is never returned.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import unprocessable
from command_inbox.core.outbox import publish
from command_inbox.db.models import Membership, Org, User
from command_inbox.platform import keycloak
from command_inbox.platform.keys import tenant_decrypt, tenant_encrypt
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import WorkspaceProfileBody, WorkspaceSsoBody

PROVIDER_LABEL = {"entra": "Microsoft Entra ID", "google": "Google Workspace"}


async def _org(tx: AsyncSession, ctx: Ctx, lock: bool = False) -> Org:
    q = select(Org).where(Org.id == ctx.org_id)
    return (await tx.execute(q.with_for_update() if lock else q)).scalar_one()


async def _sso_members(tx: AsyncSession, org_id: str) -> int:
    return int(
        (
            await tx.execute(
                select(func.count())
                .select_from(Membership)
                .join(User, User.id == Membership.user_id)
                .where(Membership.org_id == org_id, User.idp_subject.is_not(None))
            )
        ).scalar_one()
    )


def _sso_dto(o: Org, members: int) -> dto.WorkspaceSsoDTO:
    c: dict[str, Any] = o.sso_config or {}
    alias = c.get("alias")
    return dto.WorkspaceSsoDTO(
        provider=c.get("provider"),
        directory_id=c.get("directoryId", ""),
        client_id=c.get("clientId", ""),
        has_secret=bool(c.get("secretSealed")),
        state=c.get("state", "not_connected"),
        detail=c.get("detail", ""),
        idp_alias=o.sso_idp_alias,
        redirect_uri=keycloak.broker_redirect_uri(alias or f"{o.slug}-entra"),
        sso_members=members,
    )


async def get_profile(tx: AsyncSession, ctx: Ctx) -> dto.WorkspaceProfileDTO:
    require(ctx, "workspace.manage", "manage the organisation profile")
    o = await _org(tx, ctx)
    return dto.WorkspaceProfileDTO(
        name=o.name,
        legal_name=o.legal_name,
        support_email=o.support_email,
        locale=o.locale,
        currency=o.currency,
        time_zone=o.time_zone,
        email_domains=list(o.sso_email_domains or []),
        region=o.region,
        data_residency=o.data_residency,
        status=o.status,  # type: ignore[arg-type]
        sso=_sso_dto(o, await _sso_members(tx, o.id)),
    )


async def update_profile(tx: AsyncSession, ctx: Ctx, body: WorkspaceProfileBody) -> dto.WorkspaceProfileDTO:
    require(ctx, "workspace.manage", "edit the organisation profile")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(body.time_zone)
    except Exception as err:
        raise unprocessable(
            "bad_time_zone", f"{body.time_zone!r} is not a time zone (e.g. Europe/London)."
        ) from err
    o = await _org(tx, ctx, lock=True)
    before = {
        "legalName": o.legal_name,
        "supportEmail": o.support_email,
        "locale": o.locale,
        "currency": o.currency,
        "timeZone": o.time_zone,
    }
    o.legal_name, o.support_email = body.legal_name, body.support_email
    o.locale, o.currency, o.time_zone = body.locale, body.currency, body.time_zone
    after = {
        "legalName": o.legal_name,
        "supportEmail": o.support_email,
        "locale": o.locale,
        "currency": o.currency,
        "timeZone": o.time_zone,
    }
    await tx.flush()
    changed = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changed:
        await audit(
            tx,
            ctx.org_id,
            actor=actor_of(ctx),
            action="workspace.profile_updated",
            entity="org",
            entity_id=ctx.org_id,
            summary=f"{ctx.user.name} updated the organisation profile",
            data=changed,
        )
        await publish(tx, ctx.org_id, "workspace.updated", {"area": "profile"})
    return await get_profile(tx, ctx)


async def connect_sso(tx: AsyncSession, ctx: Ctx, body: WorkspaceSsoBody) -> dto.WorkspaceProfileDTO:
    require(ctx, "workspace.manage", "connect single sign-on")
    o = await _org(tx, ctx, lock=True)
    current: dict[str, Any] = dict(o.sso_config or {})
    alias = f"{o.slug}-{body.provider}"
    aad = f"sso|{alias}"
    if body.client_secret:
        sealed = await tenant_encrypt(tx, o.id, body.client_secret, aad=aad)
        secret = body.client_secret
    elif current.get("secretSealed") and current.get("alias") == alias:
        sealed = current["secretSealed"]
        secret = await tenant_decrypt(tx, o.id, sealed, aad=aad)
    else:
        raise unprocessable("secret_required", "Enter the client secret.")
    try:
        result = await keycloak.ensure_identity_provider(
            org_alias=o.slug,
            alias=alias,
            display=PROVIDER_LABEL[body.provider],
            provider=body.provider,
            directory_id=body.directory_id,
            client_id=body.client_id,
            secret=secret,
        )
        state, detail = result.state, result.detail
    except Exception as err:  # the identity service is down or refused: record it, the admin retries
        state, detail = "failed", f"Keycloak refused the change: {err}"[:300]
    o.sso_config = {
        "provider": body.provider,
        "directoryId": body.directory_id,
        "clientId": body.client_id,
        "secretSealed": sealed,
        "alias": alias,
        "state": state,
        "detail": detail,
    }
    if state == "connected":
        o.sso_idp_alias = alias
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="workspace.sso_configured",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} set up sign-in with {PROVIDER_LABEL[body.provider]} ({state})",
        data={
            "provider": body.provider,
            "directoryId": body.directory_id,
            "clientId": body.client_id,
            "state": state,
            "secretChanged": bool(body.client_secret),
        },
    )
    await publish(tx, ctx.org_id, "workspace.updated", {"area": "sso"})
    return await get_profile(tx, ctx)
