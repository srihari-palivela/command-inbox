"""AI setup: boards, agents, feedback, actions and the autonomy dial, rules, knowledge, taxonomy."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

MISSING = "00000000-0000-4000-8000-000000000000"


async def _audit(org_id: str, action: str) -> list[dict]:
    from command_inbox.db.engine import rows, tenant_tx

    async with tenant_tx(org_id) as tx:
        return await rows(
            tx,
            "select summary, entity_id, data, feed_tone from audit_events where action = :a order by seq desc",
            {"a": action},
        )


async def test_staff_cannot_view_or_edit_setup(staff, admin):
    for url in ("/v1/agents", "/v1/actions", "/v1/policies", "/v1/knowledge", "/v1/taxonomy"):
        assert (await staff.get(url)).status_code == 403, url
    board = (await admin.get("/v1/boards")).json()[0]
    agent = (await admin.get("/v1/agents")).json()["agents"][0]
    rule = (await admin.get("/v1/policies")).json()["priorityRules"][0]
    dept = (await admin.get("/v1/taxonomy")).json()["departments"][0]
    edits = [
        (
            "POST",
            "/v1/boards",
            {"name": "Staff board", "provider": "dev", "mailbox": "staff-board@bank.example"},
        ),
        (
            "POST",
            "/v1/agents",
            {
                "name": "Staff agent",
                "template": "drafting",
                "model": "some-model",
                "prompt": "p",
                "boardIds": [board["id"]],
            },
        ),
        ("POST", f"/v1/agents/{agent['id']}/versions", {"prompt": "new"}),
        ("PUT", f"/v1/agents/{agent['id']}/boards", {"boardIds": []}),
        ("PUT", "/v1/actions/dial", {"cell": "0-0", "level": 1}),
        ("POST", "/v1/actions/templates", {"name": "Close account", "system": "CBS", "cell": "0-1"}),
        ("PATCH", f"/v1/policies/priority-rules/{rule['id']}", {"enabled": False}),
        ("POST", f"/v1/policies/proposed/{MISSING}/decide", {"approve": True}),
        ("POST", "/v1/knowledge/sources", {"kind": "S3"}),
        ("POST", f"/v1/knowledge/sources/{MISSING}/sync", None),
        ("POST", f"/v1/knowledge/gaps/{MISSING}/act", None),
        ("PUT", f"/v1/taxonomy/departments/{dept['id']}/owner", {}),
        ("POST", f"/v1/feedback/{MISSING}/queue", None),
    ]
    for method, url, body in edits:
        r = await staff.send(method, url, body)
        assert r.status_code == 403, (method, url, r.text)
        assert r.json()["code"] == "capability_required"


async def test_the_irreversible_money_cell_cannot_be_dialled_up_even_by_an_admin(admin):
    r = await admin.send("PUT", "/v1/actions/dial", {"cell": "1-1", "level": 1})
    assert r.status_code == 403 and r.json()["code"] == "cell_locked"
    r = await admin.send("PUT", "/v1/actions/dial", {"cell": "0-1", "level": 2})
    assert r.status_code == 422 and r.json()["code"] == "dial_cap"


async def test_the_autonomy_dial_is_admin_only_and_audited(lead, admin):
    r = await lead.send("PUT", "/v1/actions/dial", {"cell": "0-1", "level": 1})
    assert r.status_code == 403
    before = next(c for c in (await admin.get("/v1/actions")).json()["cells"] if c["cell"] == "0-1")["dial"]
    level = 0 if before == 1 else 1
    r = await admin.send("PUT", "/v1/actions/dial", {"cell": "0-1", "level": level})
    assert r.status_code == 200 and r.json() == {"ok": True}
    after = next(c for c in (await admin.get("/v1/actions")).json()["cells"] if c["cell"] == "0-1")
    assert after["dial"] == level
    [last, *_] = await _audit(admin.me["org"]["id"], "autonomy.changed")
    assert last["entity_id"] == "0-1" and last["feed_tone"] == "flag"
    assert last["data"]["to"] == level and last["data"]["from"] in (before, None)
    assert last["summary"].startswith('Autonomy for "Cannot be undone · no money moves" set to ')


async def test_boards(admin):
    boards = (await admin.get("/v1/boards")).json()
    assert boards and "volume24h" in boards[0] and "autoRatePct" in boards[0]
    body = {"name": "Trade Finance!", "provider": "dev", "mailbox": "  Trade-Desk@Bank.Example "}
    r = await admin.send("POST", "/v1/boards", body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["authorizeUrl"] is None
    board = out["board"]
    assert board["key"].startswith("trade-finance-") and board["state"] == "observe"
    assert board["source"] == "trade-desk@bank.example" and board["team"] == "Not staffed yet"
    assert any(b["id"] == board["id"] for b in (await admin.get("/v1/boards")).json())
    again = await admin.send("POST", "/v1/boards", body)
    assert again.status_code == 409 and again.json()["code"] == "mailbox_exists"
    r = await admin.send(
        "POST", "/v1/boards", {**body, "mailbox": "t2@bank.example", "departmentId": MISSING}
    )
    assert r.status_code == 404


async def test_agents_versions_and_boards(admin, lead):
    overview = (await admin.get("/v1/agents")).json()
    assert overview["templates"] and overview["models"]
    assert "costPer1kMinor" in overview["agents"][0]
    boards = [b["id"] for b in (await admin.get("/v1/boards")).json()][:2]
    body = {
        "name": "Trade Drafter",
        "template": "drafting",
        "model": overview["models"][-1]["id"],
        "prompt": "Draft from approved sources only.",
        "boardIds": boards,
    }
    assert (await admin.send("POST", "/v1/agents", body)).status_code == 200
    assert (await admin.send("POST", "/v1/agents", body)).json()["code"] == "agent_exists"
    agent = next(a for a in (await admin.get("/v1/agents")).json()["agents"] if a["name"] == "Trade Drafter")
    assert agent["abbr"] == "TD" and agent["state"] == "observe" and agent["version"] == 1
    assert {b["id"] for b in agent["boards"]} == set(boards)
    assert [e["label"] for e in agent["evals"]] == ["Golden set run", "Live traffic"]
    r = await admin.send("POST", f"/v1/agents/{agent['id']}/versions", {"prompt": "Tighter prompt."})
    assert r.json() == {"version": 2}
    r = await admin.send("PUT", f"/v1/agents/{agent['id']}/boards", {"boardIds": boards[:1]})
    assert r.status_code == 200
    agent = next(a for a in (await admin.get("/v1/agents")).json()["agents"] if a["id"] == agent["id"])
    assert [b["id"] for b in agent["boards"]] == boards[:1]
    assert [v["version"] for v in agent["versions"]] == [2, 1]
    r = await admin.send("PUT", f"/v1/agents/{agent['id']}/boards", {"boardIds": [MISSING]})
    assert r.status_code == 404
    assert (await admin.send("POST", f"/v1/agents/{MISSING}/versions", {"prompt": "x"})).status_code == 404
    # Leads see insights, so they can queue tuning from feedback.
    fb = overview["feedback"][0]
    assert (await lead.send("POST", f"/v1/feedback/{fb['id']}/queue")).status_code == 200
    queued = next(f for f in (await admin.get("/v1/agents")).json()["feedback"] if f["id"] == fb["id"])
    assert queued["status"] == "queued"


async def test_action_templates_take_the_next_free_code(admin):
    first = await admin.send(
        "POST", "/v1/actions/templates", {"name": "Close account", "system": "CBS", "cell": "0-1"}
    )
    second = await admin.send(
        "POST", "/v1/actions/templates", {"name": "Refund fee", "system": "CBS", "cell": "1-0"}
    )
    a, b = first.json(), second.json()
    assert a["code"].startswith("ACT-NEW-") and int(b["code"][-3:]) == int(a["code"][-3:]) + 1
    assert a["approval"] == "dual" and a["state"] == "pending_review" and a["cell"] == "0-1"
    codes = [t["code"] for t in (await admin.get("/v1/actions")).json()["templates"]]
    assert a["code"] in codes and b["code"] in codes


async def test_priority_rules_and_proposed_rules(admin, staff):
    rules = (await admin.get("/v1/policies")).json()
    hard = next(r for r in rules["priorityRules"] if r["hard"])
    soft = next(r for r in rules["priorityRules"] if not r["hard"])
    r = await admin.send("PATCH", f"/v1/policies/priority-rules/{hard['id']}", {"enabled": False})
    assert r.status_code == 403 and r.json()["code"] == "hard_rule"
    r = await admin.send(
        "PATCH", f"/v1/policies/priority-rules/{soft['id']}", {"enabled": not soft["enabled"]}
    )
    assert r.status_code == 200
    after = next(
        x for x in (await admin.get("/v1/policies")).json()["priorityRules"] if x["id"] == soft["id"]
    )
    assert after["enabled"] is (not soft["enabled"])

    from command_inbox.db.engine import tenant_tx

    org = admin.me["org"]["id"]
    async with tenant_tx(org) as tx:
        pid = (
            await tx.execute(
                text(
                    "insert into proposed_rules (org_id, text, proposed_by, proposed_by_name)"
                    " values (:org, 'Any mention of a court order', :u, 'P. Sharma') returning id::text"
                ),
                {"org": org, "u": staff.me["user"]["id"]},
            )
        ).scalar_one()
    r = await admin.send("POST", f"/v1/policies/proposed/{pid}/decide", {"approve": True})
    assert r.status_code == 200
    rules = (await admin.get("/v1/policies")).json()
    assert any(
        b["description"] == "Any mention of a court order" and b["kind"] == "Hard stop"
        for b in rules["bucketRules"]
    )
    assert next(p for p in rules["proposedRules"] if p["id"] == pid)["status"] == "approved"
    r = await admin.send("POST", f"/v1/policies/proposed/{pid}/decide", {"approve": False})
    assert r.status_code == 409 and r.json()["code"] == "already_decided"


async def test_knowledge_sync_enqueues_a_job_that_the_worker_completes(admin):
    from command_inbox.core.clock import clock
    from command_inbox.db.engine import global_tx
    from command_inbox.worker import build_worker

    r = await admin.send("POST", "/v1/knowledge/sources", {"kind": "Confluence", "name": "Ops wiki"})
    assert r.status_code == 200, r.text
    src = r.json()
    assert src["name"] == "Ops wiki"
    listed = next(s for s in (await admin.get("/v1/knowledge")).json()["sources"] if s["id"] == src["id"])
    assert listed["health"] == "warn" and listed["sync"] == "Connecting… first sync queued"
    async with global_tx() as tx:
        state = (
            await tx.execute(
                text("select state from jobs where kind = 'knowledge_sync' and dedupe_key = :k"),
                {"k": f"ksync:{src['id']}:first"},
            )
        ).scalar_one()
    assert state == "queued"

    clock.advance(5)  # the first sync is scheduled a few seconds out
    try:
        await build_worker().drain()
    finally:
        clock.reset()
    async with global_tx() as tx:
        state = (
            await tx.execute(
                text("select state from jobs where kind = 'knowledge_sync' and dedupe_key = :k"),
                {"k": f"ksync:{src['id']}:first"},
            )
        ).scalar_one()
    assert state == "done"
    synced = next(s for s in (await admin.get("/v1/knowledge")).json()["sources"] if s["id"] == src["id"])
    assert synced["health"] == "ok" and synced["docs"] == f"{12 + len('Ops wiki') % 20} documents"
    assert synced["sync"].startswith("Synced ") and "pending approval" in synced["note"]

    r = await admin.send("POST", f"/v1/knowledge/sources/{src['id']}/sync")
    assert r.json() == {"message": "Sync queued for Ops wiki."}


async def test_a_broken_source_asks_for_reconsent_instead_of_syncing(admin):
    from command_inbox.db.engine import tenant_tx

    async with tenant_tx(admin.me["org"]["id"]) as tx:
        bad = (
            await tx.execute(
                text("select id::text, kind from knowledge_sources where health = 'bad' limit 1")
            )
        ).one()
    r = await admin.send("POST", f"/v1/knowledge/sources/{bad.id}/sync")
    assert r.json() == {"message": f"Re-consent request sent to the {bad.kind} owner."}
    assert await _audit(admin.me["org"]["id"], "knowledge.reconsent_requested")


async def test_gaps_and_taxonomy_owner(admin):
    k = (await admin.get("/v1/knowledge")).json()
    gap = next(g for g in k["gaps"] if g["age"] != "closed")
    r = await admin.send("POST", f"/v1/knowledge/gaps/{gap['id']}/act")
    assert r.status_code == 200 and gap["number"] in r.json()["message"]
    assert (await admin.send("POST", f"/v1/knowledge/gaps/{MISSING}/act")).status_code == 404

    tax = (await admin.get("/v1/taxonomy")).json()
    assert tax["total"] >= tax["owned"] and len(tax["contract"]) == 4
    dept = next(d for d in tax["departments"] if d["owner"])
    r = await admin.send("PUT", f"/v1/taxonomy/departments/{dept['id']}/owner", {})
    if r.status_code == 200:
        owner = r.json()["owner"]
        after = next(
            d for d in (await admin.get("/v1/taxonomy")).json()["departments"] if d["id"] == dept["id"]
        )
        assert after["owner"]["name"] == owner != dept["owner"]["name"]
    else:
        assert r.status_code == 409 and r.json()["code"] == "no_candidate"
    r = await admin.send("PUT", f"/v1/taxonomy/departments/{dept['id']}/owner", {"userId": MISSING})
    assert r.status_code == 403 and r.json()["code"] == "clearance_required"
