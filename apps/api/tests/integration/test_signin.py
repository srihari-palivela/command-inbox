"""Sign-in: home-realm discovery from the email typed on the sign-in page."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from tests.integration.admin_support import admin_conn


async def test_login_hint_routes_to_the_tenants_identity_provider(anon, monkeypatch):
    from command_inbox.auth import oidc

    async def fake_metadata():
        return {"issuer": "https://idp.test/realms/ci", "authorization_endpoint": "https://idp.test/auth"}

    monkeypatch.setattr(oidc, "metadata", fake_metadata)
    conn = await admin_conn()
    try:
        before = await conn.fetchrow(
            "select sso_idp_alias, sso_email_domains from orgs where slug = 'northwind'"
        )
        await conn.execute(
            "update orgs set sso_idp_alias = 'northwind-entra', sso_email_domains = '[\"NorthWind.test\"]'::jsonb "
            "where slug = 'northwind'"
        )
        try:

            async def params(email: str) -> dict[str, list[str]]:
                r = await anon.get(
                    "/v1/auth/oidc/login", params={"login_hint": email}, follow_redirects=False
                )
                assert r.status_code == 302
                return parse_qs(urlparse(r.headers["location"]).query)

            known = await params("ops@northwind.test")
            assert known["kc_idp_hint"] == ["northwind-entra"]
            assert known["login_hint"] == ["ops@northwind.test"]
            # An unknown domain still redirects (to the Keycloak form), so the answer reveals no tenant.
            unknown = await params("someone@elsewhere.test")
            assert "kc_idp_hint" not in unknown and unknown["login_hint"] == ["someone@elsewhere.test"]
            r = await anon.get(
                "/v1/auth/oidc/login", params={"login_hint": "not-an-email"}, follow_redirects=False
            )
            assert r.status_code == 400
        finally:
            await conn.execute(
                "update orgs set sso_idp_alias = $1, sso_email_domains = $2::jsonb where slug = 'northwind'",
                before["sso_idp_alias"],
                before["sso_email_domains"],
            )
    finally:
        await conn.close()
