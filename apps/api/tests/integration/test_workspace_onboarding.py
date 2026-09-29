"""The bank admin's side of onboarding: organisation profile, single sign-on, and the computed checklist."""

from __future__ import annotations

import pytest

from tests.integration.admin_support import add_member


@pytest.fixture(scope="module")
async def nw_admin(app):
    return await add_member(app, "northwind", "admin")


async def test_only_admins_manage_the_workspace(app, staff, lead, nw_admin):
    assert (await staff.get("/v1/workspace/profile")).status_code == 403
    assert (await lead.get("/v1/onboarding")).status_code == 403
    assert "workspace.manage" in nw_admin.me["capabilities"]
    assert nw_admin.me["org"]["status"] == "live"


async def test_profile_updates_are_validated_and_audited(nw_admin):
    body = {
        "legalName": "Northwind Cooperative Ltd",
        "supportEmail": "help@northwind.test",
        "locale": "en-GB",
        "currency": "GBP",
        "timeZone": "Mars/Olympus",
    }
    r = await nw_admin.send("PUT", "/v1/workspace/profile", body)
    assert r.status_code == 422 and r.json()["code"] == "bad_time_zone"
    r = await nw_admin.send("PUT", "/v1/workspace/profile", {**body, "timeZone": "Europe/London"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["legalName"] == "Northwind Cooperative Ltd" and p["currency"] == "GBP"
    me = (await nw_admin.get("/v1/me")).json()
    assert me["org"]["timeZone"] == "Europe/London"


async def test_sso_secret_is_sealed_and_never_returned(nw_admin, monkeypatch):
    body = {
        "provider": "entra",
        "directoryId": "0f1e2d3c-aaaa-bbbb-cccc-1234567890ab",
        "clientId": "app-client-id",
    }
    r = await nw_admin.send("PUT", "/v1/workspace/sso", body)
    assert r.status_code == 422 and r.json()["code"] == "secret_required"

    r = await nw_admin.send("PUT", "/v1/workspace/sso", {**body, "clientSecret": "s3cret-value-123"})
    sso = r.json()["sso"]
    assert r.status_code == 200 and sso["state"] == "saved" and sso["hasSecret"] is True
    assert "s3cret" not in r.text and sso["idpAlias"] is None  # not applied without the Keycloak admin API

    from command_inbox.platform import keycloak

    seen = {}

    async def applied(**kw):
        seen.update(kw)
        return keycloak.OrgResult("connected", "live")

    monkeypatch.setattr(keycloak, "ensure_identity_provider", applied)
    r = await nw_admin.send("PUT", "/v1/workspace/sso", body)  # the stored secret is reused
    sso = r.json()["sso"]
    assert sso["state"] == "connected" and sso["idpAlias"] == "northwind-entra"
    assert seen["secret"] == "s3cret-value-123" and seen["directory_id"] == body["directoryId"]

    from tests.integration.admin_support import admin_conn

    conn = await admin_conn()
    try:
        raw = await conn.fetchval("select sso_config::text from orgs where slug = 'northwind'")
        await conn.execute(
            "update orgs set sso_idp_alias = null, sso_config = '{}'::jsonb where slug = 'northwind'"
        )
    finally:
        await conn.close()
    assert "s3cret" not in raw and '"secretSealed": "t1.' in raw


async def test_checklist_is_computed_from_real_data(nw_admin):
    r = await nw_admin.get("/v1/onboarding")
    assert r.status_code == 200
    ob = r.json()
    steps = {s["key"]: s for s in ob["steps"]}
    assert list(steps)[:4] == ["profile", "sso", "people", "mailbox"]
    assert steps["actions"]["state"] == "later"
    assert ob["total"] == len(ob["steps"]) - 1
    assert steps["go_live"]["state"] == "done"  # northwind is a live demo tenant
    assert ob["done"] == sum(1 for s in ob["steps"] if s["state"] == "done")
