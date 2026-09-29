"""SCIM 2.0 provisioning the way Microsoft Entra ID drives it: users, groups, roles from groups, offboarding."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from tests.integration.admin_support import add_member, audit_actions, last_seq, org_id

pytestmark = pytest.mark.integration


class Scim:
    def __init__(self, app: Any, token: str) -> None:
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/scim/v2")
        self.h = {"authorization": f"Bearer {token}", "content-type": "application/scim+json"}

    async def req(self, method: str, path: str, body: Any = None) -> httpx.Response:
        return await self.http.request(method, path, json=body, headers=self.h)


@pytest.fixture
async def scim(app) -> tuple[Any, Scim, str]:
    admin = await add_member(app, "meridian", "admin")
    r = await admin.send("POST", "/v1/workspace/scim/token", {})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"].startswith("scim_") and body["settings"]["enabled"] is True
    return admin, Scim(app, body["token"]), await org_id("meridian")


async def test_entra_provisions_a_person_and_offboards_them(app, scim):
    admin, s, oid = scim
    seq = await last_seq(oid)
    email = f"asha.{uuid.uuid4().hex[:6]}@meridian.example"
    # Entra first looks the person up, then creates them.
    r = await s.req("GET", f'/Users?filter=userName eq "{email}"')
    assert r.status_code == 200 and r.json()["totalResults"] == 0
    r = await s.req(
        "POST",
        "/Users",
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": email,
            "externalId": "entra-oid-1",
            "name": {"givenName": "Asha", "familyName": "Rao"},
            "emails": [{"value": email, "type": "work", "primary": True}],
            "active": True,
        },
    )
    assert r.status_code == 201, r.text
    assert r.headers["content-type"].startswith("application/scim+json")
    user = r.json()
    assert user["userName"] == email and user["active"] is True and user["externalId"] == "entra-oid-1"
    assert (await s.req("POST", "/Users", {"userName": email})).status_code == 409

    members = (await admin.get("/v1/members")).json()
    me = next(m for m in members if m["user"]["id"] == user["id"])
    assert me["role"] == "staff"

    # Rename, then disable (Entra's soft delete): the membership goes and sessions are revoked.
    r = await s.req(
        "PATCH",
        f"/Users/{user['id']}",
        {"Operations": [{"op": "replace", "path": "displayName", "value": "Asha R. Rao"}]},
    )
    assert r.status_code == 200 and r.json()["displayName"] == "Asha R. Rao"
    r = await s.req(
        "PATCH", f"/Users/{user['id']}", {"Operations": [{"op": "replace", "value": {"active": False}}]}
    )
    assert r.status_code == 200 and r.json()["active"] is False
    assert (await s.req("GET", f"/Users/{user['id']}")).status_code == 404
    actions = [a["action"] for a in await audit_actions(oid, seq)]
    assert "member.provisioned" in actions and "member.deprovisioned" in actions


async def test_groups_set_roles_but_never_remove_the_last_admin(app, scim):
    admin, s, oid = scim
    r = await admin.send("PUT", "/v1/workspace/scim/roles", {"groupRoles": {"CI-Leads": "lead"}})
    assert r.status_code == 200, r.text
    email = f"lead.{uuid.uuid4().hex[:6]}@meridian.example"
    u = (await s.req("POST", "/Users", {"userName": email, "displayName": "Lead Person"})).json()
    g = await s.req("POST", "/Groups", {"displayName": "CI-Leads", "members": [{"value": u["id"]}]})
    assert g.status_code == 201, g.text
    group = g.json()
    role = lambda ms: next(m for m in ms if m["user"]["id"] == u["id"])["role"]  # noqa: E731
    assert role((await admin.get("/v1/members")).json()) == "lead"

    r = await s.req(
        "PATCH",
        f"/Groups/{group['id']}",
        {"Operations": [{"op": "remove", "path": f'members[value eq "{u["id"]}"]'}]},
    )
    assert r.status_code == 200 and r.json()["members"] == []
    assert role((await admin.get("/v1/members")).json()) == "staff"

    settings = (await admin.get("/v1/workspace/scim")).json()
    assert {"name": "CI-Leads", "members": 0} in settings["groups"] and settings["provisionedMembers"] >= 1
    assert (await s.req("DELETE", f"/Groups/{group['id']}")).status_code == 204
    await admin.send("PUT", "/v1/workspace/scim/roles", {"groupRoles": {}})


async def test_tokens_are_checked_and_can_be_revoked(app, scim):
    admin, s, _ = scim
    bad = Scim(app, "scim_wrong")
    r = await bad.req("GET", "/Users")
    assert r.status_code == 401 and r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert (await s.req("GET", "/ServiceProviderConfig")).json()["patch"]["supported"] is True
    r = await admin.send("DELETE", "/v1/workspace/scim/token")
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await s.req("GET", "/Users")).status_code == 401
    staff = await add_member(app, "meridian", "staff")
    assert (await staff.send("POST", "/v1/workspace/scim/token", {})).status_code == 403
