"""Global search, the natural-language filter box and the copilot (heuristic provider in tests)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.conftest import Client, sign_in

pytestmark = pytest.mark.integration


async def search(c: Client, q: str) -> dict[str, Any]:
    r = await c.get("/v1/search", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


async def test_short_queries_return_nothing(staff):
    assert await search(staff, " a ") == {"tickets": [], "customers": [], "knowledge": [], "policies": []}
    assert await search(staff, "") == {"tickets": [], "customers": [], "knowledge": [], "policies": []}


async def test_search_finds_tickets_by_number_and_customers_by_name(staff):
    for q in ("48199", "qry-48199", "QRY-48199"):
        assert "QRY-48199" in [t["number"] for t in (await search(staff, q))["tickets"]], q
    found = await search(staff, "Fatima")
    fatima = next(c for c in found["customers"] if c["name"] == "Fatima Sheikh")
    assert fatima["tickets"] == 5 and fatima["cif"] == "CIF 2290844"
    assert fatima["latestTicketId"] == (await staff.ticket("QRY-48199"))["id"]
    assert found["tickets"] and all(set(t) == {"id", "number", "subject", "lane"} for t in found["tickets"])


async def test_search_reaches_policies_and_templates(admin):
    body = await search(admin, "ACT")
    kinds = {p["kind"] for p in body["policies"]}
    assert "Action template" in kinds
    assert all(" · " in p["text"] for p in body["policies"] if p["kind"] == "Action template")


async def test_like_wildcards_are_literal(staff):
    assert (await search(staff, "%%"))["tickets"] == []


async def test_search_is_tenant_isolated(app, staff):
    assert (await search(staff, "Fatima"))["customers"]
    other = next(m for m in staff.me["memberships"] if m["org"]["slug"] == "northwind")
    moved = await sign_in(app, "p.sharma@bank.example")
    assert (await moved.send("POST", "/v1/session/org", {"orgId": other["org"]["id"]})).status_code == 200
    elsewhere = await search(moved, "Fatima")
    assert elsewhere["customers"] == [] and elsewhere["tickets"] == []
    assert (await search(moved, "48199"))["tickets"] == []
    assert [t["number"] for t in (await search(moved, "3301"))["tickets"]] == ["QRY-3301"]


async def test_search_query_length_is_bounded(staff):
    assert (await staff.get("/v1/search", params={"q": "x" * 201})).status_code == 400


async def test_natural_language_filters(staff):
    r = await staff.send("POST", "/v1/tickets/nl-filter", {"query": "late P1 tickets assigned to me"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filters"] == {"owner": "mine", "due": "risk", "pri": "P1"}  # unset keys are omitted
    assert [c["text"] for c in body["chips"]] == ["Owner: me", "Running late", "Priority: P1"]
    assert body["understood"] is True and body["provider"] == "heuristic"

    team = (await staff.send("POST", "/v1/tickets/nl-filter", {"query": "chargeback disputes"})).json()
    assert team["chips"][0]["text"] == "Team: Chargeback & Disputes"
    nothing = (await staff.send("POST", "/v1/tickets/nl-filter", {"query": "zzz"})).json()
    assert nothing == {"filters": {}, "chips": [], "understood": False, "provider": "heuristic"}
    assert (await staff.send("POST", "/v1/tickets/nl-filter", {"query": "  "})).status_code == 400


async def ask(c: Client, question: str) -> dict[str, Any]:
    r = await c.send("POST", "/v1/copilot/ask", {"question": question})
    assert r.status_code == 200, r.text
    return r.json()


async def test_copilot_answers_from_the_live_queue(staff):
    late = await ask(staff, "What is running late?")
    assert late["provider"] == "heuristic"
    assert "inside the warning band" in late["headline"]
    assert late["actions"][0]["filters"] == {"due": "risk"} and "to" not in late["actions"][0]

    approvals = await ask(staff, "What needs approval?")
    assert "waiting for a human decision" in approvals["headline"]
    assert approvals["actions"] == [{"label": "Show what needs approving", "filters": {"status": "approval"}}]

    low = await ask(staff, "Where is the AI unsure?")
    assert "below the 0.78 bar" in low["headline"]

    default = await ask(staff, "hello")
    assert default["headline"].endswith("open right now.")
    assert (
        default["lines"][-1] == "Ask about deadlines, approvals, confidence or results for a sharper answer."
    )


async def test_copilot_results_depend_on_role(staff, lead, admin):
    s = await ask(staff, "How is the AI doing this week?")
    assert s["headline"] == "Results are visible to team leads and admins." and s["actions"] == []
    lr = await ask(lead, "How is the AI doing this week?")
    assert lr["headline"].startswith("Time to resolve is down")
    assert lr["actions"] == [{"label": "Open Results", "to": "/results"}]

    unowned_staff = await ask(staff, "anything unassigned?")
    assert [a["label"] for a in unowned_staff["actions"]] == ["Show unowned tickets"]
    unowned_admin = await ask(admin, "anything unassigned?")
    assert unowned_admin["actions"][-1] == {"label": "Open Who owns what", "to": "/setup/ownership"}


async def test_copilot_question_is_validated(staff):
    r = await staff.send("POST", "/v1/copilot/ask", {"question": "x" * 501})
    assert r.status_code == 400
