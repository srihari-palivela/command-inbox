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
