"""The pilot at a bank: gates, four-eyes Risk sign-off, stepping back, no sends in shadow, incidents."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.admin_support import (
    add_member,
    admin_conn,
    audit_actions,
    last_seq,
    make_dataset,
    make_deployment,
    org_id,
    run_eval,
)

pytestmark = pytest.mark.integration

TARGETS = {
    "acceptance": 0.7,
    "lightEditMax": 0.2,
    "agreement": 0.85,
    "shadowDays": 14,
    "assistedDays": 28,
    "minLabelled": 100,
    "minDrafts": 200,
}


async def _set_stage(oid: str, stage: str, days_ago: int = 1) -> None:
    conn = await admin_conn()
    try:
        await conn.execute(
            "update orgs set status = $2, status_changed_at = now() - make_interval(days => $3) where id = $1",
            oid,
            stage,
            days_ago,
        )
    finally:
        await conn.close()


async def _a_ticket(oid: str) -> str:
    conn = await admin_conn()
    try:
        return str(
            await conn.fetchval(
                "select id from tickets where org_id = $1 and status not in ('resolved', 'closed', 'triaging') "
                "and merged_into_id is null order by number limit 1",
                oid,
            )
        )
    finally:
        await conn.close()


def _gates(p: dict) -> dict[str, str]:
    return {g["key"]: g["state"] for g in p["gates"]}


async def test_moving_forward_needs_gates_and_a_second_person(app):
    oid = await org_id("apex")
    await _set_stage(oid, "onboarding")
    try:
        a = await add_member(app, "apex", "admin")
        b = await add_member(app, "apex", "admin")
        risk = await add_member(app, "apex", "admin")
        staff = await add_member(app, "apex", "staff")
        seq = await last_seq(oid)

        p = (await a.get("/v1/pilot")).json()
        assert p["stage"] == "onboarding" and p["next"] == "shadow" and p["sendsAllowed"] is False
        assert set(_gates(p)) == {"eval", "mailbox", "baseline"} and _gates(p)["baseline"] == "fail"

        # Nothing is sent from Command Inbox before assisted mode.
        r = await a.send("POST", f"/v1/tickets/{await _a_ticket(oid)}/replies", {"body": "Hello"})
        assert r.status_code == 409 and r.json()["code"] == "pilot_shadow"

        r = await a.send("POST", "/v1/pilot/requests", {"toStage": "shadow", "reason": "Ready to shadow"})
        assert r.status_code == 409 and r.json()["code"] == "gates_not_met"

        # The baseline: from records, then figures the bank measured itself.
        r = await a.send("POST", "/v1/pilot/baseline", {"days": 90})
        assert r.status_code == 200, r.text
        assert r.json()["baseline"]["source"] == "records"
        r = await a.send("POST", "/v1/pilot/baseline", {"onTimeRate": 0.8, "firstReplyMinutes": 240})
        assert r.json()["baseline"] == {**r.json()["baseline"], "onTimeRate": 0.8, "source": "manual"}
        assert (await a.send("POST", "/v1/pilot/baseline", {})).status_code == 400

        dep, vid = await make_deployment(a, f"pilot_{uuid.uuid4().hex[:6]}")
        run = await run_eval(a, vid, await make_dataset(a, dep))
        assert run["state"] == "passed", run
        p = (await a.get("/v1/pilot")).json()
        assert p["ready"] is True, p["gates"]

        body = {"toStage": "shadow", "reason": "Evals passed; baseline recorded"}
        assert (await staff.send("POST", "/v1/pilot/requests", body)).status_code == 403
        assert (await a.send("POST", "/v1/pilot/requests", {**body, "toStage": "live"})).status_code == 409
        r = await a.send("POST", "/v1/pilot/requests", body)
        assert r.status_code == 200, r.text
        pending = r.json()["pending"]
        assert pending["canDecide"] is False and pending["canWithdraw"] is True
        assert {g["key"] for g in pending["evidence"]} == {"eval", "mailbox", "baseline"}
        assert (await a.send("POST", "/v1/pilot/requests", body)).status_code == 409

        # Four eyes: not the person who asked; and only a named Risk approver when there are any.
        url = f"/v1/pilot/requests/{pending['id']}/decision"
        r = await a.send("POST", url, {"approve": True})
        assert r.status_code == 403 and r.json()["code"] == "four_eyes"
        r = await a.send(
            "PUT", "/v1/pilot/settings", {"targets": TARGETS, "riskApprovers": [staff.me["user"]["id"]]}
        )
        assert r.status_code == 400 and r.json()["code"] == "not_admin"
        r = await a.send(
            "PUT", "/v1/pilot/settings", {"targets": TARGETS, "riskApprovers": [risk.me["user"]["id"]]}
        )
        assert r.status_code == 200 and [u["id"] for u in r.json()["riskApprovers"]] == [
            risk.me["user"]["id"]
        ]
        r = await b.send("POST", url, {"approve": True})
        assert r.status_code == 403 and r.json()["code"] == "not_risk_approver"
        assert (await risk.get("/v1/pilot")).json()["pending"]["canDecide"] is True
        assert (await risk.send("POST", url, {"approve": False})).status_code == 400
        r = await risk.send("POST", url, {"approve": True, "note": "Signed off by Risk"})
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["stage"] == "shadow" and p["pending"] is None and p["sendsAllowed"] is False
        assert (
            p["history"][0]["state"] == "approved"
            and p["history"][0]["decidedBy"]["id"] == risk.me["user"]["id"]
        )
        assert (await risk.send("POST", url, {"approve": True})).status_code == 409

        # Shadow → assisted waits on labelled mail and days in shadow.
        assert p["next"] == "assisted"
        assert set(_gates(p)) == {"days", "labelled", "category", "lane", "hard_stops", "p1"}
        assert _gates(p)["days"] == "pending" and p["ready"] is False
        r = await a.send("POST", "/v1/pilot/requests", {"toStage": "assisted", "reason": "Try"})
        assert r.status_code == 409 and r.json()["code"] == "gates_not_met"

        # Stepping back is immediate, needs no second person, and can only go back.
        r = await b.send("POST", "/v1/pilot/step-back", {"toStage": "live", "reason": "no"})
        assert r.status_code == 409
        r = await b.send("POST", "/v1/pilot/step-back", {"toStage": "onboarding", "reason": "Mailbox issue"})
        assert r.status_code == 200 and r.json()["stage"] == "onboarding"

        actions = [x["action"] for x in await audit_actions(oid, seq)]
        for act in (
            "pilot.baseline_recorded",
            "pilot.stage_requested",
            "pilot.settings_changed",
            "pilot.stage_changed",
            "pilot.stepped_back",
        ):
            assert act in actions, act
    finally:
        await _set_stage(oid, "live")
        conn = await admin_conn()
        try:
            await conn.execute("update orgs set pilot_settings = '{}'::jsonb where id = $1", oid)
        finally:
            await conn.close()


async def test_incidents_raise_an_alert_and_count_against_the_gates(app):
    oid = await org_id("apex")
    admin = await add_member(app, "apex", "admin")
    staff = await add_member(app, "apex", "staff")
    conn = await admin_conn()
    try:
        number = await conn.fetchval(
            "select number from tickets where org_id = $1 order by number limit 1", oid
        )
    finally:
        await conn.close()
    r = await staff.send(
        "POST",
        "/v1/pilot/incidents",
        {
            "severity": "P1",
            "kind": "hard_stop_miss",
            "title": "Complaint not held back",
            "ticketNumber": number,
        },
    )
    assert r.status_code == 200, r.text
    inc = r.json()[0]
    assert inc["severity"] == "P1" and inc["ticketNumber"] == number and inc["resolvedAt"] is None
    r = await staff.send(
        "POST",
        "/v1/pilot/incidents",
        {"severity": "P3", "kind": "other", "title": "x", "ticketNumber": 999999},
    )
    assert r.status_code == 404

    async def p1_alert_open() -> bool:
        c = await admin_conn()
        try:
            return bool(
                await c.fetchval(
                    "select count(*) from alerts where org_id = $1 and key = 'pilot:p1' and resolved_at is null",
                    oid,
                )
            )
        finally:
            await c.close()

    assert await p1_alert_open()
    k = (await admin.get("/v1/pilot")).json()["kpis"]
    assert k["p1Incidents"] >= 1 and k["hardStopMisses"] >= 1 and k["openIncidents"] >= 1

    url = f"/v1/pilot/incidents/{inc['id']}/resolve"
    assert (await staff.send("POST", url, {"resolution": "Fixed"})).status_code == 403
    r = await admin.send("POST", url, {"resolution": "Hard stop keyword added; retrained reviewers"})
    assert r.status_code == 200
    assert next(i for i in r.json() if i["id"] == inc["id"])["resolvedBy"]["id"] == admin.me["user"]["id"]
    assert (await admin.send("POST", url, {"resolution": "again"})).status_code == 409
    assert not await p1_alert_open()


async def test_shadow_comparison_counts_every_triaged_mail(app):
    admin = await add_member(app, "apex", "admin")
    r = await admin.get("/v1/pilot/shadow?days=60")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["compared"] >= 1
    assert sum(x["count"] for x in rep["lanes"]) == rep["compared"]
    assert all(d["kind"] in ("category", "lane", "hard_stop_miss") for d in rep["disagreements"])
    staff = await add_member(app, "apex", "staff")
    assert (await staff.get("/v1/pilot/shadow")).status_code in (200, 403)
