"""People: the clearance matrix, clearance edits and auto-assign (ports 'roles and permissions')."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _audit(org_id: str, action: str) -> list[dict]:
    from command_inbox.db.engine import rows, tenant_tx

    async with tenant_tx(org_id) as tx:
        return await rows(
            tx,
            "select summary, entity_id, data from audit_events where action = :a order by seq desc",
            {"a": action},
        )


async def test_staff_cannot_widen_autonomy_or_change_clearances(staff, lead):
    assert (await staff.send("PUT", "/v1/actions/dial", {"cell": "0-0", "level": 2})).status_code == 403
    dept = (await lead.get("/v1/people")).json()["departments"][0]["id"]
    r = await staff.send(
        "PUT", "/v1/clearances", {"userId": staff.me["user"]["id"], "departmentId": dept, "level": 3}
    )
    assert r.status_code == 403
    assert r.json()["code"] == "capability_required"


async def test_a_lead_cannot_write_a_clearance_for_someone_outside_the_workspace(lead):
    dept = (await lead.get("/v1/people")).json()["departments"][0]["id"]
    r = await lead.send(
        "PUT",
        "/v1/clearances",
        {"userId": "00000000-0000-4000-8000-000000000000", "departmentId": dept, "level": 1},
    )
    assert r.status_code == 404


async def test_a_lead_cannot_write_a_clearance_for_another_workspaces_team(lead):
    from command_inbox.db.engine import global_tx

    async with global_tx() as tx:
        other = (await tx.execute(text("select id::text from orgs where slug = 'northwind'"))).scalar_one()
    from command_inbox.db.engine import tenant_tx

    async with tenant_tx(other) as tx:
        foreign_dept = (await tx.execute(text("select id::text from departments limit 1"))).scalar_one()
    r = await lead.send(
        "PUT",
        "/v1/clearances",
        {"userId": lead.me["user"]["id"], "departmentId": foreign_dept, "level": 3},
    )
    assert r.status_code == 404


async def test_staff_can_read_the_clearance_matrix_but_not_edit_it(staff, lead):
    r = await staff.get("/v1/people")
    assert r.status_code == 200
    body = r.json()
    assert body["editable"] is False
    assert any(p["isMe"] for p in body["staff"]) and body["departments"]
    assert (await lead.get("/v1/people")).json()["editable"] is True


async def test_a_lead_edits_a_clearance_in_the_workspace_and_it_is_audited(lead):
    people = (await lead.get("/v1/people")).json()
    person = next(p for p in people["staff"] if p["role"] == "staff" and not p["isMe"])
    dept = people["departments"][-1]
    level = 1 if person["clearances"].get(dept["id"]) != 1 else 2
    r = await lead.send(
        "PUT", "/v1/clearances", {"userId": person["id"], "departmentId": dept["id"], "level": level}
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    after = (await lead.get("/v1/people")).json()
    assert next(p for p in after["staff"] if p["id"] == person["id"])["clearances"][dept["id"]] == level
    [last, *_] = await _audit(lead.me["org"]["id"], "clearance.changed")
    assert last["entity_id"] == f"{person['id']}:{dept['id']}"
    assert last["summary"].startswith(f"{person['name']} · {dept['name']} → ")
    assert last["data"] == {"level": level}


async def test_clearance_level_is_validated(lead):
    dept = (await lead.get("/v1/people")).json()["departments"][0]["id"]
    r = await lead.send(
        "PUT", "/v1/clearances", {"userId": lead.me["user"]["id"], "departmentId": dept, "level": 4}
    )
    assert r.status_code in (400, 422)


async def test_auto_assign(staff, lead):
    assert (await staff.send("POST", "/v1/assignments/auto")).status_code == 403
    r = await lead.send("POST", "/v1/assignments/auto")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] >= len(body["moves"])
    for m in body["moves"]:
        assert m["ticketNumber"].startswith("QRY-") and m["reason"]
    [ran, *_] = await _audit(lead.me["org"]["id"], "auto_assign.ran")
    assert ran["summary"].startswith(f"Auto-assign checked {body['checked']} at-risk ticket")
    moved = [m for m in body["moves"] if m["to"]]
    if moved:
        events = await _audit(lead.me["org"]["id"], "ticket.auto_assigned")
        assert any(e["summary"] == f"{moved[0]['ticketNumber']} → {moved[0]['to']}" for e in events)
    # Tickets it assigned are now owned by people, so a second run does not move them again.
    again = (await lead.send("POST", "/v1/assignments/auto")).json()
    assert not {m["ticketNumber"] for m in again["moves"] if m["to"]} & {m["ticketNumber"] for m in moved}
