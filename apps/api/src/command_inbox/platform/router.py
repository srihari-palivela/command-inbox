"""HTTP routes of the platform console: `/v1/platform/*`, authenticated by the operator session only."""

from __future__ import annotations

import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from command_inbox.config import settings
from command_inbox.core.crypto import decrypt, encrypt
from command_inbox.core.errors import AppError, forbidden, unauthorized
from command_inbox.db.engine import global_tx
from command_inbox.db.models import PlatformOperator
from command_inbox.platform import oidc, tenants
from command_inbox.platform.audit import platform_audit, verify_platform_chain
from command_inbox.platform.rbac import CAPABILITIES, OperatorCtx, require_platform
from command_inbox.platform.schemas import CreateTenantBody, DevLoginBody, ReinviteBody, TenantReasonBody
from command_inbox.platform.sessions import (
    PLATFORM_COOKIE,
    create_platform_session,
    resolve_platform_session,
    revoke_platform_session,
)
from command_inbox.schemas import platform_dto as pdto
from command_inbox.schemas.base import Ok

router = APIRouter(prefix="/v1/platform", tags=["platform"])
TXN_COOKIE = "ci_platform_oidc"
TXN_MAX_AGE = 600
TenantId = Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")]

# Reachable without an operator session; the dev login is also exempt from CSRF (no session yet).
PUBLIC = {
    "/v1/platform/auth/config",
    "/v1/platform/auth/dev-login",
    "/v1/platform/auth/oidc/login",
    "/v1/platform/auth/oidc/callback",
}
CSRF_EXEMPT = {"/v1/platform/auth/dev-login"}


def operator_of(request: Request) -> OperatorCtx:
    op = getattr(request.state, "operator", None)
    if op is None:
        raise unauthorized()
    return op


async def current_operator(request: Request) -> OperatorCtx:
    return operator_of(request)


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        PLATFORM_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=settings.platform_session_ttl_hours * 3600,
    )


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _me(op: OperatorCtx, csrf: str) -> pdto.PlatformMeDTO:
    return pdto.PlatformMeDTO(
        operator=pdto.OperatorDTO(
            id=op.id,
            email=op.email,
            name=op.name,
            role=op.role,
            capabilities=sorted(op.capabilities),  # type: ignore[arg-type]
        ),
        csrf_token=csrf,
    )


# ── Sign-in ─────────────────────────────────────────────────────────────────────────────────────────


@router.get("/auth/config", response_model=pdto.PlatformAuthConfigDTO)
async def auth_config() -> pdto.PlatformAuthConfigDTO:
    dev = []
    if settings.demo_mode:
        async with global_tx() as g:
            rows = (
                await g.execute(
                    select(PlatformOperator)
                    .where(PlatformOperator.disabled_at.is_(None))
                    .order_by(PlatformOperator.email)
                )
            ).scalars()
            dev = [{"email": o.email, "name": o.name, "role": o.role} for o in rows]
    return pdto.PlatformAuthConfigDTO.model_validate(
        {"sso": bool(settings.platform_oidc_issuer), "devOperators": dev}
    )


async def _start_session(
    request: Request, response: Response, operator_id: str, via: str
) -> pdto.PlatformMeDTO:
    token, _csrf = await create_platform_session(
        operator_id, user_agent=request.headers.get("user-agent", ""), ip=_ip(request)
    )
    _set_cookie(response, token)
    resolved = await resolve_platform_session(token, request.state.request_id)
    assert resolved is not None
    op, csrf = resolved
    async with global_tx() as g:
        await platform_audit(
            g,
            operator_id=op.id,
            operator_email=op.email,
            action="operator.signed_in",
            summary=f"{op.name} signed in to the console",
            data={"via": via, "ip": _ip(request) or ""},
        )
    return _me(op, csrf)


@router.post("/auth/dev-login", response_model=pdto.PlatformMeDTO)
async def dev_login(body: DevLoginBody, request: Request, response: Response) -> pdto.PlatformMeDTO:
    """Development only: sign in as an existing operator without SSO. Production never enables demo mode."""
    if not settings.demo_mode:
        raise forbidden("Sign in with single sign-on.", "sso_required")
    async with global_tx() as g:
        op = (
            await g.execute(
                select(PlatformOperator).where(
                    PlatformOperator.email == body.email, PlatformOperator.disabled_at.is_(None)
                )
            )
        ).scalar_one_or_none()
    if op is None:
        raise forbidden("You are not a platform operator.", "not_an_operator")
    return await _start_session(request, response, op.id, "development")


@router.get("/auth/oidc/login")
async def oidc_login(next: str = Query("/", max_length=300)) -> Response:
    url, txn = await oidc.authorization_url(next)
    txn["iat"] = str(int(time.time()))
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        TXN_COOKIE,
        encrypt(json.dumps(txn), purpose="platform-oidc-transaction"),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/v1/platform/auth/oidc",
        max_age=TXN_MAX_AGE,
    )
    return resp


