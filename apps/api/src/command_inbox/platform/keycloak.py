"""Keycloak admin adapter: one Keycloak Organization per tenant, created at provisioning.

The organization (alias = tenant slug, with the bank's email domains) is what Keycloak uses to route a
bank's people to the bank's own identity provider. Keycloak 26+ Organizations API:
`POST /admin/realms/{realm}/organizations`. Auth is a confidential client with the service-account role
`realm-management/manage-realm` (client credentials grant).

Not configured (no KEYCLOAK_ADMIN_URL): provisioning records the step as skipped, and the tenant signs in
through email-domain matching until an operator connects it.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from command_inbox.config import settings


@dataclass(frozen=True, slots=True)
class OrgResult:
    state: str  # "done" | "skipped"
    detail: str
    org_id: str | None = None


def configured() -> bool:
    return bool(settings.keycloak_admin_url and settings.keycloak_admin_client_secret)


async def _token(client: httpx.AsyncClient) -> str:
    base = settings.keycloak_admin_url.rstrip("/")  # type: ignore[union-attr]
    r = await client.post(
        f"{base}/realms/{settings.keycloak_realm}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.keycloak_admin_client_id,
            "client_secret": settings.keycloak_admin_client_secret,
        },
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


async def ensure_organization(alias: str, name: str, domains: list[str]) -> OrgResult:
    """Idempotent: finds the organization by alias, creates it if missing."""
    if not configured():
        return OrgResult(
            "skipped", "Keycloak admin API is not configured; sign-in uses email-domain matching."
        )
    base = settings.keycloak_admin_url.rstrip("/")  # type: ignore[union-attr]
    orgs = f"{base}/admin/realms/{settings.keycloak_realm}/organizations"
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"authorization": f"Bearer {await _token(client)}"}
        found = await client.get(orgs, params={"search": alias, "exact": "true"}, headers=headers)
        found.raise_for_status()
        for o in found.json():
            if o.get("alias") == alias:
                return OrgResult("done", f"Keycloak organization {alias!r} already exists.", o.get("id"))
        body = {
            "name": name,
            "alias": alias,
            "enabled": True,
            "domains": [{"name": d, "verified": False} for d in domains],
        }
        created = await client.post(orgs, json=body, headers=headers)
        created.raise_for_status()
        org_id = created.headers.get("location", "").rsplit("/", 1)[-1] or None
    return OrgResult("done", f"Created Keycloak organization {alias!r}.", org_id)


def broker_redirect_uri(alias: str) -> str | None:
    """What the bank registers in its Entra app or Google OAuth client: Keycloak's broker endpoint."""
    if not settings.oidc_issuer:
        return None
    return f"{settings.oidc_issuer.rstrip('/')}/broker/{alias}/endpoint"


def _idp_body(
    alias: str, display: str, provider: str, directory_id: str, client_id: str, secret: str
) -> dict:
    if provider == "google":
        return {
            "alias": alias,
            "displayName": display,
            "providerId": "google",
            "enabled": True,
            "trustEmail": True,
            "config": {
                "clientId": client_id,
                "clientSecret": secret,
                "hostedDomain": directory_id,
                "defaultScope": "openid email profile",
                "syncMode": "IMPORT",
            },
        }
    base = f"https://login.microsoftonline.com/{directory_id}"
    return {
        "alias": alias,
        "displayName": display,
        "providerId": "oidc",
        "enabled": True,
        "trustEmail": True,
        "config": {
            "clientId": client_id,
            "clientSecret": secret,
            "issuer": f"{base}/v2.0",
            "authorizationUrl": f"{base}/oauth2/v2.0/authorize",
            "tokenUrl": f"{base}/oauth2/v2.0/token",
            "jwksUrl": f"{base}/discovery/v2.0/keys",
            "useJwksUrl": "true",
            "validateSignature": "true",
            "pkceEnabled": "true",
            "pkceMethod": "S256",
            "clientAuthMethod": "client_secret_post",
            "defaultScope": "openid email profile",
            "syncMode": "IMPORT",
        },
    }


