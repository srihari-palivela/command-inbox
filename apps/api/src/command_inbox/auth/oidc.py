"""Single sign-on through Keycloak (OIDC Authorization Code + PKCE), backend-for-frontend style.

The API is the OIDC client: the browser never sees an access or ID token. After the code exchange we
validate the ID token (issuer, audience, signature via JWKS, nonce), link the identity to a user, admit
them only through an existing membership or a pending invitation, and issue our own session cookie.

Keycloak federates each bank's own identity provider (Entra ID, Google Workspace, SAML) as a brokered IdP,
so the bank keeps MFA and joiner/mover/leaver control.
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
from sqlalchemy import select, update

from command_inbox.config import settings
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import Actor
from command_inbox.core.errors import AppError, forbidden, unauthorized
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Invitation, Membership, Org, User

_metadata: dict[str, Any] | None = None
_jwks: tuple[float, Any] | None = None


async def metadata() -> dict[str, Any]:
    global _metadata
    if _metadata is None:
        if not settings.oidc_issuer:
            raise AppError(503, "sso_not_configured", "Single sign-on is not configured.")
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration")
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
    return settings.public_api_url.rstrip("/") + "/v1/auth/oidc/callback"


async def idp_for_email(email: str) -> str | None:
    """Home-realm discovery: the brokered IdP alias of the tenant that owns this email's domain, if exactly one."""
    domain = email.strip().lower().rsplit("@", 1)[-1]
    if not domain:
        return None
    async with global_tx() as g:
        orgs = (await g.execute(select(Org).where(Org.sso_idp_alias.is_not(None)))).scalars().all()
    aliases = {o.sso_idp_alias for o in orgs if domain in [d.lower() for d in (o.sso_email_domains or [])]}
    return aliases.pop() if len(aliases) == 1 else None


async def authorization_url(
    next_path: str, org_hint: str | None, login_hint: str | None = None, invite: str | None = None
) -> tuple[str, dict[str, str]]:
    """Returns the IdP URL and the transaction values to keep in a short-lived signed cookie.

    With a `login_hint` (the email typed on the sign-in page) Keycloak skips its own login form and goes
    straight to the tenant's identity provider when the domain is known; otherwise it shows its form with the
    email prefilled. Either way the answer looks the same to the caller, so it reveals no tenant.
    """
    meta = await metadata()
    state, nonce, verifier = generate_token(32), generate_token(32), generate_token(64)
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri(),
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": create_s256_code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if org_hint:
        params["organization"] = org_hint
    if login_hint:
        params["login_hint"] = login_hint
        if idp := await idp_for_email(login_hint):
            params["kc_idp_hint"] = idp
    safe_next = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/inbox"
    txn = {"state": state, "nonce": nonce, "verifier": verifier, "next": safe_next}
    if invite:
        txn["invite"] = invite  # kept in the sealed, short-lived transaction cookie
    return meta["authorization_endpoint"] + "?" + urlencode(params), txn


async def exchange(code: str, txn: dict[str, str]) -> dict[str, Any]:
    """Code → tokens → validated ID-token claims."""
    meta = await metadata()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": settings.oidc_client_id,
        "code_verifier": txn["verifier"],
    }
    auth = (settings.oidc_client_id, settings.oidc_client_secret) if settings.oidc_client_secret else None
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
        aud={"essential": True, "value": settings.oidc_client_id},
        exp={"essential": True},
        nonce={"essential": True, "value": txn["nonce"]},
    ).validate(token.claims)
    claims = token.claims
    if not claims.get("email") or claims.get("email_verified") is not True:
        raise forbidden("Your account has no verified email address.", "email_unverified")
    return dict(claims)


def _trusts(org: Org, claims: dict[str, Any]) -> bool:
    """A tenant trusts a sign-in only from its pinned identity provider, or from its own email domains."""
    email = str(claims.get("email", "")).lower()
    domain = email.rsplit("@", 1)[-1]
    idp = claims.get("identity_provider")
    if org.sso_idp_alias:
        return idp == org.sso_idp_alias
    return domain in [d.lower() for d in (org.sso_email_domains or [])]


