"""The platform console end to end: an operator creates a bank, provisioning runs, the bank's first admin
accepts the emailed invitation and invites two people, and every step is audited."""

from __future__ import annotations

import re
import uuid
from typing import Any

import asyncpg
import httpx
import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select

from tests.integration.admin_support import admin_conn, drain
from tests.integration.conftest import Client


async def _operator(role: str) -> str:
    from command_inbox.db.engine import global_tx
    from command_inbox.db.models import PlatformOperator

    email = f"{role}.{uuid.uuid4().hex[:6]}@platform.test"
    async with global_tx() as g:
        g.add(PlatformOperator(email=email, name=f"{role.title()} Op", role=role))
    return email


async def _console(app: Any, email: str) -> Client:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await http.post("/v1/platform/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    return Client(http, r.json())


def _tenant_body(slug: str, admin_email: str, **over: Any) -> dict[str, Any]:
    body = {
        "slug": slug,
        "name": "Harbour Bank",
        "legalName": "Harbour Bank plc",
        "region": "uk-south",
        "plan": "Pilot",
        "locale": "en-GB",
        "currency": "GBP",
        "timeZone": "Europe/London",
        "emailDomains": ["Harbour.test"],
        "admin": {"name": "Hana Admin", "email": admin_email},
    }
    body.update(over)
    return body


def _token_for(to: str) -> str:
    from command_inbox.core.email import dev_mailbox

    for m in reversed(dev_mailbox):
        if m["to"] == to:
            found = re.search(r"/accept\?token=([A-Za-z0-9_-]+)", m["text"])
            assert found, m["text"]
            return found.group(1)
    raise AssertionError(f"no email to {to}")


async def _accept(app: Any, token: str) -> Client:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await http.post("/v1/auth/invitation/accept", json={"token": token})
    assert r.status_code == 200, r.text
    return Client(http, r.json())


@pytest.fixture(scope="module")
async def ops(app: Any) -> dict[str, Client]:
    return {
        role: await _console(app, await _operator(role)) for role in ("platform_owner", "operator", "support")
    }


async def test_console_and_workspace_sessions_never_cross(app, ops, admin, anon):
    assert (await anon.get("/v1/platform/tenants")).status_code == 401
    assert (await admin.get("/v1/platform/tenants")).status_code == 401  # a tenant admin is not an operator
    op = ops["operator"]
    assert (await op.get("/v1/platform/me")).json()["operator"]["role"] == "operator"
    assert (await op.get("/v1/inbox")).status_code == 401  # an operator cannot read a bank's mail
    r = await op.http.post("/v1/platform/tenants", json=_tenant_body("nocsrf-bank", "a@x.test"))
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    r = await anon.post("/v1/platform/auth/dev-login", json={"email": "p.sharma@bank.example"})
    assert r.status_code == 403 and r.json()["code"] == "not_an_operator"


async def test_support_is_read_only(ops):
    sup = ops["support"]
    assert (await sup.get("/v1/platform/tenants")).status_code == 200
    r = await sup.send("POST", "/v1/platform/tenants", _tenant_body("support-bank", "a@x.test"))
    assert r.status_code == 403 and r.json()["code"] == "platform_forbidden"


async def test_operator_onboards_a_bank_end_to_end(app, ops):
    op = ops["operator"]
    slug = f"harbour-{uuid.uuid4().hex[:6]}"
    admin_email = f"hana.{slug}@harbour.test"
    r = await op.send("POST", "/v1/platform/tenants", _tenant_body(slug, admin_email))
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "provisioning" and t["emailDomains"] == ["harbour.test"]
    assert [s["state"] for s in t["steps"]] == ["pending"] * 4
    tid = t["id"]
    assert (await op.send("POST", "/v1/platform/tenants", _tenant_body(slug, admin_email))).json()[
        "code"
    ] == "slug_taken"

    await drain()
    t = (await op.get(f"/v1/platform/tenants/{tid}")).json()
    steps = {s["step"]: s for s in t["steps"]}
    assert t["status"] == "provisioned" and t["provisioning"] is None
    assert steps["data_key"]["state"] == "done" and t["keys"][0]["state"] == "active"
    assert steps["identity"]["state"] == "skipped"  # no Keycloak admin API in tests
    assert steps["starter_deployment"]["state"] == "done" and steps["first_admin"]["state"] == "done"
    assert [(i["email"], i["role"], i["state"]) for i in t["invitations"]] == [
        (admin_email, "admin", "pending")
    ]
    assert t["actions"] == ["reinvite", "suspend", "archive"]

    # The starter deployment is live, with no automatic lane.
    from command_inbox.db.engine import tenant_tx
    from command_inbox.db.models import Deployment, DeploymentVersion

    async with tenant_tx(tid) as tx:
        v = (
            await tx.execute(
                select(DeploymentVersion).join(
                    Deployment, Deployment.active_version_id == DeploymentVersion.id
                )
            )
        ).scalar_one()
    assert v.state == "published" and v.config["thresholds"]["autoMinConfidence"] == 1.0
    assert all(c["defaultLane"] != "auto" for c in v.config["taxonomy"]["categories"])
    # Its query types, reply-time targets and model-node agents are written out for the admin to edit.
    from command_inbox.db.models import QueryType, SlaPolicy

    async with tenant_tx(tid) as tx:
        qts = (await tx.execute(select(QueryType))).scalars().all()
        slas = (await tx.execute(select(SlaPolicy))).scalars().all()
    assert len(qts) == len(v.config["taxonomy"]["categories"]) - 1 and all(q.department_id for q in qts)
    assert any(p.priority is None and p.segment is None and not p.escalation for p in slas)
    agents = {n["type"]: n.get("agent") for n in v.config["flow"]["nodes"]}
    assert agents["draft_reply"]["name"] == "Reply Drafter" and agents["mask_pii"] is None

    # Nobody from the bank can sign in before accepting.
    token = _token_for(admin_email)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        preview = (await anon.get("/v1/auth/invitation", params={"token": token})).json()
        assert preview["tenantName"] == "Harbour Bank" and preview["role"] == "admin"
        assert preview["state"] == "pending" and preview["invitedBy"] == "Operator Op (Command Inbox)"
        assert (await anon.get("/v1/auth/invitation", params={"token": "x" * 43})).status_code == 404

    admin = await _accept(app, token)
    assert admin.me["org"]["id"] == tid and admin.me["role"] == "admin"
    assert admin.me["org"]["currency"] == "GBP"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        again = await anon.post("/v1/auth/invitation/accept", json={"token": token})
        assert again.status_code == 409 and again.json()["code"] == "invitation_accepted"

    t = (await op.get(f"/v1/platform/tenants/{tid}")).json()
    assert t["status"] == "onboarding" and t["admins"] == 1
    assert "tenant.onboarding" in [e["action"] for e in t["audit"]]

    # The admin invites two people; each gets an email and joins with the role they were given.
    for role in ("lead", "staff"):
        email = f"{role}.{slug}@harbour.test"
        r = await admin.send("POST", "/v1/invitations", {"email": email, "role": role})
        assert r.status_code == 200, r.text
        await drain()
        person = await _accept(app, _token_for(email))
        assert person.me["org"]["id"] == tid and person.me["role"] == role
    t = (await op.get(f"/v1/platform/tenants/{tid}")).json()
    assert t["members"] == 3 and t["status"] == "onboarding"

    # The admin's checklist reflects the real state of the new workspace.
    ob = (await admin.get("/v1/onboarding")).json()
    steps = {x["key"]: x["state"] for x in ob["steps"]}
    assert ob["status"] == "onboarding"
    assert steps["people"] == "done" and steps["mailbox"] == "not_started" and steps["sso"] == "not_started"
    assert (
        steps["rules"] == "in_progress" and steps["categories"] == "in_progress"
    )  # starter pack, not yet tuned

    # The bank's audit chain shows what the vendor did, and verifies.
    r = await admin.get("/v1/audit/verify")
    assert r.status_code == 200 and r.json()["ok"] is True
    conn = await admin_conn()
    try:
        actions = [
            r["action"]
            for r in await conn.fetch(
                "select action from audit_events where org_id = $1 order by seq", uuid.UUID(tid)
            )
        ]
    finally:
        await conn.close()
    assert actions[:3] == ["tenant.created", "deployment.created", "invitation.created"]
    assert actions.count("invitation.accepted") == 3

    # Suspension locks the bank out at once; resuming restores the status it had.
    r = await op.send("POST", f"/v1/platform/tenants/{tid}/suspend", {"reason": "Contract paused"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert (await admin.get("/v1/me")).status_code == 401
    assert (await op.send("POST", f"/v1/platform/tenants/{tid}/suspend", {"reason": "x"})).json()["code"] == (
        "invalid_transition"
    )
    r = await op.send("POST", f"/v1/platform/tenants/{tid}/resume", {"reason": "Contract signed"})
    assert r.json()["status"] == "onboarding"

    # Both chains verify.
    owner = ops["platform_owner"]
    assert (await owner.get("/v1/platform/audit/verify")).json()["ok"] is True
    log = (await owner.get("/v1/platform/audit", params={"tenantId": tid})).json()
    assert {
        "tenant.created",
        "tenant.provisioned",
        "tenant.onboarding",
        "tenant.suspended",
        "tenant.resumed",
    } <= {e["action"] for e in log}


async def test_reinvite_replaces_a_lost_invitation_and_archive_revokes(ops):
    op = ops["operator"]
    slug = f"quay-{uuid.uuid4().hex[:6]}"
    first = f"first.{slug}@quay.test"
    tid = (
        await op.send("POST", "/v1/platform/tenants", _tenant_body(slug, first, emailDomains=["quay.test"]))
    ).json()["id"]
    await drain()
    old = _token_for(first)
    second = f"second.{slug}@quay.test"
    r = await op.send("POST", f"/v1/platform/tenants/{tid}/invitations", {"name": "Second", "email": second})
    assert r.status_code == 200, r.text
    await drain()
    states = {i["email"]: i["state"] for i in r.json()["invitations"]}
    assert states == {first: "revoked", second: "pending"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=op.http._transport.app), base_url="http://test"
    ) as anon:  # type: ignore[attr-defined]
        assert (await anon.post("/v1/auth/invitation/accept", json={"token": old})).json()[
            "code"
        ] == "invitation_revoked"
    r = await op.send("POST", f"/v1/platform/tenants/{tid}/archive", {"reason": "Pilot cancelled"})
    assert r.json()["status"] == "archived" and r.json()["actions"] == []
    assert {i["state"] for i in r.json()["invitations"]} == {"revoked"}


async def test_draft_tenant_provisions_on_request_and_failed_steps_retry(ops, monkeypatch):
    from command_inbox.platform import keycloak

    op = ops["operator"]
    slug = f"draft-{uuid.uuid4().hex[:6]}"
    r = await op.send("POST", "/v1/platform/tenants", _tenant_body(slug, f"a.{slug}@d.test", provision=False))
    t = r.json()
    assert t["status"] == "draft" and t["actions"] == ["provision", "archive"]

    async def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("Keycloak is down")

    monkeypatch.setattr(keycloak, "ensure_organization", broken)
    r = await op.send("POST", f"/v1/platform/tenants/{t['id']}/provision", None)
    assert r.json()["status"] == "provisioning"
    await drain()
    t = (await op.get(f"/v1/platform/tenants/{t['id']}")).json()
    steps = {s["step"]: s for s in t["steps"]}
    assert steps["data_key"]["state"] == "done" and steps["identity"]["state"] == "failed"
    assert "Keycloak is down" in steps["identity"]["detail"] and t["provisioning"] == "failed"
    assert "provision" in t["actions"]

    monkeypatch.undo()
    await op.send("POST", f"/v1/platform/tenants/{t['id']}/provision", None)
    await drain()
    t = (await op.get(f"/v1/platform/tenants/{t['id']}")).json()
    assert t["status"] == "provisioned"
    assert [s["attempts"] for s in t["steps"]][:2] == [1, 2]  # the done step was not redone


async def test_sso_invitation_binds_to_the_invited_email(ops, monkeypatch):
    from command_inbox.auth import oidc

    op = ops["operator"]
    slug = f"sso-{uuid.uuid4().hex[:6]}"
    invited = f"boss.{slug}@sso.test"
    tid = (
        await op.send("POST", "/v1/platform/tenants", _tenant_body(slug, invited, emailDomains=["sso.test"]))
    ).json()["id"]
    await drain()
    token = _token_for(invited)

    async def fake_metadata():
        return {"issuer": "https://idp.test/realms/ci"}

    monkeypatch.setattr(oidc, "metadata", fake_metadata)
    from command_inbox.core.errors import AppError

    with pytest.raises(AppError) as err:
        await oidc.admit({"sub": "s1", "email": f"other.{slug}@sso.test", "email_verified": True}, token)
    assert err.value.code == "invite_email_mismatch"
    user, org_id = await oidc.admit(
        {"sub": uuid.uuid4().hex, "email": invited, "email_verified": True, "name": "Boss"}, token
    )
    assert org_id == tid and user.email == invited


async def test_tenant_keys_seal_per_tenant_and_crypto_shred(ops):
    from command_inbox.db.engine import global_tx
    from command_inbox.platform.keys import (
        destroy_tenant_keys,
        ensure_tenant_key,
        tenant_decrypt,
        tenant_encrypt,
    )

    op = ops["operator"]
    a = (
        await op.send("POST", "/v1/platform/tenants", _tenant_body(f"ka-{uuid.uuid4().hex[:6]}", "a@ka.test"))
    ).json()["id"]
    b = (
        await op.send("POST", "/v1/platform/tenants", _tenant_body(f"kb-{uuid.uuid4().hex[:6]}", "b@kb.test"))
    ).json()["id"]
    await drain()
    async with global_tx() as g:
        assert (await ensure_tenant_key(g, a)).version == 1  # idempotent: provisioning already made it
        sealed = await tenant_encrypt(g, a, "refresh-token-123", aad="mailbox|1")
        assert await tenant_decrypt(g, a, sealed, aad="mailbox|1") == "refresh-token-123"
        with pytest.raises(InvalidTag):
            await tenant_decrypt(g, b, sealed, aad="mailbox|1")  # another tenant's key cannot open it
        with pytest.raises(InvalidTag):
            await tenant_decrypt(g, a, sealed, aad="mailbox|2")  # bound to its row
        assert await destroy_tenant_keys(g, a) == 1
    async with global_tx() as g:
        from command_inbox.core.errors import AppError

        with pytest.raises(AppError):
            await tenant_decrypt(g, a, sealed, aad="mailbox|1")


async def test_platform_audit_is_append_only(app):
    conn = await admin_conn()
    try:
        await conn.execute("set role ci_app")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("update platform_audit_events set summary = 'edited'")
    finally:
        await conn.close()


async def test_first_admin_gets_a_bootstrap_sign_in_until_bank_sso_is_live(app, ops, monkeypatch, anon):
    from command_inbox.config import settings
    from command_inbox.platform import keycloak

    op = ops["operator"]
    slug = f"boot-{uuid.uuid4().hex[:6]}"
    invited = f"first.{slug}@boot.test"
    await op.send("POST", "/v1/platform/tenants", _tenant_body(slug, invited, emailDomains=["boot.test"]))
    await drain()
    token = _token_for(invited)

    # Without SSO (development) there is nothing to bootstrap.
    assert (await anon.get("/v1/auth/invitation", params={"token": token})).json()["canBootstrap"] is False
    r = await anon.post("/v1/auth/invitation/setup", json={"token": token})
    assert r.status_code == 403 and r.json()["code"] == "bootstrap_unavailable"

    calls: list[dict[str, str]] = []

    async def fake_bootstrap(**kw: str) -> keycloak.OrgResult:
        calls.append(kw)
        return keycloak.OrgResult("done", "We emailed you a link to set your password and authenticator.")

    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.test/realms/ci")
    monkeypatch.setattr(keycloak, "configured", lambda: True)
    monkeypatch.setattr(keycloak, "ensure_bootstrap_user", fake_bootstrap)
    preview = (await anon.get("/v1/auth/invitation", params={"token": token})).json()
    assert preview["signIn"] == "sso" and preview["canBootstrap"] is True
    for _ in range(3):
        r = await anon.post("/v1/auth/invitation/setup", json={"token": token})
        assert r.status_code == 200 and "emailed you a link" in r.json()["message"]
    assert calls[0]["email"] == invited and calls[0]["redirect_uri"].endswith(f"/accept?token={token}")
    r = await anon.post("/v1/auth/invitation/setup", json={"token": token})
    assert r.status_code == 409 and r.json()["code"] == "bootstrap_limit"


async def test_fleet_health_aggregates_every_tenant_without_content(ops):
    r = await ops["support"].get("/v1/platform/fleet")
    assert r.status_code == 200, r.text
    f = r.json()
    slugs = {t["slug"] for t in f["tenants"]}
    assert {"apex", "meridian"} <= slugs
    apex = next(t for t in f["tenants"] if t["slug"] == "apex")
    assert apex["spendCapMinor"] > 0 and apex["openAlerts"] >= 0
    assert all(set(q) == {"kind", "due", "running", "failed24h", "oldestDueSeconds"} for q in f["queue"])
    assert "subject" not in r.text and "body" not in r.text
