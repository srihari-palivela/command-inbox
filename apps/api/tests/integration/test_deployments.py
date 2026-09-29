"""Deployments: backfill, schema integrity, drafts, four-eyes publishing gated on evals, rollout and rollback."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import pytest

from tests.integration.admin_support import (
    add_member,
    admin_conn,
    audit_actions,
    desk_config,
    last_outbox_id,
    last_seq,
    make_dataset,
    make_deployment,
    org_id,
    outbox_topics,
    run_eval,
)
from tests.integration.conftest import sign_in

pytestmark = pytest.mark.integration

# Tables with an org_id that are deliberately outside row-level security (see migrations/sql/0001_security.sql):
# the global identity tables and the worker's cross-tenant queues, never exposed through the API.
RLS_EXEMPT = {"memberships", "sessions", "jobs", "outbox"}


# ── Schema ────────────────────────────────────────────────────────────────────────────────────────────


async def test_every_tenant_table_has_rls_enabled_and_forced(app):
    conn = await admin_conn()
    try:
        rows = await conn.fetch(
            """select c.relname, c.relrowsecurity, c.relforcerowsecurity,
                      exists (select 1 from pg_policy p where p.polrelid = c.oid and p.polname = 'tenant_isolation') as policy
                 from pg_class c
                 join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
                where c.relkind in ('r', 'p')
                  and exists (select 1 from pg_attribute a
                               where a.attrelid = c.oid and a.attname = 'org_id' and not a.attisdropped)
                order by c.relname"""
        )
    finally:
        await conn.close()
    tables = {r["relname"] for r in rows}
    assert {
        "deployments",
        "deployment_versions",
        "eval_runs",
        "role_policies",
        "invitations",
        "tickets",
    } <= tables
    bad = [
        r["relname"]
        for r in rows
        if r["relname"] not in RLS_EXEMPT
        and not (r["relrowsecurity"] and r["relforcerowsecurity"] and r["policy"])
    ]
    assert bad == [], f"tables with org_id but without enabled+forced RLS and a tenant policy: {bad}"


async def test_composite_keys_stop_cross_tenant_references(app):
    apex, meridian = await org_id("apex"), await org_id("meridian")
    conn = await admin_conn()
    try:
        apex_dep = await conn.fetchval(
            "select id from deployments where org_id = $1 and key = 'default'", uuid.UUID(apex)
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "insert into eval_datasets (org_id, deployment_id, name) values ($1, $2, 'sneaky')",
                uuid.UUID(meridian),
                apex_dep,
            )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "update mailboxes set deployment_id = $1 where org_id = $2", apex_dep, uuid.UUID(meridian)
            )
    finally:
        await conn.close()


async def test_default_deployment_backfill(admin):
    r = await admin.get("/v1/deployments")
    assert r.status_code == 200, r.text
    default = next(d for d in r.json() if d["key"] == "default")
    assert default["activeVersion"] == 1 and default["draftVersionId"] is None
    assert len(default["mailboxes"]) == 5
    conn = await admin_conn()
    try:
        orphans = await conn.fetchval(
            "select count(*) from tickets where deployment_id is null or deployment_version_id is null"
        )
        unbound = await conn.fetchval("select count(*) from mailboxes where deployment_id is null")
        orgs_without = await conn.fetchval(
            """select count(*) from orgs o where not exists (select 1 from deployments d
                 join deployment_versions v on v.id = d.active_version_id and v.state = 'published'
                where d.org_id = o.id and d.key = 'default')"""
        )
    finally:
        await conn.close()
    assert (orphans, unbound, orgs_without) == (0, 0, 0)

    detail = (await admin.get(f"/v1/deployments/{default['id']}")).json()
    v1 = detail["versions"][0]
    assert v1["state"] == "published" and len(v1["configHash"]) == 64
    categories = {c["name"] for c in v1["config"]["taxonomy"]["categories"]}
    assert {"Statement re-issue", "Stop payment instruction", "Other / unclear"} <= categories
    assert "regulator_named" in {h["key"] for h in v1["config"]["rules"]["hardStops"]}


# ── Access ────────────────────────────────────────────────────────────────────────────────────────────


async def test_staff_can_view_but_not_edit(staff, admin):
    assert (await staff.get("/v1/deployments")).status_code == 200
    r = await staff.send("POST", "/v1/deployments", {"key": "nope", "name": "Nope"})
    assert r.status_code == 403 and r.json()["code"] == "capability_required"
    default = next(d for d in (await admin.get("/v1/deployments")).json() if d["key"] == "default")
    r = await staff.send("POST", f"/v1/deployments/{default['id']}/versions", {})
    assert r.status_code == 403
    r = await staff.send(
        "POST",
        f"/v1/deployments/{default['id']}/versions/{default['activeVersionId']}/promote",
        {"to": "published"},
    )
    assert r.status_code == 403


async def test_invalid_config_is_a_400_naming_the_path(app):
    a = await add_member(app, "meridian", "admin")
    dep_id, vid = await make_deployment(a, "validation_desk")
    bad = desk_config()
    bad["taxonomy"]["categories"][0]["key"] = "Not A Key"
    r = await a.send("PUT", f"/v1/deployments/{dep_id}/versions/{vid}/config", {"config": bad})
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "invalid_config"
    assert body["detail"].startswith("taxonomy.categories.0.key:"), body["detail"]

    no_guard = desk_config()
    no_guard["flow"]["nodes"] = [n for n in no_guard["flow"]["nodes"] if n["type"] != "hard_stop_guard"]
    r = await a.send("PUT", f"/v1/deployments/{dep_id}/versions/{vid}/config", {"config": no_guard})
    assert r.status_code == 400 and "hard_stop_guard" in r.json()["detail"]


# ── Four-eyes publishing, eval gate, rollout, rollback ────────────────────────────────────────────────


async def _promote(c, dep_id: str, vid: str, to: str, **extra: Any):
    return await c.send("POST", f"/v1/deployments/{dep_id}/versions/{vid}/promote", {"to": to, **extra})


async def test_four_eyes_publish_gated_on_a_current_passing_eval(app):
    editor = await add_member(app, "meridian", "admin", "Editor Admin")
    publisher = await add_member(app, "meridian", "admin", "Publisher Admin")
    oid = editor.me["org"]["id"]
    seq, ob = await last_seq(oid), await last_outbox_id()
    dep_id, vid = await make_deployment(editor, "four_eyes_desk")
    ds = await make_dataset(editor, dep_id)

    # No eval run yet: the gate blocks, and so does the version's publish check.
    r = await _promote(publisher, dep_id, vid, "published")
    assert r.status_code == 409 and r.json()["code"] == "eval_gate"
    v = (await publisher.get(f"/v1/deployments/{dep_id}/versions/{vid}")).json()
    assert v["publishCheck"]["ready"] is False and v["editedBy"]["name"] == "Editor Admin"

    run = await run_eval(editor, vid, ds)
    assert run["state"] == "passed", run
    assert run["current"] is True

    # The editor cannot publish their own edit while another admin exists.
    r = await _promote(editor, dep_id, vid, "published")
    assert r.status_code == 403 and r.json()["code"] == "four_eyes"
    check = (await editor.get(f"/v1/deployments/{dep_id}/versions/{vid}")).json()["publishCheck"]
    assert check["ready"] is False and check["evalRunId"] == run["id"]

    # An edit after the run makes it stale: the hash no longer matches.
    config = desk_config()
    config["taxonomy"]["categories"][0]["description"] = "The customer wants a cheque stopped."
    r = await editor.send("PUT", f"/v1/deployments/{dep_id}/versions/{vid}/config", {"config": config})
    assert r.status_code == 200
    assert (await editor.get(f"/v1/evals/runs/{run['id']}")).json()["current"] is False
    r = await _promote(publisher, dep_id, vid, "published")
    assert r.status_code == 409 and r.json()["code"] == "eval_gate"

    # Re-run on the new hash, then roll out: shadow (no gate) → canary → published.
    run2 = await run_eval(editor, vid, ds)
    assert run2["state"] == "passed"
    r = await _promote(publisher, dep_id, vid, "shadow")
    assert r.status_code == 200 and r.json()["state"] == "shadow"
    r = await _promote(publisher, dep_id, vid, "canary")
    assert r.status_code == 400 and r.json()["code"] == "canary_percent_required"
    r = await _promote(publisher, dep_id, vid, "canary", canaryPercent=20)
    assert r.status_code == 200 and r.json()["canaryPercent"] == 20
    r = await _promote(publisher, dep_id, vid, "shadow")
    assert r.status_code == 409  # versions only move forward
    r = await _promote(publisher, dep_id, vid, "published")
    assert r.status_code == 200, r.text
    v1 = r.json()
    assert v1["state"] == "published" and v1["evalRunId"] == run2["id"] and v1["canaryPercent"] is None
    assert v1["publishedBy"]["name"] == "Publisher Admin"
    dep = (await publisher.get(f"/v1/deployments/{dep_id}")).json()
    assert dep["activeVersionId"] == vid and dep["activeVersion"] == 1

    # Published versions are immutable.
    r = await editor.send("PUT", f"/v1/deployments/{dep_id}/versions/{vid}/config", {"config": config})
    assert r.status_code == 409 and r.json()["code"] == "not_a_draft"

    actions = [a["action"] for a in await audit_actions(oid, seq)]
    for expected in (
        "deployment.created",
        "deployment.draft_edited",
        "deployment.shadow",
        "deployment.canary",
    ):
        assert expected in actions
    published = [a for a in await audit_actions(oid, seq) if a["action"] == "deployment.published"]
    assert len(published) == 1
    assert "deployment.updated" in await outbox_topics(oid, ob)


async def test_publishing_retires_the_previous_version_and_rollback_restores_it(app):
    a = await add_member(app, "meridian", "admin")
    b = await add_member(app, "meridian", "admin")
    dep_id, v1 = await make_deployment(a, "rollback_desk")
    ds = await make_dataset(a, dep_id)
    assert (await run_eval(a, v1, ds))["state"] == "passed"
    assert (await _promote(b, dep_id, v1, "published")).status_code == 200

    r = await b.send("POST", f"/v1/deployments/{dep_id}/versions", {"notes": "tighten thresholds"})
    assert r.status_code == 200
    v2 = r.json()
    assert v2["version"] == 2 and v2["state"] == "draft" and v2["config"]["taxonomy"]
    r = await b.send("POST", f"/v1/deployments/{dep_id}/versions", {})
    assert r.status_code == 409 and r.json()["code"] == "draft_exists"
    assert (await run_eval(b, v2["id"], ds))["state"] == "passed"
    assert (await _promote(a, dep_id, v2["id"], "published")).status_code == 200

    dep = (await a.get(f"/v1/deployments/{dep_id}")).json()
    states = {v["version"]: v["state"] for v in dep["versions"]}
    assert states == {2: "published", 1: "retired"} and dep["activeVersion"] == 2

    # Bind the workspace's mailbox to the new deployment; a mailbox can never be left unbound.
    default = next(d for d in (await a.get("/v1/deployments")).json() if d["key"] == "default")
    mailbox = default["mailboxes"][0]["id"]
    r = await a.send("PUT", f"/v1/deployments/{dep_id}/mailboxes", {"mailboxIds": [mailbox]})
    assert r.status_code == 200 and r.json()["mailboxes"][0]["id"] == mailbox
    r = await a.send("PUT", f"/v1/deployments/{dep_id}/mailboxes", {"mailboxIds": []})
    assert r.status_code == 409 and r.json()["code"] == "mailbox_unbound"
    r = await a.send("PUT", f"/v1/deployments/{default['id']}/mailboxes", {"mailboxIds": [mailbox]})
    assert r.status_code == 200  # moved back

    seq = await last_seq(a.me["org"]["id"])
    r = await a.send("POST", f"/v1/deployments/{dep_id}/rollback", {})
    assert r.status_code == 200, r.text
    dep = r.json()
    states = {v["version"]: v["state"] for v in dep["versions"]}
    assert states == {1: "published", 2: "retired"} and dep["activeVersionId"] == v1
    rolled = [
        x for x in await audit_actions(a.me["org"]["id"], seq) if x["action"] == "deployment.rolled_back"
    ]
    assert len(rolled) == 1 and "back to v1" in rolled[0]["summary"]

    # A draft that was never published cannot be "rolled back" to.
    r = await b.send("POST", f"/v1/deployments/{dep_id}/versions", {})
    draft = r.json()["id"]
    r = await a.send("POST", f"/v1/deployments/{dep_id}/rollback", {"versionId": draft})
    assert r.status_code == 409 and r.json()["code"] == "not_rollbackable"
    r = await b.send("POST", f"/v1/deployments/{dep_id}/versions/{draft}/withdraw")
    assert r.status_code == 200 and r.json()["state"] == "retired"


async def test_lead_with_delegated_edit_drafts_and_an_admin_publishes(app):
    admin_ = await add_member(app, "meridian", "admin")
    lead_ = await add_member(app, "meridian", "lead")
    dep_id, v1 = await make_deployment(admin_, "delegated_desk")

    r = await lead_.send("POST", f"/v1/deployments/{dep_id}/versions/{v1}/config", {"config": desk_config()})
    assert r.status_code in (403, 405)
    r = await lead_.send("PUT", f"/v1/deployments/{dep_id}/versions/{v1}/config", {"config": desk_config()})
    assert r.status_code == 403 and r.json()["code"] == "capability_required"

    grant = {"overrides": [{"role": "lead", "capability": "deployment.edit", "effect": "allow"}]}
    r = await admin_.send("PUT", "/v1/permissions", grant)
    assert r.status_code == 200, r.text
    try:
        config = desk_config()
        config["thresholds"] = {"autoMinConfidence": 0.95, "draftMinConfidence": 0.7}
        r = await lead_.send("PUT", f"/v1/deployments/{dep_id}/versions/{v1}/config", {"config": config})
        assert r.status_code == 200, r.text
        assert r.json()["editedBy"]["id"] == lead_.me["user"]["id"]
        # The lead still cannot publish (admin only, never delegable).
        ds = await make_dataset(admin_, dep_id)
        assert (await run_eval(admin_, v1, ds))["state"] == "passed"
        r = await _promote(lead_, dep_id, v1, "published")
        assert r.status_code == 403 and r.json()["code"] == "capability_required"
        # Four-eyes is satisfied: the admin did not make the last edit.
        r = await _promote(admin_, dep_id, v1, "published")
        assert r.status_code == 200, r.text
    finally:
        revoke = {"overrides": [{"role": "lead", "capability": "deployment.edit", "effect": None}]}
        assert (await admin_.send("PUT", "/v1/permissions", revoke)).status_code == 200
    r = await lead_.send("POST", f"/v1/deployments/{dep_id}/versions", {})
    assert r.status_code == 403


async def test_single_admin_must_acknowledge_publishing_their_own_edit(app):
    # northwind has exactly one admin (a.kapoor); use a separate session so the shared fixture is untouched.
    c = await sign_in(app, "a.kapoor@bank.example")
    northwind = await org_id("northwind")
    r = await c.send("POST", "/v1/session/org", {"orgId": northwind})
    assert r.status_code == 200
    me = (await c.get("/v1/me")).json()
    c.me = me
    assert me["role"] == "admin"

    dep_id, vid = await make_deployment(c, "solo_desk")
    ds = await make_dataset(c, dep_id)
    assert (await run_eval(c, vid, ds))["state"] == "passed"
    check = (await c.get(f"/v1/deployments/{dep_id}/versions/{vid}")).json()["publishCheck"]
    assert check == {**check, "ready": True, "requiresSingleAdminAck": True}

    r = await _promote(c, dep_id, vid, "published")
    assert r.status_code == 409 and r.json()["code"] == "single_admin_ack_required"
    seq = await last_seq(northwind)
    r = await _promote(c, dep_id, vid, "published", acknowledgeSingleAdmin=True)
    assert r.status_code == 200, r.text
    event = next(a for a in await audit_actions(northwind, seq) if a["action"] == "deployment.published")
    assert "acknowledged without review" in event["summary"]
    import json

    data = event["data"] if isinstance(event["data"], dict) else json.loads(event["data"])
    assert data["singleAdminAcknowledged"] is True
