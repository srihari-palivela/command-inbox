"""Members and roles, the last-admin invariant, invitations (through SSO acceptance) and permission overrides."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from tests.integration.admin_support import (
    add_member,
    admin_conn,
    audit_actions,
    last_outbox_id,
    last_seq,
    outbox_topics,
)

pytestmark = pytest.mark.integration


async def test_member_admin_requires_members_manage(lead, staff, admin):
    assert (await lead.get("/v1/members")).status_code == 403
    assert (await staff.get("/v1/invitations")).status_code == 403
    assert (await lead.get("/v1/permissions")).status_code == 403
    r = await admin.get("/v1/members")
    assert r.status_code == 200
    members = r.json()
    assert {m["role"] for m in members} == {"staff", "lead", "admin"}
    assert sum(m["isMe"] for m in members) == 1


async def test_change_role_and_you_cannot_change_your_own(app):
    a = await add_member(app, "meridian", "admin")
    s = await add_member(app, "meridian", "staff")
    oid = a.me["org"]["id"]
    seq = await last_seq(oid)
    r = await a.send("PUT", f"/v1/members/{s.me['user']['id']}/role", {"role": "lead"})
    assert r.status_code == 200 and r.json()["role"] == "lead"
    # The new role applies on the member's next request (capabilities are resolved per request).
    me = (await s.get("/v1/me")).json()
    assert me["role"] == "lead" and "action.approve_checker" in me["capabilities"]
    r = await a.send("PUT", f"/v1/members/{a.me['user']['id']}/role", {"role": "staff"})
    assert r.status_code == 403 and r.json()["code"] == "own_role"
    r = await a.send("PUT", f"/v1/members/{s.me['user']['id']}/role", {"role": "owner"})
    assert r.status_code == 400
    r = await a.send("PUT", f"/v1/members/{uuid.uuid4()}/role", {"role": "lead"})
    assert r.status_code == 404
    changed = [e for e in await audit_actions(oid, seq) if e["action"] == "member.role_changed"]
    assert len(changed) == 1 and "from Staff to Team lead" in changed[0]["summary"]


async def test_concurrent_demotions_never_leave_zero_admins(app):
    # A fresh pair of admins in a workspace whose other admin we demote first, so these two are the last.
    a = await add_member(app, "meridian", "admin")
    b = await add_member(app, "meridian", "admin")
    members = (await a.get("/v1/members")).json()
    for m in members:
        if m["role"] == "admin" and m["user"]["id"] not in (a.me["user"]["id"], b.me["user"]["id"]):
            r = await a.send("PUT", f"/v1/members/{m['user']['id']}/role", {"role": "lead"})
            assert r.status_code == 200, r.text
    try:
        ra, rb = await asyncio.gather(
            a.send("PUT", f"/v1/members/{b.me['user']['id']}/role", {"role": "staff"}),
            b.send("PUT", f"/v1/members/{a.me['user']['id']}/role", {"role": "staff"}),
        )
        codes = sorted([ra.status_code, rb.status_code])
        assert codes[0] == 200 and codes[1] in (403, 409), (ra.text, rb.text)
        loser = ra if ra.status_code != 200 else rb
        assert loser.json()["code"] in ("last_admin", "capability_required")
        admins = [
            m
            for m in (await (a if ra.status_code == 200 else b).get("/v1/members")).json()
            if m["role"] == "admin"
        ]
        assert len(admins) == 1
    finally:
        # Restore meridian's seeded admin so later tests see the workspace as seeded.
        conn = await admin_conn()
        try:
            await conn.execute(
                """update memberships m set role = 'admin' from users u
                    where u.id = m.user_id and u.email = 'p.sharma@bank.example'
                      and m.org_id = (select id from orgs where slug = 'meridian')"""
            )
        finally:
            await conn.close()


async def test_the_database_refuses_to_drop_the_last_admin(app):
    from command_inbox.db.engine import tenant_tx

    northwind = (await admin_conn_fetch("select id from orgs where slug = 'northwind'"))[0]["id"]
    with pytest.raises(Exception, match="at least one admin"):
        async with tenant_tx(str(northwind)) as tx:
            from sqlalchemy import text

            await tx.execute(
                text("update memberships set role = 'staff' where org_id = :o and role = 'admin'"),
                {"o": str(northwind)},
            )
    rows = await admin_conn_fetch(
        "select count(*) as n from memberships where org_id = $1 and role = 'admin'", northwind
    )
    assert rows[0]["n"] == 1


async def admin_conn_fetch(sql: str, *args):
    conn = await admin_conn()
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def test_removing_a_member_revokes_their_sessions(app):
    a = await add_member(app, "meridian", "admin")
    s = await add_member(app, "meridian", "staff")
    assert (await s.get("/v1/me")).status_code == 200
    r = await a.send("DELETE", f"/v1/members/{s.me['user']['id']}")
    assert r.status_code == 200 and r.json()["revokedSessions"] >= 1
    assert (await s.get("/v1/me")).status_code == 401
    assert s.me["user"]["id"] not in {m["user"]["id"] for m in (await a.get("/v1/members")).json()}
    r = await a.send("DELETE", f"/v1/members/{a.me['user']['id']}")
    assert r.status_code == 403 and r.json()["code"] == "own_membership"


async def test_invitations(app, monkeypatch):
    a = await add_member(app, "meridian", "admin")
    oid = a.me["org"]["id"]
    email = f"New.Joiner.{uuid.uuid4().hex[:6]}@Invitee.Example"
    r = await a.send("POST", "/v1/invitations", {"email": email, "role": "lead"})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["email"] == email.lower() and inv["state"] == "pending" and inv["role"] == "lead"
    assert inv["invitedBy"]["id"] == a.me["user"]["id"]

    r = await a.send("POST", "/v1/invitations", {"email": email.lower(), "role": "staff"})
    assert r.status_code == 409 and r.json()["code"] == "already_invited"
    r = await a.send("POST", "/v1/invitations", {"email": a.me["user"]["email"], "role": "staff"})
    assert r.status_code == 409 and r.json()["code"] == "already_member"
    assert (
        await a.send("POST", "/v1/invitations", {"email": "not-an-email", "role": "staff"})
    ).status_code == 400

    # Revoke, then re-invite; an expired invitation frees the slot too.
    r = await a.send("DELETE", f"/v1/invitations/{inv['id']}")
    assert r.status_code == 200 and r.json()["state"] == "revoked"
    assert (await a.send("DELETE", f"/v1/invitations/{inv['id']}")).status_code == 409
    r = await a.send("POST", "/v1/invitations", {"email": email, "role": "lead", "expiresInDays": 1})
    second = r.json()
    conn = await admin_conn()
    try:
        await conn.execute(
            "update invitations set expires_at = now() - interval '1 minute' where id = $1",
            uuid.UUID(second["id"]),
        )
    finally:
        await conn.close()
    listed = {i["id"]: i["state"] for i in (await a.get("/v1/invitations")).json()}
    assert listed[inv["id"]] == "revoked" and listed[second["id"]] == "expired"
    r = await a.send("POST", "/v1/invitations", {"email": email, "role": "lead"})
    assert r.status_code == 200
    third = r.json()

    # Accepting through SSO: an identity from a trusted domain with the invited email joins with that role.
    from command_inbox.auth import oidc

    conn = await admin_conn()
    try:
        await conn.execute(
            "update orgs set sso_email_domains = '[\"invitee.example\"]'::jsonb where id = $1", uuid.UUID(oid)
        )
    finally:
        await conn.close()

    async def fake_metadata():
        return {"issuer": "https://idp.test/realms/ci"}

    monkeypatch.setattr(oidc, "metadata", fake_metadata)
    claims = {"sub": uuid.uuid4().hex, "email": email, "email_verified": True, "name": "New Joiner"}
    user, joined = await oidc.admit(claims)
    assert joined == oid and user.email == email.lower()
    members = {m["user"]["email"]: m["role"] for m in (await a.get("/v1/members")).json()}
    assert members[email.lower()] == "lead"
    states = {i["id"]: i["state"] for i in (await a.get("/v1/invitations")).json()}
    assert states[third["id"]] == "accepted"


async def test_permission_matrix_and_overrides(app, lead):
    a = await add_member(app, "meridian", "admin")
    l = await add_member(app, "meridian", "lead")  # noqa: E741
    oid = a.me["org"]["id"]
    r = await a.get("/v1/permissions")
    assert r.status_code == 200
    matrix = r.json()
    assert matrix["roles"] == ["staff", "lead", "admin"]
    row = {x["capability"]: x for x in matrix["rows"]}
    checker = {c["role"]: c for c in row["action.approve_checker"]["cells"]}
    assert checker["staff"]["locked"] and not checker["staff"]["allowed"] and checker["lead"]["allowed"]
    assert all(c["locked"] for c in row["deployment.publish"]["cells"])
    edit = {c["role"]: c for c in row["deployment.edit"]["cells"]}
    assert not edit["lead"]["locked"] and not edit["lead"]["allowed"] and edit["admin"]["allowed"]

    # Locked rules are refused.
    for role, cap in (
        ("staff", "action.approve_checker"),
        ("lead", "deployment.publish"),
        ("admin", "insights.view"),
        ("lead", "members.manage"),
    ):
        r = await a.send(
            "PUT", "/v1/permissions", {"overrides": [{"role": role, "capability": cap, "effect": "allow"}]}
        )
        assert r.status_code == 403 and r.json()["code"] == "locked", (role, cap, r.text)
    r = await a.send(
        "PUT",
        "/v1/permissions",
        {"overrides": [{"role": "lead", "capability": "no.such", "effect": "allow"}]},
    )
    assert r.status_code == 400 and r.json()["code"] == "bad_capability"
    # A lead cannot change permissions at all.
    r = await lead.send(
        "PUT",
        "/v1/permissions",
        {"overrides": [{"role": "staff", "capability": "insights.view", "effect": "allow"}]},
    )
    assert r.status_code == 403

    seq, ob = await last_seq(oid), await last_outbox_id()
    grant = {
        "overrides": [
            {"role": "lead", "capability": "evals.run", "effect": "allow"},
            {"role": "lead", "capability": "insights.view", "effect": "deny"},
        ]
    }
    r = await a.send("PUT", "/v1/permissions", grant)
    assert r.status_code == 200, r.text
    try:
        cells = {x["capability"]: {c["role"]: c for c in x["cells"]} for x in r.json()["rows"]}
        assert cells["evals.run"]["lead"]["allowed"] and cells["evals.run"]["lead"]["override"] == "allow"
        assert not cells["insights.view"]["lead"]["allowed"] and cells["insights.view"]["lead"]["baseline"]
        caps = set((await l.get("/v1/me")).json()["capabilities"])
        assert "evals.run" in caps and "insights.view" not in caps
        assert {o["capability"] for o in r.json()["overrides"]} == {"evals.run", "insights.view"}
        assert "rbac.updated" in await outbox_topics(oid, ob)
        event = next(e for e in await audit_actions(oid, seq) if e["action"] == "rbac.overrides_changed")
        data = event["data"] if isinstance(event["data"], dict) else json.loads(event["data"])
        assert len(data["changes"]) == 2
        # Re-applying the same overrides changes nothing and writes no audit event.
        seq2 = await last_seq(oid)
        assert (await a.send("PUT", "/v1/permissions", grant)).status_code == 200
        assert await audit_actions(oid, seq2) == []
    finally:
        reset = {
            "overrides": [
                {"role": "lead", "capability": "evals.run", "effect": None},
                {"role": "lead", "capability": "insights.view", "effect": None},
            ]
        }
        r = await a.send("PUT", "/v1/permissions", reset)
        assert r.status_code == 200 and r.json()["overrides"] == []
    caps = set((await l.get("/v1/me")).json()["capabilities"])
    assert "evals.run" not in caps and "insights.view" in caps
