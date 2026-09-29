"""Sign-in (Keycloak SSO; demo picker in development), the current user, workspaces and sessions."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import EmailStr, Field

from command_inbox.auth import oidc
from command_inbox.auth import service as auth
from command_inbox.auth.device import device_label
from command_inbox.auth.sessions import SESSION_COOKIE, create_session, resolve_session, rotate
from command_inbox.config import settings
from command_inbox.core.context import Ctx
from command_inbox.core.crypto import decrypt, encrypt
from command_inbox.core.errors import AppError
from command_inbox.core.http import current_ctx
from command_inbox.schemas import dto
from command_inbox.schemas.base import CamelModel, Ok

router = APIRouter(prefix="/v1", tags=["auth"])
OIDC_TXN_COOKIE = "ci_oidc"
OIDC_TXN_MAX_AGE = 600


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_ttl_hours * 3600,
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


class LoginBody(CamelModel):
    email: EmailStr


class SwitchOrgBody(CamelModel):
    org_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class DemoInfo(CamelModel):
    demo_mode: bool
    sso: bool
    users: list[dto.DemoUserDTO]
    orgs: list[dto.OrgChoiceDTO]


@router.get("/auth/demo", response_model=DemoInfo)
async def demo_info() -> DemoInfo:
    if not settings.demo_mode:
        # Outside demo mode, anonymous callers learn nothing about which tenants exist.
        return DemoInfo(demo_mode=False, sso=settings.oidc_enabled, users=[], orgs=[])
    return DemoInfo(
        demo_mode=True,
        sso=settings.oidc_enabled,
        users=await auth.demo_users(),
        orgs=await auth.org_choices("p.sharma@bank.example"),
    )


@router.post("/auth/login", response_model=dto.MeDTO)
async def demo_login(body: LoginBody, request: Request, response: Response) -> dto.MeDTO:
    user = await auth.demo_user(str(body.email))
    org_id = await auth.first_org_for(user.id)
    if org_id is None:
        raise AppError(401, "unauthenticated", "No workspace access for that email. Ask your administrator.")
    ua = request.headers.get("user-agent", "")
    token, csrf = await create_session(user.id, org_id, user_agent=ua, ip=_client_ip(request))
    await auth.record_sign_in(user, org_id, device_label(ua), "demo")
    _set_session_cookie(response, token)
    resolved = await resolve_session(token, request.state.request_id)
    assert resolved is not None
    return await auth.build_me(resolved.ctx, csrf)


@router.get("/auth/oidc/login")
async def oidc_login(
    next: str = Query("/inbox", max_length=300), org: str | None = Query(None, max_length=80)
) -> Response:
    url, txn = await oidc.authorization_url(next, org)
    txn["iat"] = str(int(time.time()))
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        OIDC_TXN_COOKIE,
        encrypt(json.dumps(txn), purpose="oidc-transaction"),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/v1/auth/oidc",
        max_age=OIDC_TXN_MAX_AGE,
    )
    return resp


@router.get("/auth/oidc/callback")
async def oidc_callback(
    request: Request, code: str = Query(..., max_length=4096), state: str = Query(..., max_length=200)
) -> Response:
    fail = RedirectResponse(settings.web_origin.rstrip("/") + "/?error=sso", status_code=302)
    sealed = request.cookies.get(OIDC_TXN_COOKIE)
    if not sealed:
        return fail
    try:
        txn = json.loads(decrypt(sealed, purpose="oidc-transaction"))
    except Exception:
        return fail
    if txn.get("state") != state or time.time() - int(txn.get("iat", "0")) > OIDC_TXN_MAX_AGE:
        return fail
    try:
        claims = await oidc.exchange(code, txn)
        user, org_id = await oidc.admit(claims)
    except AppError as err:
        return RedirectResponse(f"{settings.web_origin.rstrip('/')}/?error={err.code}", status_code=302)
    ua = request.headers.get("user-agent", "")
    token, _csrf = await create_session(user.id, org_id, user_agent=ua, ip=_client_ip(request))
    await auth.record_sign_in(user, org_id, device_label(ua), "sso")
    resp = RedirectResponse(settings.web_origin.rstrip("/") + txn["next"], status_code=302)
    resp.delete_cookie(OIDC_TXN_COOKIE, path="/v1/auth/oidc")
    _set_session_cookie(resp, token)
    return resp


class LogoutResult(Ok):
    logout_url: str | None = None


@router.post("/auth/logout", response_model=LogoutResult)
async def logout(response: Response, ctx: Ctx = Depends(current_ctx)) -> LogoutResult:
    await auth.logout(ctx)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return LogoutResult(logout_url=await oidc.end_session_url())


@router.get("/me", response_model=dto.MeDTO)
async def me(request: Request, ctx: Ctx = Depends(current_ctx)) -> dto.MeDTO:
    return await auth.build_me(ctx, request.state.csrf_token)


@router.post("/session/org", response_model=Ok)
async def switch_org(body: SwitchOrgBody, response: Response, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await auth.switch_org(ctx, body.org_id)
    token, _csrf = await rotate(ctx.session_id)  # a new identity context gets new credentials
    _set_session_cookie(response, token)
    return Ok()


@router.get("/sessions", response_model=list[dto.SessionDTO])
async def sessions(ctx: Ctx = Depends(current_ctx)) -> list[dto.SessionDTO]:
    return await auth.list_sessions(ctx)


@router.delete("/sessions/{session_id}", response_model=Ok)
async def revoke(session_id: str, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await auth.revoke_session(ctx, session_id)
    return Ok()


class Revoked(CamelModel):
    revoked: int


@router.post("/sessions/revoke-others", response_model=Revoked)
async def revoke_others(ctx: Ctx = Depends(current_ctx)) -> Revoked:
    return Revoked(revoked=await auth.revoke_other_sessions(ctx))
