"""Approval gateway: maker/checker separation, undo and recall windows, replies, batch approval, job
execution and final failure, and tenant ownership of ids in paths. Ported from
apps/server/test/integration/api.test.ts plus the review's hardening cases."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from command_inbox.core.clock import clock
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import AuditEvent, Draft, Job, Message, Reply, Ticket
from tests.integration.conftest import sign_in
from tests.integration.gateway_support import action_of, gate, make_action_ticket, ticket_row, worker

pytestmark = pytest.mark.integration

W = worker()


async def _count(org_id: str, sql: str, **params: object) -> int:
    async with tenant_tx(org_id) as tx:
        return int((await tx.execute(text(sql), params)).scalar() or 0)


# ── Irreversible money movement (maker + checker), as in the TS suite ────────────


async def test_maker_approval_is_recorded_replayed_and_waits_for_checker(staff):
    org = staff.me["org"]["id"]
    g = await gate(staff, "QRY-48211")
    assert g["mode"] == "action" and g["chain"] == "dual"
    assert g["reversible"] is False and g["moneyMoves"] is True
    assert g["canApprove"] is True and g["canUndo"] is False
    assert g["proposedChecker"]["name"] == "R. Menon"
    t = await ticket_row(org, "QRY-48211")

    key = {"idempotency-key": "it-48211-maker"}
    first = await staff.send("POST", f"/v1/tickets/{t.id}/gate/approve", {"openedEvidence": True}, key)
    assert first.status_code == 200, first.text
    assert first.json() == {"outcome": "awaiting_checker"}
    replay = await staff.send("POST", f"/v1/tickets/{t.id}/gate/approve", {"openedEvidence": True}, key)
    assert replay.status_code == 200
    assert replay.json() == {"outcome": "awaiting_checker"}
    assert replay.headers.get("idempotent-replay") == "true"

    a = await action_of(org, t.id)
    n = await _count(org, "select count(*) from approvals where subject_id = :s and step = 'maker'", s=a.id)
    assert n == 1


async def test_the_maker_is_never_their_own_checker(staff):
    g = await gate(staff, "QRY-48211")
    assert g["state"] == "awaiting_checker" and g["canApprove"] is False
    r = await staff.send(
        "POST", "/v1/tickets/QRY-48211/gate/approve", {"openedEvidence": True}, {"idempotency-key": "it-self"}
    )
    assert r.status_code >= 400
    assert (await gate(staff, "QRY-48211"))["state"] == "awaiting_checker"


async def test_checker_executes_with_no_undo_and_audit_trail(lead, admin):
    org = lead.me["org"]["id"]
    assert (await gate(lead, "QRY-48211"))["canApprove"] is True
    r = await lead.send(
        "POST", "/v1/tickets/QRY-48211/gate/approve", {"openedEvidence": True}, {"idempotency-key": "it-chk"}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"outcome": "scheduled"}
    await W.drain()
    t = await ticket_row(org, "QRY-48211")
    a = await action_of(org, t.id)
    assert a.state == "executed" and a.external_ref
    assert a.maker_id != a.checker_id
    g = await gate(lead, "QRY-48211")
    assert g["state"] == "done" and g["canUndo"] is False
    assert (await lead.send("POST", f"/v1/tickets/{t.id}/gate/undo")).status_code == 409
    assert t.status == "resolved"
    async with tenant_tx(org) as tx:
        actions = set(
            (await tx.execute(select(AuditEvent.action).where(AuditEvent.ticket_id == t.id))).scalars().all()
        )
    assert {"action.maker_approved", "action.checker_approved", "action.executed"} <= actions


# ── Separation of duties on a fresh dual action ──────────────────────────────────


async def test_lead_as_maker_cannot_check_and_staff_cannot_check(app, lead, admin):
    org = lead.me["org"]["id"]
    tid = await make_action_ticket(org, 48211, "ACT-STP-014", "dual")
    r = await lead.send("POST", f"/v1/tickets/{tid}/gate/approve", {"openedEvidence": True})
    assert r.json() == {"outcome": "awaiting_checker"}

    # The same lead cannot now check it.
    g = await gate(lead, tid)
    assert g["canApprove"] is False and "different person" in g["blockedReason"]
    r = await lead.send("POST", f"/v1/tickets/{tid}/gate/approve", {"openedEvidence": True})
    assert r.status_code == 403 and r.json()["code"] == "maker_checker"

    # Staff — even with "Can approve" clearance in this team — cannot be a checker.
    kulkarni = await sign_in(app, "d.kulkarni@bank.example")
    assert (await gate(kulkarni, tid))["blockedReason"] == "Only a team lead or admin can approve as checker."
    r = await kulkarni.send("POST", f"/v1/tickets/{tid}/gate/approve", {"openedEvidence": True})
    assert r.status_code == 403 and r.json()["code"] == "capability_required"

    # An admin holds the capability but only "Can read" clearance for this team.
    r = await admin.send("POST", f"/v1/tickets/{tid}/gate/approve", {"openedEvidence": True})
    assert r.status_code == 403 and r.json()["code"] == "clearance_required"
    assert (await action_of(org, tid)).state == "awaiting_checker"


async def test_silent_checker_escalates_after_two_hours(lead):
    org = lead.me["org"]["id"]
    tid = await make_action_ticket(org, 48211, "ACT-TD-005", "dual")
    assert (await lead.send("POST", f"/v1/tickets/{tid}/gate/approve", {})).json()[
        "outcome"
    ] == "awaiting_checker"
    clock.advance(121 * 60)
    try:
        await W.drain()
    finally:
        clock.reset()
    t = await ticket_row(org, tid)
    assert t.priority == "P1" and "escalated" in t.next_move
    async with tenant_tx(org) as tx:
        n = (
            await tx.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.ticket_id == tid, AuditEvent.action == "action.checker_escalated")
            )
        ).scalar()
    assert n == 1


async def test_reversible_action_undo_inside_the_window_only(staff, lead):
    org = staff.me["org"]["id"]
    tid = await make_action_ticket(org, 48211, "ACT-FEE-002", "single_undo")
    r = await staff.send("POST", f"/v1/tickets/{tid}/gate/approve", {"openedEvidence": True})
    assert r.json() == {"outcome": "scheduled"}
    g = await gate(staff, tid)
    assert g["state"] == "scheduled" and g["canUndo"] is True and g["undoUntil"]
    assert (await staff.send("POST", f"/v1/tickets/{tid}/gate/undo")).status_code == 200
    a = await action_of(org, tid)
    assert a.state == "drafted" and a.maker_id is None
    async with tenant_tx(org) as tx:
        states = (
            (
                await tx.execute(
                    select(Job.state).where(
                        Job.kind == "execute_action", Job.payload["actionId"].astext == a.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert states == ["cancelled"]

    await staff.send("POST", f"/v1/tickets/{tid}/gate/approve", {"openedEvidence": True})
    clock.advance(31)
    try:
        r = await staff.send("POST", f"/v1/tickets/{tid}/gate/undo")
        assert r.status_code == 409 and r.json()["code"] == "window_closed"
        await W.drain()
    finally:
        clock.reset()
    assert (await action_of(org, tid)).state == "executed"


async def test_edit_action_fields_before_approval(staff):
    org = staff.me["org"]["id"]
    tid = await make_action_ticket(org, 48211, "ACT-FEE-002", "single_undo")
    r = await staff.send(
        "PATCH", f"/v1/tickets/{tid}/action/fields", {"fields": [{"label": "Amount", "value": "250"}]}
    )
    assert r.status_code == 200, r.text
    a = await action_of(org, tid)
    assert a.fields[0]["value"] == "250" and a.fields[0]["source"].startswith("edited by")


async def test_reject_sends_the_work_back_to_the_person(staff):
    org = staff.me["org"]["id"]
    tid = await make_action_ticket(org, 48211, "ACT-FEE-002", "single_undo")
    r = await staff.send("POST", f"/v1/tickets/{tid}/gate/reject", {"reason": "bad_field"})
    assert r.status_code == 200, r.text
    t = await ticket_row(org, tid)
    assert t.lane == "manual" and t.status == "with_human" and t.assignee_id == staff.me["user"]["id"]
    assert (await action_of(org, tid)).state == "rejected"
    assert (await staff.send("POST", f"/v1/tickets/{tid}/gate/reject", {"reason": "tone"})).status_code == 409


# ── Replies are recallable inside the window only ────────────────────────────────


async def test_recalls_a_queued_draft_then_sends_once_the_window_passes(staff, lead):
    org = staff.me["org"]["id"]
    t = await ticket_row(org, "QRY-48207")
    g = await gate(staff, "QRY-48207")
    assert g["mode"] == "draft" and g["state"] == "open"
    async with tenant_tx(org) as tx:
        original = (await tx.execute(select(Draft.current_body).where(Draft.ticket_id == t.id))).scalar_one()
    edited = original + "\n\nKind regards"
    assert (await staff.send("PUT", f"/v1/tickets/{t.id}/draft", {"body": edited})).status_code == 200
    r = await staff.send(
        "POST",
        f"/v1/tickets/{t.id}/gate/approve",
        {"openedEvidence": True},
        {"idempotency-key": "it-48207-1"},
    )
    assert r.json() == {"outcome": "sending"}
    g = await gate(staff, "QRY-48207")
    assert g["state"] == "scheduled" and g["canUndo"] is True

    assert (await staff.send("POST", f"/v1/tickets/{t.id}/gate/undo")).status_code == 200
    assert (await gate(staff, "QRY-48207"))["state"] == "open"
    await W.drain()
    async with tenant_tx(org) as tx:
        assert (await tx.execute(select(Draft.state).where(Draft.ticket_id == t.id))).scalar() == "draft"

    await staff.send(
        "POST",
        f"/v1/tickets/{t.id}/gate/approve",
        {"openedEvidence": True},
        {"idempotency-key": "it-48207-2"},
    )
    clock.advance(61)
    try:
        assert (await staff.send("POST", f"/v1/tickets/{t.id}/gate/undo")).status_code == 409
        await W.drain()
    finally:
        clock.reset()
    async with tenant_tx(org) as tx:
        assert (await tx.execute(select(Draft.state).where(Draft.ticket_id == t.id))).scalar() == "sent"
        sent = (
            (
                await tx.execute(
                    select(Message.body).where(Message.ticket_id == t.id, Message.direction == "outbound")
                )
            )
            .scalars()
            .all()
        )
        edits = (
            await tx.execute(
                text("select count(*) from feedback where ticket_id = :t and kind = 'edit'"), {"t": t.id}
            )
        ).scalar()
    assert edited in sent and edits == 1
    assert (await gate(staff, "QRY-48207"))["state"] == "done"


async def test_free_text_reply_recall_window(staff, lead):
    org = staff.me["org"]["id"]
    r = await staff.send("POST", "/v1/tickets/QRY-48199/replies", {"body": "  We are on it.  "})
    assert r.status_code == 200, r.text
    first = r.json()
    assert set(first) == {"replyId", "sendAfter"} and first["sendAfter"].endswith("Z")
    # Only the author may recall.
    other = await lead.send("POST", f"/v1/replies/{first['replyId']}/recall")
    assert other.status_code == 403
    assert (await staff.send("POST", f"/v1/replies/{first['replyId']}/recall")).status_code == 200
    again = await staff.send("POST", f"/v1/replies/{first['replyId']}/recall")
    assert again.status_code == 409 and again.json()["code"] == "not_undoable"

    second = (await staff.send("POST", "/v1/tickets/QRY-48199/replies", {"body": "Update: done."})).json()
    clock.advance(61)
    try:
        late = await staff.send("POST", f"/v1/replies/{second['replyId']}/recall")
        assert late.status_code == 409 and late.json()["code"] == "window_closed"
        await W.drain()
    finally:
        clock.reset()
    async with tenant_tx(org) as tx:
        states = dict((await tx.execute(select(Reply.id, Reply.state))).all())
        sent = (
            await tx.execute(select(func.count()).select_from(Message).where(Message.body == "Update: done."))
        ).scalar()
    assert states[first["replyId"]] == "recalled" and states[second["replyId"]] == "sent"
    assert sent == 1


async def test_batch_approvals_count_as_approving_without_opening_the_evidence(staff):
    org = staff.me["org"]["id"]
    sql = "select count(*) from approvals where not opened_evidence"
    before = await _count(org, sql)
    t = await ticket_row(org, "QRY-48188")
    r = await staff.send(
        "POST", "/v1/gate/batch-approve", {"ticketIds": [t.id, "00000000-0000-0000-0000-000000000000"]}
    )
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0]["ok"] is True and res[0]["outcome"] == "sending"
    assert res[1] == {
        "ticketId": "00000000-0000-0000-0000-000000000000",
        "ok": False,
        "outcome": None,
        "error": "Ticket not found",
    }
    assert await _count(org, sql) == before + 1


async def test_draft_edit_needs_a_pending_draft(staff):
    r = await staff.send("PUT", "/v1/tickets/QRY-48196/draft", {"body": "Edited body"})  # no draft row
    assert r.status_code == 409 and r.json()["code"] == "not_editable"


# ── Final failure: the ticket goes to a person ───────────────────────────────────


async def test_execution_that_keeps_failing_hands_the_ticket_to_a_person(staff, lead):
    org = staff.me["org"]["id"]
    # A dual action scheduled without a checker: the executor refuses it every time.
    tid = await make_action_ticket(
        org, 48211, "ACT-STP-014", "dual", state="scheduled", maker_id=staff.me["user"]["id"]
    )
    a = await action_of(org, tid)
    from command_inbox.core.jobs import enqueue

    async with tenant_tx(org) as tx:
        await enqueue(
            tx, org, "execute_action", {"actionId": a.id}, dedupe_key=f"exec-test:{a.id}", max_attempts=1
        )
    await W.drain()
    t = await ticket_row(org, tid)
    assert t.lane == "manual" and t.status == "with_human"
    assert (await action_of(org, tid)).state == "failed"
    async with tenant_tx(org) as tx:
        n = (
            await tx.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.ticket_id == tid, AuditEvent.action == "ai.handed_back")
            )
        ).scalar()
    assert n == 1


# ── Identifiers in paths belong to the caller's tenant ───────────────────────────


async def test_another_tenants_ticket_id_is_not_found(staff):
    apex = staff.me["org"]["id"]
    other = next(m["org"]["id"] for m in staff.me["memberships"] if m["org"]["id"] != apex)
    async with tenant_tx(other) as tx:
        foreign = (await tx.execute(select(Ticket.id).limit(1))).scalar_one()
    # RLS: inside Apex's transaction the other bank's ticket does not exist at all.
    async with tenant_tx(apex) as tx:
        assert (await tx.execute(select(Ticket).where(Ticket.id == foreign))).scalar_one_or_none() is None
    for method, path, body in (
        ("POST", f"/v1/tickets/{foreign}/gate/approve", {"openedEvidence": True}),
        ("POST", f"/v1/tickets/{foreign}/gate/reject", {"reason": "tone"}),
        ("POST", f"/v1/tickets/{foreign}/gate/undo", None),
        ("PUT", f"/v1/tickets/{foreign}/draft", {"body": "x"}),
        ("POST", f"/v1/tickets/{foreign}/replies", {"body": "x"}),
        ("POST", f"/v1/tickets/{foreign}/calls", None),
    ):
        r = await staff.send(method, path, body)
        assert r.status_code == 404, (path, r.text)
    r = await staff.send("POST", "/v1/gate/batch-approve", {"ticketIds": [foreign]})
    assert r.json()["results"][0]["ok"] is False


async def test_audit_chain_intact_after_all_of_that(staff):
    from command_inbox.core.audit import verify_audit_chain

    org = staff.me["org"]["id"]
    async with tenant_tx(org) as tx:
        result = await verify_audit_chain(tx, org)
    assert result["ok"] and result["brokenAt"] is None
