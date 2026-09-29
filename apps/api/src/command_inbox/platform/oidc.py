"""Operator single sign-on: OIDC Authorization Code + PKCE against the operators' Keycloak realm.

Operators are not tenant users: a separate realm (vendor staff, vendor MFA policy), a separate client and a
separate session cookie. Only operators created beforehand (by a platform owner, or the bootstrap CLI) get in;
the first SSO sign-in links the identity by email, later ones by issuer + subject.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.common.security import generate_token
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from joserfc import jwt
from joserfc.jwk import KeySet
from sqlalchemy import select

from command_inbox.config import settings
from command_inbox.core.errors import AppError, forbidden, unauthorized
from command_inbox.db.engine import global_tx
from command_inbox.db.models import PlatformOperator

_metadata: dict[str, Any] | None = None
_jwks: tuple[float, Any] | None = None


async def metadata() -> dict[str, Any]:
    global _metadata
    if _metadata is None:
        if not settings.platform_oidc_issuer:
            raise AppError(503, "sso_not_configured", "Operator single sign-on is not configured.")
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                settings.platform_oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
            )
            r.raise_for_status()
            _metadata = r.json()
    return _metadata


async def _keys() -> Any:
    global _jwks
    if _jwks is None or time.monotonic() - _jwks[0] > 600:
        meta = await metadata()
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(meta["jwks_uri"])
            r.raise_for_status()
        _jwks = (time.monotonic(), KeySet.import_key_set(r.json()))
    return _jwks[1]


def redirect_uri() -> str:
    return settings.public_api_url.rstrip("/") + "/v1/platform/auth/oidc/callback"


async def authorization_url(next_path: str) -> tuple[str, dict[str, str]]:
    meta = await metadata()
    state, nonce, verifier = generate_token(32), generate_token(32), generate_token(64)
    params = {
        "response_type": "code",
        "client_id": settings.platform_oidc_client_id,
        "redirect_uri": redirect_uri(),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": create_s256_code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if settings.operator_mfa_required:
        params["acr_values"] = settings.platform_mfa_acr[0]  # ask for the second-factor level up front
    safe_next = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    return meta["authorization_endpoint"] + "?" + urlencode(params), {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "next": safe_next,
    }


async def exchange(code: str, txn: dict[str, str]) -> dict[str, Any]:
    meta = await metadata()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": settings.platform_oidc_client_id,
        "code_verifier": txn["verifier"],
    }
    secret = settings.platform_oidc_client_secret
    auth = (settings.platform_oidc_client_id, secret) if secret else None
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(meta["token_endpoint"], data=data, auth=auth)
    if r.status_code != 200:
        raise unauthorized("Sign-in could not be completed. Try again.")
    id_token = r.json().get("id_token")
    if not id_token:
        raise unauthorized("The identity provider returned no ID token.")
    token = jwt.decode(id_token, await _keys(), algorithms=["RS256", "ES256", "PS256"])
    jwt.JWTClaimsRegistry(
        leeway=30,
        iss={"essential": True, "value": meta["issuer"]},
        aud={"essential": True, "value": settings.platform_oidc_client_id},
        exp={"essential": True},
        nonce={"essential": True, "value": txn["nonce"]},
    ).validate(token.claims)
    claims = dict(token.claims)
    if not claims.get("email") or claims.get("email_verified") is not True:
        raise forbidden("Your account has no verified email address.", "email_unverified")
    if settings.operator_mfa_required and not has_second_factor(claims):
        raise forbidden(
            "The console needs a second sign-in factor. Set one up and sign in again.", "mfa_required"
        )
    return claims


SECOND_FACTORS = frozenset({"otp", "mfa", "hwk", "swk", "pop", "fido", "webauthn", "sms"})


def has_second_factor(claims: dict[str, Any]) -> bool:
    """Whether the ID token says the operator used a second factor (`acr` level or `amr` method)."""
    if str(claims.get("acr", "")) in settings.platform_mfa_acr:
        return True
    amr = claims.get("amr") or []
    return bool(
        SECOND_FACTORS.intersection(str(m).lower() for m in (amr if isinstance(amr, list) else [amr]))
    )


async def admit(claims: dict[str, Any]) -> PlatformOperator:
    meta = await metadata()
    subject = f"{meta['issuer']}|{claims['sub']}"
    email = str(claims["email"]).lower()
    async with global_tx() as g:
        op = (
            await g.execute(select(PlatformOperator).where(PlatformOperator.idp_subject == subject))
        ).scalar_one_or_none()
        if op is None:
            op = (
                await g.execute(select(PlatformOperator).where(PlatformOperator.email == email))
            ).scalar_one_or_none()
            if op is None:
                raise forbidden("You are not a platform operator.", "not_an_operator")
            if op.idp_subject and op.idp_subject != subject:
                raise forbidden("This email is linked to a different sign-in identity.", "identity_conflict")
            op.idp_subject = subject
        if op.disabled_at is not None:
            raise forbidden("Your operator account is disabled.", "operator_disabled")
        await g.flush()
        g.expunge(op)
    return op
