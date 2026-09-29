"""Teams, query types and reply-time targets: CRUD with the guards that keep live routing and history intact."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.admin_support import add_member, admin_conn, audit_actions, drain, last_seq, org_id

pytestmark = pytest.mark.integration


async def test_teams_and_query_types_lifecycle(app):
    a = await add_member(app, "meridian", "admin")
    oid = await org_id("meridian")
    seq = await last_seq(oid)
    name = f"Trade Desk {uuid.uuid4().hex[:5]}"

    r = await a.send("POST", "/v1/taxonomy/departments", {"name": name, "risk": True})
    assert r.status_code == 200, r.text
    team = next(d for d in r.json()["departments"] if d["name"] == name)
    assert team["risk"] and team["deletable"] and team["queryTypes"] == 0
    # Every member can work the new team's mail at their role's default clearance.
    conn = await admin_conn()
    try:
        levels = await conn.fetch(
            "select level from clearances where org_id = $1 and department_id = $2",
            uuid.UUID(oid),
            uuid.UUID(team["id"]),
        )
    finally:
        await conn.close()
    assert levels and all(r["level"] in (2, 3) for r in levels)

    assert (await a.send("POST", "/v1/taxonomy/departments", {"name": name.upper()})).status_code == 409

    r = await a.send(
        "POST",
        "/v1/taxonomy/query-types",
        {"name": f"LC amendment {name}", "departmentId": team["id"], "defaultLane": "draft"},
    )
    assert r.status_code == 200, r.text
    qt = next(q for q in r.json()["queryTypes"] if q["name"] == f"LC amendment {name}")
    assert qt["departmentId"] == team["id"] and qt["live"] and qt["deletable"]
    assert (
        await a.send(
            "POST",
            "/v1/taxonomy/query-types",
            {"name": "x", "departmentId": team["id"], "defaultLane": "auto"},
        )
    ).status_code == 400  # no automatic lane in this version (request validation)

    r = await a.send("DELETE", f"/v1/taxonomy/departments/{team['id']}")
    assert r.status_code == 409 and r.json()["code"] == "department_in_use", r.text

    r = await a.send(
        "PUT",
        f"/v1/taxonomy/query-types/{qt['id']}",
        {"name": f"LC amendments {name}", "departmentId": team["id"], "defaultLane": "manual", "live": False},
    )
    assert r.status_code == 200, r.text
    assert (await a.send("DELETE", f"/v1/taxonomy/query-types/{qt['id']}")).status_code == 200
    r = await a.send("PUT", f"/v1/taxonomy/departments/{team['id']}", {"name": name + " II", "risk": False})
    assert r.status_code == 200, r.text
    assert (await a.send("DELETE", f"/v1/taxonomy/departments/{team['id']}")).status_code == 200

    actions = [x["action"] for x in await audit_actions(oid, seq)]
    for action in (
        "department.created",
        "query_type.created",
        "query_type.updated",
        "query_type.deleted",
        "department.updated",
        "department.deleted",
    ):
        assert action in actions, action

    staff = await add_member(app, "meridian", "staff")
    assert (await staff.send("POST", "/v1/taxonomy/departments", {"name": "Nope"})).status_code == 403


async def test_a_team_or_query_type_a_live_deployment_routes_by_keeps_its_name(app, admin):
    oid = admin.me["org"]["id"]
    conn = await admin_conn()
    try:
        config = await conn.fetchval(
            "select v.config from deployment_versions v where v.org_id = $1 and v.state = 'published' limit 1",
            uuid.UUID(oid),
        )
    finally:
        await conn.close()
    import json

    categories = (json.loads(config) if isinstance(config, str) else config)["taxonomy"]["categories"]
    view = (await admin.get("/v1/taxonomy/admin")).json()
    by_name = {d["name"]: d for d in view["departments"]}
    team = next(by_name[c["department"]] for c in categories if c["department"] in by_name)
    r = await admin.send(
        "PUT",
        f"/v1/taxonomy/departments/{team['id']}",
        {"name": team["name"] + " (renamed)", "risk": team["risk"]},
    )
    assert r.status_code == 409 and r.json()["code"] == "name_in_use", r.text

    used = next((q for q in view["queryTypes"] if q["tickets"]), None)
    assert used is not None and not used["deletable"]
    r = await admin.send("DELETE", f"/v1/taxonomy/query-types/{used['id']}")
    assert r.status_code == 409 and r.json()["code"] == "query_type_in_use"


async def test_reply_time_targets_apply_to_newly_triaged_mail(app):
    a = await add_member(app, "meridian", "admin")
    r = await a.get("/v1/taxonomy/sla-policies")
    assert r.status_code == 200, r.text
    original = r.json()
    assert original["policies"] and "Retail" in original["segments"]

    bad = {
        "policies": [
            {"name": "Urgent", "priority": "P1", "segment": None, "escalation": False, "minutes": 60}
        ]
    }
    r = await a.send("PUT", "/v1/taxonomy/sla-policies", bad)
    assert r.status_code == 422 and r.json()["code"] == "catch_all_required"
    dup = {
        "policies": [
            *bad["policies"],
            *bad["policies"],
            {"name": "All", "priority": None, "segment": None, "minutes": 90},
        ]
    }
    assert (await a.send("PUT", "/v1/taxonomy/sla-policies", dup)).json()["code"] == "duplicate_policy"

    mine = {
        "policies": [
            {"name": "Everything", "priority": None, "segment": None, "escalation": False, "minutes": 95}
        ]
    }
    r = await a.send("PUT", "/v1/taxonomy/sla-policies", mine)
    assert r.status_code == 200, r.text
    assert r.json()["usingDefaults"] is False and [p["minutes"] for p in r.json()["policies"]] == [95]

    r = await a.send("POST", "/v1/dev/simulate-mail", {"index": 2})
    assert r.status_code == 200, r.text
    await drain()
    ticket = await a.ticket(r.json()["ticketId"])
    assert ticket["sla"]["budgetMinutes"] == 95

    restore = {
        "policies": [
            {k: p[k] for k in ("name", "priority", "segment", "escalation", "minutes")}
            for p in original["policies"]
        ]
    }
    assert (await a.send("PUT", "/v1/taxonomy/sla-policies", restore)).status_code == 200