@router.get("/auth/oidc/callback")
async def oidc_callback(
    request: Request, code: str = Query(..., max_length=4096), state: str = Query(..., max_length=200)
) -> Response:
    origin = settings.console_origin.rstrip("/")
    fail = RedirectResponse(origin + "/?error=sso", status_code=302)
    sealed = request.cookies.get(TXN_COOKIE)
    if not sealed:
        return fail
    try:
        txn = json.loads(decrypt(sealed, purpose="platform-oidc-transaction"))
    except Exception:
        return fail
    if txn.get("state") != state or time.time() - int(txn.get("iat", "0")) > TXN_MAX_AGE:
        return fail
    try:
        op = await oidc.admit(await oidc.exchange(code, txn))
    except AppError as err:
        return RedirectResponse(f"{origin}/?error={err.code}", status_code=302)
    resp = RedirectResponse(origin + txn["next"], status_code=302)
    await _start_session(request, resp, op.id, "sso")
    resp.delete_cookie(TXN_COOKIE, path="/v1/platform/auth/oidc")
    return resp


@router.post("/auth/logout", response_model=Ok)
async def logout(response: Response, op: OperatorCtx = Depends(current_operator)) -> Ok:
    await revoke_platform_session(op.session_id)
    response.delete_cookie(PLATFORM_COOKIE, path="/")
    return Ok()


@router.get("/me", response_model=pdto.PlatformMeDTO)
async def me(request: Request, op: OperatorCtx = Depends(current_operator)) -> pdto.PlatformMeDTO:
    return _me(op, request.state.csrf_token)


# ── Tenants ─────────────────────────────────────────────────────────────────────────────────────────


@router.get("/tenants", response_model=list[pdto.TenantSummaryDTO])
async def list_tenants(op: OperatorCtx = Depends(current_operator)) -> list[pdto.TenantSummaryDTO]:
    return await tenants.list_tenants(op)


@router.post("/tenants", response_model=pdto.TenantDetailDTO)
async def create_tenant(
    body: CreateTenantBody, op: OperatorCtx = Depends(current_operator)
) -> pdto.TenantDetailDTO:
    return await tenants.create_tenant(op, body)


@router.get("/tenants/{tenant_id}", response_model=pdto.TenantDetailDTO)
async def get_tenant(
    tenant_id: TenantId, op: OperatorCtx = Depends(current_operator)
) -> pdto.TenantDetailDTO:
    return await tenants.tenant_detail(op, tenant_id)


@router.post("/tenants/{tenant_id}/provision", response_model=pdto.TenantDetailDTO)
async def provision(tenant_id: TenantId, op: OperatorCtx = Depends(current_operator)) -> pdto.TenantDetailDTO:
    return await tenants.provision(op, tenant_id)


@router.post("/tenants/{tenant_id}/suspend", response_model=pdto.TenantDetailDTO)
async def suspend(
    tenant_id: TenantId, body: TenantReasonBody, op: OperatorCtx = Depends(current_operator)
) -> pdto.TenantDetailDTO:
    return await tenants.suspend(op, tenant_id, body)


@router.post("/tenants/{tenant_id}/resume", response_model=pdto.TenantDetailDTO)
async def resume(
    tenant_id: TenantId, body: TenantReasonBody, op: OperatorCtx = Depends(current_operator)
) -> pdto.TenantDetailDTO:
    return await tenants.resume(op, tenant_id, body)


@router.post("/tenants/{tenant_id}/archive", response_model=pdto.TenantDetailDTO)
async def archive(
    tenant_id: TenantId, body: TenantReasonBody, op: OperatorCtx = Depends(current_operator)
) -> pdto.TenantDetailDTO:
    return await tenants.archive(op, tenant_id, body)


@router.post("/tenants/{tenant_id}/invitations", response_model=pdto.TenantDetailDTO)
async def reinvite(
    tenant_id: TenantId, body: ReinviteBody, op: OperatorCtx = Depends(current_operator)
) -> pdto.TenantDetailDTO:
    return await tenants.reinvite(op, tenant_id, body)


# ── Platform audit ──────────────────────────────────────────────────────────────────────────────────


@router.get("/audit", response_model=list[pdto.PlatformAuditEventDTO])
async def audit_log(
    tenant_id: str | None = Query(None, alias="tenantId", pattern=r"^[0-9a-f-]{36}$"),
    limit: int = Query(200, ge=1, le=1000),
    op: OperatorCtx = Depends(current_operator),
) -> list[pdto.PlatformAuditEventDTO]:
    return await tenants.audit_log(op, tenant_id, limit)


@router.get("/audit/verify", response_model=pdto.PlatformAuditVerifyDTO)
async def verify(op: OperatorCtx = Depends(current_operator)) -> pdto.PlatformAuditVerifyDTO:
    require_platform(op, "audit.view", "verify the platform audit log")
    async with global_tx() as g:
        return pdto.PlatformAuditVerifyDTO.model_validate(await verify_platform_chain(g))


__all__ = ["CAPABILITIES", "CSRF_EXEMPT", "PUBLIC", "router"]