async def _admit_invitee(
    claims: dict[str, Any], subject: str, email: str, name: str, token: str
) -> tuple[User, str]:
    """An emailed invitation link is the proof: the IdP's verified email must be the invited address."""
    from command_inbox.auth import invitations

    found = invitations.usable(await invitations.lookup(token))
    if found.invitation.email != email:
        raise forbidden(
            "This invitation was sent to a different email address. Sign in with that account.",
            "invite_email_mismatch",
        )
    async with global_tx() as g:
        user = (await g.execute(select(User).where(User.idp_subject == subject))).scalar_one_or_none()
        if user is None:
            user = (await g.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is not None and user.idp_subject and user.idp_subject != subject:
                raise forbidden("This email is linked to a different sign-in identity.", "identity_conflict")
        if user is None:
            user = User(email=email, name=name, initials=invitations.initials_of(name), idp_subject=subject)
            g.add(user)
        elif not user.idp_subject:
            user.idp_subject = subject
        await g.flush()
        g.expunge(user)
    await invitations.complete(found, user, idp=claims.get("identity_provider"))
    return user, found.org.id


async def admit(claims: dict[str, Any], invite_token: str | None = None) -> tuple[User, str]:
    """Link the identity to a user and pick the workspace. Only members or invitees get in, and only through an
    identity provider their tenant trusts: one bank's IdP asserting another bank's email gets nowhere."""
    meta = await metadata()
    subject = f"{meta['issuer']}|{claims['sub']}"  # subjects are unique per issuer, not globally
    email = str(claims["email"]).lower()
    name = str(claims.get("name") or email.split("@")[0])
    now = clock.now()
    if invite_token:
        return await _admit_invitee(claims, subject, email, name, invite_token)
    async with global_tx() as g:
        user = (await g.execute(select(User).where(User.idp_subject == subject))).scalar_one_or_none()
        linked = user is not None
        if user is None:
            user = (await g.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is not None and user.idp_subject and user.idp_subject != subject:
                raise forbidden("This email is linked to a different sign-in identity.", "identity_conflict")
        member_orgs = (
            []
            if user is None
            else (
                await g.execute(
                    select(Org)
                    .join(Membership, Membership.org_id == Org.id)
                    .where(Membership.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        all_orgs = (await g.execute(select(Org))).scalars().all()

    if user is not None and not linked and member_orgs and not any(_trusts(o, claims) for o in member_orgs):
        # First SSO sign-in for an existing account must come from an IdP one of its tenants trusts.
        raise forbidden("Your organisation's sign-in is not trusted for this account.", "idp_untrusted")
    trusted_members = [o for o in member_orgs if linked or _trusts(o, claims)]

    invitation = None
    if not trusted_members:
        for org in all_orgs:
            if not _trusts(org, claims):
                continue
            async with tenant_tx(org.id) as tx:
                invitation = (
                    await tx.execute(
                        select(Invitation).where(
                            Invitation.email == email,
                            Invitation.accepted_at.is_(None),
                            Invitation.revoked_at.is_(None),
                            Invitation.expires_at > now,
                        )
                    )
                ).scalar_one_or_none()
            if invitation is not None:
                break
        if invitation is None:
            raise forbidden(
                "You don't have access to any workspace yet. Ask your administrator for an invitation.",
                "no_membership",
            )

    initials = "".join(p[0] for p in name.replace(".", " ").split()[:2]).upper() or "?"
    async with global_tx() as g:
        if user is None:
            user = User(email=email, name=name, initials=initials, idp_subject=subject)
            g.add(user)
            await g.flush()
        elif not user.idp_subject:
            await g.execute(update(User).where(User.id == user.id).values(idp_subject=subject))
        if invitation is not None:
            g.add(Membership(org_id=invitation.org_id, user_id=user.id, role=invitation.role))
    if invitation is not None:
        async with tenant_tx(invitation.org_id) as tx:
            await tx.execute(update(Invitation).where(Invitation.id == invitation.id).values(accepted_at=now))
            await audit(
                tx,
                invitation.org_id,
                actor=Actor("user", user.id, user.name, user.initials),
                action="invitation.accepted",
                entity="membership",
                entity_id=user.id,
                summary=f"{user.name} joined as {invitation.role}",
                data={"role": invitation.role, "idp": claims.get("identity_provider")},
            )
        return user, invitation.org_id
    if not linked:
        org = trusted_members[0]
        async with tenant_tx(org.id) as tx:
            await audit(
                tx,
                org.id,
                actor=Actor("user", user.id, user.name, user.initials),
                action="identity.linked",
                entity="user",
                entity_id=user.id,
                summary=f"{user.name} linked single sign-on",
                data={"idp": claims.get("identity_provider")},
            )
    return user, trusted_members[0].id


async def end_session_url() -> str | None:
    if not settings.oidc_enabled:
        return None
    meta = await metadata()
    ep = meta.get("end_session_endpoint")
    if not ep:
        return None
    return (
        ep
        + "?"
        + urlencode({"client_id": settings.oidc_client_id, "post_logout_redirect_uri": settings.web_origin})
    )
