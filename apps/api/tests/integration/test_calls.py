"""Calls from a ticket: live transcript, wrap-up, save to the ticket or discard."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from command_inbox.core.clock import clock
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import Attachment, AuditEvent, Comment
from tests.integration.gateway_support import ticket_row

pytestmark = pytest.mark.integration


async def test_call_lifecycle_saves_recording_and_context(staff):
    org = staff.me["org"]["id"]
    r = await staff.send("POST", "/v1/tickets/QRY-48199/calls")
    assert r.status_code == 200, r.text
    call = r.json()
    assert call["state"] == "dialing" and call["transcript"] == [] and call["scriptLength"] == 6
    assert call["number"].startswith("+91 •••• ••") and call["customerName"]

    clock.advance(1.4 + 2.6 * 2 + 0.1)  # dialled, then three lines revealed
    try:
        live = (await staff.get(f"/v1/calls/{call['id']}")).json()
        assert live["state"] == "live" and len(live["transcript"]) == 3 and live["durationSec"] == 5
        early = await staff.send("POST", f"/v1/calls/{call['id']}/save", {"discard": False})
        assert early.status_code == 409 and early.json()["code"] == "not_wrapped"
        ended = (await staff.send("POST", f"/v1/calls/{call['id']}/end")).json()
    finally:
        clock.reset()
    assert ended["state"] == "wrap" and ended["summary"] and len(ended["updates"]) == 3
    assert ended["recording"].startswith("call-qry-48199.mp3")
    again = await staff.send("POST", f"/v1/calls/{call['id']}/end")
    assert again.status_code == 409 and again.json()["code"] == "not_live"

    before = await ticket_row(org, "QRY-48199")
    assert (await staff.send("POST", f"/v1/calls/{call['id']}/save", {"discard": False})).status_code == 200
    t = await ticket_row(org, "QRY-48199")
    assert t.logged_minutes == before.logged_minutes + 1 and t.sentiment == "de-escalating"
    async with tenant_tx(org) as tx:
        kinds = (await tx.execute(select(Comment.kind).where(Comment.ticket_id == t.id))).scalars().all()
        exts = (await tx.execute(select(Attachment.ext).where(Attachment.ticket_id == t.id))).scalars().all()
        saved = (
            await tx.execute(
                select(AuditEvent.summary).where(
                    AuditEvent.ticket_id == t.id, AuditEvent.action == "call.saved"
                )
            )
        ).scalar_one()
    assert "call" in kinds and {"MP3", "TXT"} <= set(exts)
    assert saved.startswith(staff.me["user"]["name"])
    assert (await staff.get(f"/v1/calls/{call['id']}")).json()["state"] == "saved"


async def test_discard_and_unknown_call(staff):
    call = (await staff.send("POST", "/v1/tickets/QRY-48204/calls")).json()
    await staff.send("POST", f"/v1/calls/{call['id']}/end")
    assert (await staff.send("POST", f"/v1/calls/{call['id']}/save", {"discard": True})).status_code == 200
    assert (await staff.get(f"/v1/calls/{call['id']}")).json()["state"] == "discarded"
    missing = await staff.get("/v1/calls/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert (await staff.get("/v1/calls/not-a-uuid")).status_code == 400
    assert (await staff.send("POST", "/v1/tickets/QRY-1/calls")).status_code == 404