async def ensure_identity_provider(
    *, org_alias: str, alias: str, display: str, provider: str, directory_id: str, client_id: str, secret: str
) -> OrgResult:
    """Create or update the tenant's brokered identity provider and link it to the tenant's organization."""
    if not configured():
        return OrgResult(
            "saved", "Saved. It takes effect once the Keycloak admin API is configured for this stack."
        )
    base = settings.keycloak_admin_url.rstrip("/")  # type: ignore[union-attr]
    realm = f"{base}/admin/realms/{settings.keycloak_realm}"
    body = _idp_body(alias, display, provider, directory_id, client_id, secret)
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"authorization": f"Bearer {await _token(client)}"}
        existing = await client.get(f"{realm}/identity-provider/instances/{alias}", headers=headers)
        if existing.status_code == 200:
            r = await client.put(f"{realm}/identity-provider/instances/{alias}", json=body, headers=headers)
        else:
            r = await client.post(f"{realm}/identity-provider/instances", json=body, headers=headers)
        r.raise_for_status()
        orgs = await client.get(
            f"{realm}/organizations", params={"search": org_alias, "exact": "true"}, headers=headers
        )
        orgs.raise_for_status()
        org = next((o for o in orgs.json() if o.get("alias") == org_alias), None)
        if org is None:
            return OrgResult("failed", "The tenant's Keycloak organization is missing; re-run provisioning.")
        linked = await client.post(
            f"{realm}/organizations/{org['id']}/identity-providers",
            content=alias,
            headers={**headers, "content-type": "application/json"},
        )
        if linked.status_code not in (204, 409):
            linked.raise_for_status()
    return OrgResult("connected", f"Identity provider {alias!r} is live and linked to the organization.")


async def ensure_bootstrap_user(*, email: str, name: str, redirect_uri: str) -> OrgResult:
    """A local Keycloak account for a tenant's first admin, before the bank's own IdP is connected.

    The emailed invitation already proved the address, so the account is created with a verified email and
    the required actions "set a password" and "set up an authenticator app"; Keycloak emails the link that
    runs them (the realm's SMTP settings), then returns the person to `redirect_uri`. The account carries
    `bootstrap=true` so it can be retired once the bank's SSO is live.
    """
    if not configured():
        return OrgResult("skipped", "The Keycloak admin API is not configured for this stack.")
    base = settings.keycloak_admin_url.rstrip("/")  # type: ignore[union-attr]
    realm = f"{base}/admin/realms/{settings.keycloak_realm}"
    first, _, last = name.partition(" ")
    async with httpx.AsyncClient(timeout=10) as client:
        headers = {"authorization": f"Bearer {await _token(client)}"}
        found = await client.get(f"{realm}/users", params={"email": email, "exact": "true"}, headers=headers)
        found.raise_for_status()
        users = found.json()
        if users:
            user_id = users[0]["id"]
            if (users[0].get("attributes") or {}).get("bootstrap") != ["true"]:
                return OrgResult("exists", "An account with this email already exists; sign in with it.")
        else:
            created = await client.post(
                f"{realm}/users",
                json={
                    "username": email,
                    "email": email,
                    "emailVerified": True,
                    "enabled": True,
                    "firstName": first or name,
                    "lastName": last,
                    "attributes": {"bootstrap": ["true"]},
                    "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"],
                },
                headers=headers,
            )
            created.raise_for_status()
            user_id = created.headers["location"].rsplit("/", 1)[-1]
        sent = await client.put(
            f"{realm}/users/{user_id}/execute-actions-email",
            params={"client_id": settings.oidc_client_id, "redirect_uri": redirect_uri, "lifespan": 3600},
            json=["UPDATE_PASSWORD", "CONFIGURE_TOTP"],
            headers=headers,
        )
        sent.raise_for_status()
    return OrgResult("done", "We emailed you a link to set your password and authenticator.")
