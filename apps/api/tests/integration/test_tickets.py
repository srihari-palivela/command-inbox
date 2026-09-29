"""Ticket workspace: board and inbox queries, the detail DTO, and every ticket command.

Mutating tests work on tickets they create themselves, so they do not depend on (or disturb) the seeded
tickets other modules' tests use.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from tests.integration.conftest import sign_in

pytestmark = pytest.mark.integration


async def make_ticket(
    org_id: str,
    *,
    department: str | None = "Retail Service Desk",
    customer_cif: str | None = "CIF 9001772",
    status: str = "with_human",
    lane: str = "manual",
    priority: str = "P3",
    body: str = "Please send me my account statement for June.",
    subject: str = "Statement request",
    assignee_email: str | None = None,
    subtasks: Sequence[tuple[str, str, str]] = (),
) -> str:
    """Insert a fresh ticket (with one inbound message) and return its number, e.g. "QRY-48230"."""
    from command_inbox.core.clock import clock
    from command_inbox.db.engine import tenant_tx
    from command_inbox.db.models import Customer, Department, Message, Subtask, Ticket, User
    from command_inbox.modules.tickets.ops import next_number

    async with tenant_tx(org_id) as tx:
        dept_id = (
            (
                await tx.execute(
                    select(Department.id).where(Department.org_id == org_id, Department.name == department)
                )
            ).scalar_one()
            if department
            else None
        )
        cust_id = (
            (
                await tx.execute(
                    select(Customer.id).where(Customer.org_id == org_id, Customer.cif == customer_cif)
                )
            ).scalar_one()
            if customer_cif
            else None
        )
        assignee = (
            (await tx.execute(select(User.id).where(User.email == assignee_email))).scalar_one()
            if assignee_email
            else None
        )
        now = clock.now()
        n = await next_number(tx, org_id, "ticket")
        t = Ticket(
            org_id=org_id,
            number=n,
            subject=subject,
            from_name="Joseph Mathew",
            from_email="joseph.mathew@example.com",
            received_at=now - timedelta(minutes=30),
            lane=lane,
            original_lane=lane,
            status=status,
            priority=priority,
            owner_kind="user" if assignee else "unassigned",
            assignee_id=assignee,
            sla_minutes=24 * 60,
            due_at=now + timedelta(hours=20),
            department_id=dept_id,
            customer_id=cust_id,
            bucket="Statements",
        )
        tx.add(t)
        await tx.flush()
        tx.add(
            Message(
                org_id=org_id,
                ticket_id=t.id,
                direction="inbound",
                from_name=t.from_name,
                from_addr=t.from_email,
                to_addr="service@apex.example",
                body=body,
                sent_at=t.received_at,
            )
        )
        for i, (key, label, owner) in enumerate(subtasks):
            tx.add(Subtask(org_id=org_id, ticket_id=t.id, key=key, label=label, owner=owner, sort=i))
    return f"QRY-{n}"


async def audit_actions(org_id: str, ticket_id: str) -> list[str]:
    from command_inbox.db.engine import tenant_tx

    async with tenant_tx(org_id) as tx:
        found = await tx.execute(
            text("select action from audit_events where ticket_id = :t order by seq"), {"t": ticket_id}
        )
        return [r[0] for r in found]


def org(c: Any) -> str:
    return c.me["org"]["id"]


# ── Queries ──────────────────────────────────────────────────────────────────


async def test_board_lists_with_faceted_counts(staff):
    r = await staff.get("/v1/tickets")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(body["items"]) == body["all"]
    assert set(body["facets"]) == {"pri", "bucket", "status", "lane", "team", "owner", "due", "conf"}
    assert body["boards"] and body["departments"]
    item = body["items"][0]
    assert {"number", "sla", "pendingGate", "fromInitials", "boardName"} <= set(item)
    # Merged tickets and old history are never on the board.
    assert "QRY-41022" not in {t["number"] for t in body["items"]}

    drafts = (await staff.get("/v1/tickets", params={"lane": "draft"})).json()
    assert drafts["items"] and all(t["lane"] == "draft" for t in drafts["items"])
    # A facet value's count predicts what selecting it shows (counted against the other filters).
    assert drafts["facets"]["lane"]["draft"] == body["facets"]["lane"]["draft"] == drafts["total"]

    q = (await staff.get("/v1/tickets", params={"q": "qry-48199"})).json()
    assert [t["number"] for t in q["items"]] == ["QRY-48199"]


async def test_board_filter_values_are_validated(staff):
    r = await staff.get("/v1/tickets", params={"lane": "sideways"})
    assert r.status_code == 400
    assert r.json()["code"] == "validation"


async def test_inbox_is_my_open_work_first(staff):
    r = await staff.get("/v1/inbox")
    assert r.status_code == 200, r.text
    body = r.json()
    numbers = [t["number"] for t in body["items"]]
    assert "QRY-48199" in numbers
    assert body["counts"]["all"] == sum(1 for t in body["items"] if t["status"] not in ("resolved", "closed"))
    manual = (await staff.get("/v1/inbox", params={"filter": "manual"})).json()
    assert all(t["lane"] == "manual" for t in manual["items"])
    assert (await staff.get("/v1/inbox", params={"filter": "nope"})).status_code == 400


async def test_detail_by_number_and_by_id(staff):
    d = await staff.ticket("QRY-48199")
    assert d["number"] == "QRY-48199"
    assert d["messages"] and d["gate"]["mode"] == "manual"
    assert d["allowedTransitions"] == ["waiting_customer", "resolved"]
    assert d["customer"]["name"] == "Fatima Sheikh"
    assert {p["number"] for p in d["customer"]["history"]} >= {"QRY-48012", "QRY-41022"}
    assert d["permissions"] == {"canWork": True, "canAssign": False, "canOverrideUp": False, "canReply": True}
    same = await staff.ticket(d["id"])
    assert same["id"] == d["id"] and same["version"] == d["version"]
    # The full contract: every key the web app reads is present.
    for key in (
        "triage",
        "action",
        "draft",
        "brief",
        "trace",
        "subtasks",
        "log",
        "watchers",
        "links",
        "gate",
    ):
        assert key in d


async def test_detail_of_an_auto_ticket_carries_action_and_gate(staff, lead):
    d = await staff.ticket("QRY-48211")
    assert d["action"] is not None and d["action"]["cell"] in ("0-0", "0-1", "1-0", "1-1")
    assert d["gate"]["mode"] == "action"
    assert d["trace"] is None or d["trace"]["spans"] is not None
    assert (await lead.ticket("QRY-48211"))["permissions"]["canAssign"] is True


async def test_unknown_or_malformed_ids_are_not_found(staff):
    for ref in ("QRY-1", "not-a-ticket", "00000000-0000-0000-0000-000000000000"):
        r = await staff.get(f"/v1/tickets/{ref}")
        assert r.status_code == 404, ref
        assert r.json()["code"] == "not_found"


async def test_a_ticket_from_another_workspace_is_not_found(app, staff):
    apex_ticket = await staff.ticket("QRY-48199")
    other = next(m for m in staff.me["memberships"] if m["org"]["id"] != org(staff))
    moved = await sign_in(app, "p.sharma@bank.example")
    sw = await moved.send("POST", "/v1/session/org", {"orgId": other["org"]["id"]})
    assert sw.status_code == 200, sw.text
    assert (await moved.get(f"/v1/tickets/{apex_ticket['id']}")).status_code == 404
    assert (await moved.get("/v1/tickets/QRY-48199")).status_code == 404
    listed = (await moved.get("/v1/tickets")).json()
    assert "QRY-48199" not in {t["number"] for t in listed["items"]}


# ── Transitions ──────────────────────────────────────────────────────────────


async def test_transitions_follow_the_state_machine(staff):
    number = await make_ticket(org(staff))
    before = await staff.ticket(number)

    bad = await staff.send("POST", f"/v1/tickets/{number}/transition", {"to": "closed"})
    assert bad.status_code == 409
    assert bad.json()["code"] == "illegal_transition"
    assert bad.json()["title"] == 'A ticket cannot move from "With a human" to "Closed" directly.'

    stale = await staff.send(
        "POST", f"/v1/tickets/{number}/transition", {"to": "waiting_customer"}, {"if-match": "999"}
    )
    assert stale.status_code == 409 and stale.json()["code"] == "stale_version"

    ok = await staff.send(
        "POST",
        f"/v1/tickets/{number}/transition",
        {"to": "waiting_customer"},
        {"if-match": str(before["version"])},
    )
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    paused = await staff.ticket(number)
    assert paused["status"] == "waiting_customer" and paused["sla"]["tone"] == "paused"
    assert paused["version"] == before["version"] + 1
    assert paused["nextMove"] == "Waiting on the customer — clock paused"
    assert paused["log"][0]["kind"] == "system"
    assert "from With a human to Waiting on customer" in paused["log"][0]["body"]

    for to in ("with_human", "resolved", "closed"):
        r = await staff.send("POST", f"/v1/tickets/{number}/transition", {"to": to})
        assert r.status_code == 200, (to, r.text)
    closed = await staff.ticket(number)
    assert closed["status"] == "closed" and closed["resolvedAt"] is not None
    assert closed["allowedTransitions"] == ["with_human"]

    assert (
        await staff.send("POST", f"/v1/tickets/{number}/transition", {"to": "with_human"})
    ).status_code == 200
    reopened = await staff.ticket(number)
    assert reopened["reopenCount"] == 1 and reopened["resolvedAt"] is None
    assert (await audit_actions(org(staff), reopened["id"])).count("ticket.transitioned") == 5


async def test_transition_needs_resolve_clearance(staff):
    number = await make_ticket(org(staff), department="Cards")  # p.sharma: "Can read" on Cards
    r = await staff.send("POST", f"/v1/tickets/{number}/transition", {"to": "resolved"})
    assert r.status_code == 403
    assert r.json()["code"] == "clearance_required"


async def test_transition_body_is_validated(staff):
    r = await staff.send("POST", "/v1/tickets/QRY-48199/transition", {"to": "flying"})
    assert r.status_code == 400


# ── Assignment ───────────────────────────────────────────────────────────────


async def test_assignment_is_for_leads_and_respects_clearance(staff, lead):
    number = await make_ticket(org(staff), department="Chargeback & Disputes")
    r = await staff.send("POST", f"/v1/tickets/{number}/assign", {"userId": staff.me["user"]["id"]})
    assert r.status_code == 403 and r.json()["code"] == "capability_required"

    kulkarni = await user_id("d.kulkarni@bank.example")  # no clearance for Chargeback & Disputes
    uncleared = await lead.send("POST", f"/v1/tickets/{number}/assign", {"userId": kulkarni})
    assert uncleared.status_code == 403 and uncleared.json()["code"] == "clearance_required"

    ok = await lead.send("POST", f"/v1/tickets/{number}/assign", {"userId": staff.me["user"]["id"]})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"assignee": "P. Sharma", "reason": "assigned by a team lead"}
    d = await lead.ticket(number)
    assert d["assignee"]["id"] == staff.me["user"]["id"] and d["ownerKind"] == "user"
    assert "ticket.assigned" in await audit_actions(org(lead), d["id"])

    # Everyone else cleared for disputes is busy or away, and the current assignee is excluded.
    none_left = await lead.send("POST", f"/v1/tickets/{number}/assign", {})
    assert none_left.status_code == 409 and none_left.json()["code"] == "no_candidate"

    desk = await make_ticket(
        org(lead), department="Retail Service Desk", assignee_email="p.sharma@bank.example"
    )
    auto = await lead.send("POST", f"/v1/tickets/{desk}/assign", {})
    assert auto.status_code == 200, auto.text
    assert auto.json() == {
        "assignee": "A. Fernandes",
        "reason": "closest skill match for statements, lightest live load in Retail Service Desk",
    }

    missing = await lead.send(
        "POST", f"/v1/tickets/{number}/assign", {"userId": "00000000-0000-0000-0000-000000000000"}
    )
    assert missing.status_code == 404


async def user_id(email: str) -> str:
    from command_inbox.db.engine import global_tx

    async with global_tx() as tx:
        return str(
            (await tx.execute(text("select id from users where email = :e"), {"e": email})).scalar_one()
        )


async def test_auto_assign_without_candidates_is_a_conflict(lead):
    number = await make_ticket(org(lead), department=None)
    r = await lead.send("POST", f"/v1/tickets/{number}/assign", {})
    assert r.status_code == 409 and r.json()["code"] == "no_candidate"


# ── Comments, watch, sub-tasks, suggestions ──────────────────────────────────


async def test_comments_add_to_the_log_and_logged_time(staff):
    number = await make_ticket(org(staff))
    before = await staff.ticket(number)
    r = await staff.send(
        "POST", f"/v1/tickets/{number}/comments", {"kind": "note", "body": "  Called branch.  "}
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    after = await staff.ticket(number)
    assert after["log"][0]["body"] == "Called branch." and after["log"][0]["kind"] == "note"
    assert after["log"][0]["authorName"] == "P. Sharma"
    assert after["loggedMinutes"] == before["loggedMinutes"] + 2
    assert (
        await staff.send("POST", f"/v1/tickets/{number}/comments", {"kind": "public", "body": "Hi"})
    ).status_code == 200
    assert (
        await staff.send("POST", f"/v1/tickets/{number}/comments", {"kind": "note", "body": "   "})
    ).status_code == 400
    assert {"ticket.noted", "ticket.replied"} <= set(await audit_actions(org(staff), after["id"]))


async def test_watch_and_unwatch(staff):
    number = await make_ticket(org(staff))
    assert (await staff.send("PUT", f"/v1/tickets/{number}/watch", {"watching": True})).status_code == 200
    d = await staff.ticket(number)
    assert d["watching"] is True and d["watchers"][0]["name"] == "P. Sharma"
    assert (await staff.send("PUT", f"/v1/tickets/{number}/watch", {"watching": True})).status_code == 200
    assert (await staff.send("PUT", f"/v1/tickets/{number}/watch", {"watching": False})).status_code == 200
    assert (await staff.ticket(number))["watching"] is False


async def test_watch_needs_csrf(staff):
    r = await staff.http.put("/v1/tickets/QRY-48199/watch", json={"watching": True})
    assert r.status_code == 403 and r.json()["code"] == "csrf"


async def test_subtasks_owned_by_the_ai_cannot_be_ticked(staff):
    number = await make_ticket(
        org(staff), subtasks=[("c1", "Check mandate", "You"), ("a1", "Fetch file", "AI")]
    )
    ok = await staff.send("PATCH", f"/v1/tickets/{number}/subtasks/c1", {"done": True})
    assert ok.status_code == 200
    assert [s["done"] for s in (await staff.ticket(number))["subtasks"]] == [True, False]
    ai = await staff.send("PATCH", f"/v1/tickets/{number}/subtasks/a1", {"done": True})
    assert ai.status_code == 403
    assert (await staff.send("PATCH", f"/v1/tickets/{number}/subtasks/zz", {"done": True})).status_code == 404


async def test_starting_a_brief_suggestion_makes_a_subtask(staff):
    from command_inbox.db.engine import tenant_tx
    from command_inbox.db.models import Brief

    number = await make_ticket(org(staff))
    ticket_id = (await staff.ticket(number))["id"]
    async with tenant_tx(org(staff)) as tx:
        tx.add(
            Brief(
                org_id=org(staff),
                ticket_id=ticket_id,
                why="w",
                summary="s",
                context=[],
                suggestions=[{"label": "Call the customer", "meta": "recommended"}],
            )
        )
    assert (await staff.send("POST", f"/v1/tickets/{number}/suggestions/0/start")).status_code == 200
    d = await staff.ticket(number)
    assert d["subtasks"][-1] == {"key": "s0", "label": "Call the customer", "owner": "You", "done": False}
    assert d["log"][0]["body"] == "Started: Call the customer (recommended)."
    assert (await staff.send("POST", f"/v1/tickets/{number}/suggestions/3/start")).status_code == 404


# ── Escalate, override, split, merge ────────────────────────────────────────


async def test_escalation_raises_priority_and_shows_in_the_activity_rail(staff):
    number = await make_ticket(org(staff), department="Chargeback & Disputes", priority="P3")
    r = await staff.send("POST", f"/v1/tickets/{number}/escalate")
    assert r.status_code == 200, r.text
    to = r.json()["to"]
    d = await staff.ticket(number)
    assert d["priority"] == "P2"
    assert d["log"][0]["body"].startswith(f"P. Sharma escalated to {to}")
    feed = (await staff.get("/v1/activity")).json()
    assert any(a["ticketNumber"] == number and a["tone"] == "stop" for a in feed)


async def test_lane_override_up_needs_a_lead(staff, lead):
    number = await make_ticket(org(staff), lane="manual")
    up = await staff.send("POST", f"/v1/tickets/{number}/override-lane", {"lane": "draft"})
    assert up.status_code == 403 and up.json()["code"] == "capability_required"
    ok = await lead.send("POST", f"/v1/tickets/{number}/override-lane", {"lane": "draft"})
    assert ok.status_code == 200, ok.text
    d = await lead.ticket(number)
    assert (
        d["lane"] == "draft"
        and d["status"] == "triaging"
        and d["laneNote"].startswith("Overridden by R. Menon")
    )

    down = await staff.send("POST", f"/v1/tickets/{number}/override-lane", {"lane": "manual"})
    assert down.status_code == 200
    d = await staff.ticket(number)
    assert d["lane"] == "manual" and d["status"] == "with_human"
    assert (await audit_actions(org(staff), d["id"])).count("ticket.lane_overridden") == 2


async def test_split_creates_children_that_are_triaged_separately(staff):
    number = await make_ticket(
        org(staff),
        body="Two things. My debit card was blocked abroad. Separately, please send my June statement.",
        subtasks=[("c2", "Split the request", "You")],
    )
    r = await staff.send("POST", f"/v1/tickets/{number}/split")
    assert r.status_code == 200, r.text
    children = r.json()["children"]
    assert len(children) == 2
    parent = await staff.ticket(number)
    assert parent["status"] == "resolved" and parent["splitProposed"] is False
    assert parent["subtasks"][0]["done"] is True
    assert [lk["kind"] for lk in parent["links"]] == ["CHILD", "CHILD"]
    first = await staff.ticket(children[0])
    assert first["subject"] == f"My debit card was blocked abroad. (split from {number})"
    assert first["status"] == "triaging" and first["lane"] == "manual"
    assert first["links"][0] == {"kind": "PARENT", "label": f"{number} · Statement request", "ref": number}
    second = await staff.ticket(children[1])
    assert second["messages"][0]["body"] == "Separately, please send my June statement."

    from command_inbox.db.engine import global_tx

    async with global_tx() as tx:
        queued = (
            await tx.execute(
                text("select count(*) from jobs where kind = 'triage' and dedupe_key = any(:k)"),
                {"k": [f"triage:{first['id']}", f"triage:{second['id']}"]},
            )
        ).scalar_one()
    assert queued == 2


async def test_split_refuses_a_single_request(staff):
    number = await make_ticket(org(staff), body="Statement please.")
    r = await staff.send("POST", f"/v1/tickets/{number}/split")
    assert r.status_code == 400 and r.json()["code"] == "cannot_split"


async def test_merge_moves_the_thread_for_the_same_customer_only(staff):
    source = await make_ticket(org(staff), body="First email")
    target = await make_ticket(org(staff), body="Second email")
    other = await make_ticket(org(staff), customer_cif="CIF 1180342")

    assert (await staff.send("POST", f"/v1/tickets/{source}/merge", {"intoNumber": source})).json()[
        "code"
    ] == "same_ticket"
    diff = await staff.send("POST", f"/v1/tickets/{source}/merge", {"intoNumber": other})
    assert diff.status_code == 409 and diff.json()["code"] == "different_customer"
    assert (
        await staff.send("POST", f"/v1/tickets/{source}/merge", {"intoNumber": "QRY-9"})
    ).status_code == 404
    assert (
        await staff.send("POST", f"/v1/tickets/{source}/merge", {"intoNumber": "48199"})
    ).status_code == 400

    ok = await staff.send("POST", f"/v1/tickets/{source}/merge", {"intoNumber": target})
    assert ok.status_code == 200, ok.text
    merged = await staff.ticket(source)
    assert merged["status"] == "closed" and merged["messages"] == []
    into = await staff.ticket(target)
    assert {m["body"] for m in into["messages"]} == {"First email", "Second email"}
    assert into["links"][-1]["kind"] == "MERGED"
    board = (await staff.get("/v1/tickets")).json()
    assert source not in {t["number"] for t in board["items"]}


# ── Roles ────────────────────────────────────────────────────────────────────


async def test_staff_and_lead_see_different_capabilities_on_the_same_ticket(staff, lead):
    s = (await staff.ticket("QRY-48198"))["permissions"]
    lp = (await lead.ticket("QRY-48198"))["permissions"]
    assert s["canAssign"] is False and s["canOverrideUp"] is False
    assert lp["canAssign"] is True and lp["canOverrideUp"] is True


async def test_shift_strip(staff):
    r = await staff.get("/v1/shift")
    assert r.status_code == 200
    assert set(r.json()) == {"closedToday", "sentAsDraftedPct", "savedMinutes", "missedDeadlines"}
