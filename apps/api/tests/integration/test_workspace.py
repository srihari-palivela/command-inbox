"""Workspace: personal settings, the admin overview and audit-chain verification."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_audit_verify_is_ok_on_the_seeded_chain(admin):
    r = await admin.get("/v1/audit/verify")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["brokenAt"] is None and body["events"] > 0


async def test_audit_verify_is_admin_only(staff, lead):
    for client in (staff, lead):
        r = await client.get("/v1/audit/verify")
        assert r.status_code == 403
        assert r.json()["code"] == "capability_required"


async def test_settings_merge_prefs_and_keep_the_signature(app):
    from tests.integration.conftest import sign_in

    # A user no other test signs in as, so /v1/me is not disturbed elsewhere.
    me = await sign_in(app, "l.thomas@bank.example")
    r = await me.send("PUT", "/v1/settings", {"signature": "Regards,\nL. Thomas"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert (await me.send("PUT", "/v1/settings", {"prefs": {"compact": True}})).status_code == 200
    assert (await me.send("PUT", "/v1/settings", {"prefs": {"sound": False}})).status_code == 200
    settings = (await me.get("/v1/me")).json()["settings"]
    assert settings["signature"] == "Regards,\nL. Thomas"
    assert settings["prefs"]["compact"] is True and settings["prefs"]["sound"] is False


async def test_settings_body_is_validated(staff):
    r = await staff.send("PUT", "/v1/settings", {"signature": "x" * 2001})
    assert r.status_code in (400, 422)


async def test_admin_overview(admin, lead, staff):
    r = await admin.get("/v1/admin")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mailboxes"] and body["connectors"] and len(body["guardrails"]) == 5
    mb = body["mailboxes"][0]
    assert {"id", "address", "department", "permissions", "volume24h", "state", "provider"} == set(mb)
    for client in (lead, staff):
        assert (await client.get("/v1/admin")).status_code == 403


async def test_learning_notifications_and_courses(lead, staff):
    learning = (await staff.get("/v1/learning")).json()
    assert learning["courses"] and learning["teamSize"] >= 1
    assert (
        await staff.send("POST", "/v1/notifications", {"title": "Hi", "kind": "message"})
    ).status_code == 403
    r = await lead.send("POST", "/v1/notifications", {"title": "New fee schedule", "kind": "learning"})
    assert r.status_code == 200 and r.json()["recipients"] >= 0
    learning = (await staff.get("/v1/learning")).json()
    note = next(n for n in learning["notifications"] if n["title"] == "New fee schedule")
    assert note["courseId"] and note["read"] is False
    course = next(c for c in learning["courses"] if c["id"] == note["courseId"])
    answers = [q["correct"] for q in course["quiz"]]
    r = await staff.send("POST", f"/v1/courses/{course['id']}/complete", {"answers": answers})
    assert r.json() == {"score": len(answers), "total": len(answers)}
    learning = (await staff.get("/v1/learning")).json()
    assert next(c for c in learning["courses"] if c["id"] == course["id"])["completedByMe"] is True
    assert next(n for n in learning["notifications"] if n["id"] == note["id"])["read"] is True
    # Unknown ids are ignored; "all" marks everything read.
    r = await staff.send("POST", "/v1/notifications/read", {"ids": ["00000000-0000-4000-8000-000000000000"]})
    assert r.status_code == 200
    assert (await staff.send("POST", "/v1/notifications/read", {"all": True})).status_code == 200
    assert (await staff.get("/v1/learning")).json()["unread"] == 0
    assert (
        await staff.send("POST", "/v1/courses/00000000-0000-4000-8000-000000000000/complete", {"answers": []})
    ).status_code == 404
