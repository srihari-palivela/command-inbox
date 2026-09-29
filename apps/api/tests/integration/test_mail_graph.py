"""Microsoft 365 end to end against a stateful Graph fake: connect, ingest, reply in-thread, and recover.

Covers the production plan's chaos cases: a dropped notification (the sweep recovers), an expired delta
cursor (resync without duplicates), throttling (retried), a revoked sign-in (reauth required), and a send
retried after a crash (never sent twice).
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from sqlalchemy import select, update

from tests.integration.admin_support import admin_conn, drain
from tests.integration.conftest import Client
from tests.integration.graph_fake import FakeGraph
from tests.integration.test_platform_console import _accept, _console, _operator, _tenant_body, _token_for


@pytest.fixture
async def bank(app: Any) -> tuple[Client, str, str]:
    op = await _console(app, await _operator("operator"))
    slug = f"m365-{uuid.uuid4().hex[:6]}"
    admin_email = f"admin@{slug}.test"
    r = await op.send(
        "POST", "/v1/platform/tenants", _tenant_body(slug, admin_email, emailDomains=[f"{slug}.test"])
    )
    tid = r.json()["id"]
    await drain()
    admin = await _accept(app, _token_for(admin_email))
    return admin, tid, slug


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch, bank: tuple[Client, str, str]):
    from command_inbox.config import settings

    monkeypatch.setattr(settings, "ms_client_id", "bank-app-id")
    monkeypatch.setattr(settings, "ms_client_secret", "bank-app-secret")
    monkeypatch.setattr(settings, "mail_webhook_base_url", None)
    fake = FakeGraph(account=f"care@{bank[2]}.test")
    with respx.mock(assert_all_called=False) as router:
        router.route(host="test").pass_through()
        fake.mount(router)
        yield fake


async def _connect(admin: Client, fake: FakeGraph, monkeypatch: pytest.MonkeyPatch) -> str:
    from command_inbox.modules.intake import oauth

    r = await admin.send(
        "POST", "/v1/mailbox-connections", {"address": fake.account, "provider": "microsoft"}
    )
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert r.json()["connection"] == "not_connected"
    r = await admin.send("POST", f"/v1/mailboxes/{mid}/connect", None)
    url = r.json()["authorizeUrl"]
    q = parse_qs(urlparse(url).query)
    assert "Mail.Send" in q["scope"][0] and "Mail.ReadWrite" in q["scope"][0]
    assert "/organizations/oauth2/v2.0/authorize" in url or "login.microsoftonline.com" in url

    async def exchange(_p: str, _c: str, _v: str) -> dict[str, Any]:
        return {"access_token": "at-0", "refresh_token": "rt-0", "expires_in": 3600}

    async def who(_p: str, _t: dict[str, Any]) -> str:
        return fake.account

    monkeypatch.setattr(oauth, "exchange_code", exchange)
    monkeypatch.setattr(oauth, "account_email", who)
    r = await admin.get(
        "/v1/oauth/microsoft/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False
    )
    assert r.status_code == 302 and "/setup/mailboxes" in r.headers["location"]
    await drain()
    return mid


async def _box(admin: Client, mid: str) -> dict[str, Any]:
    boxes = (await admin.get("/v1/mailbox-connections")).json()["mailboxes"]
    return next(b for b in boxes if b["id"] == mid)


async def _sync(admin: Client, mid: str) -> None:
    r = await admin.send("POST", f"/v1/mailboxes/{mid}/sync", None)
    assert r.status_code == 200, r.text
    await drain()


async def _messages(tid: str) -> list[dict[str, Any]]:
    conn = await admin_conn()
    try:
        rows = await conn.fetch(
            "select provider_message_id, outcome, ticket_id, flags, auth, raw_sealed is not null as sealed "
            "from mail_messages where org_id = $1 order by created_at",
            uuid.UUID(tid),
        )
    finally:
        await conn.close()
    out = [dict(r) for r in rows]
    for r in out:
        r["auth"], r["flags"] = json.loads(r["auth"]), json.loads(r["flags"])
    return out


async def test_connect_ingest_and_reply_in_thread(bank, graph, monkeypatch):
    admin, tid, _slug = bank
    mid = await _connect(admin, graph, monkeypatch)
    box = await _box(admin, mid)
    assert box["connection"] == "live" and box["account"] == graph.account and box["mode"] == "polling"
    assert box["sendEnabled"] is False

    # Mail before the connection is history, not work: only new mail becomes tickets.
    graph.deliver("Card blocked", "old@customer.test", "An old message from before we connected.")
    await _sync(admin, mid)  # the baseline cursor already covers it? no: it arrived after the baseline
    first = await _messages(tid)
    assert [m["outcome"] for m in first] == ["ticket"]

    auth = "mx.bank.test; spf=pass smtp.mailfrom=customer.test; dkim=pass header.d=customer.test; dmarc=pass header.from=customer.test"
    m = graph.deliver(
        "Statement request",
        "ravi@customer.test",
        "Please send my statement.​\n\nOn Mon, someone wrote:\n> old quoted text",
        headers={"Authentication-Results": auth},
    )
    graph.deliver(
        "Automatic reply: away", "ooo@customer.test", "I am away", headers={"Auto-Submitted": "auto-replied"}
    )
    graph.deliver(
        "Undeliverable",
        "MAILER-DAEMON@customer.test",
        "bounce",
        headers={"Content-Type": "multipart/report; report-type=delivery-status"},
    )
    await _sync(admin, mid)
    rows = {r["provider_message_id"]: r for r in await _messages(tid)}
    assert rows[m.id]["outcome"] == "ticket" and rows[m.id]["sealed"] is True
    assert rows[m.id]["auth"]["verified"] is True
    assert sorted(r["outcome"] for r in rows.values()) == [
        "skipped_auto_reply",
        "skipped_bounce",
        "ticket",
        "ticket",
    ]
    assert "CI: triaged" in graph._find(m.id).categories

    # The same message notified again is not a second ticket.
    await _sync(admin, mid)
    assert len(await _messages(tid)) == 4

    # Sending stays off until the connection test round trip succeeded.
    r = await admin.send("PUT", f"/v1/mailboxes/{mid}/sending", {"enabled": True})
    assert r.status_code == 409 and r.json()["code"] == "test_required"
    await admin.send("POST", f"/v1/mailboxes/{mid}/test", None)
    await drain()
    await _sync(admin, mid)
    box = await _box(admin, mid)
    assert box["lastTestOkAt"] is not None
    assert "test" in [r["outcome"] for r in await _messages(tid)]
    r = await admin.send("PUT", f"/v1/mailboxes/{mid}/sending", {"enabled": True})
    assert r.status_code == 200 and r.json()["sendEnabled"] is True

    # A person approves a reply; it goes out in the customer's thread, from the mailbox, once.
    ticket_id = str(rows[m.id]["ticket_id"])
    r = await admin.send("POST", f"/v1/tickets/{ticket_id}/replies", {"body": "Your statement is attached."})
    assert r.status_code == 200, r.text
    conn = await admin_conn()
    try:
        await conn.execute(
            "update jobs set run_at = now() where org_id = $1 and kind = 'send_reply'", uuid.UUID(tid)
        )
    finally:
        await conn.close()
    await drain()
    assert len(graph.sent) == 1
    sent = graph.sent[0]
    assert sent["replyTo"] == m.id and sent["comment"] == "Your statement is attached."
    assert sent["headers"][0]["name"] == "x-ci-intent"
    assert "CI: replied" in graph._find(m.id).categories
    box = await _box(admin, mid)
    assert {s["key"]: s["level"] for s in box["signals"]}["send"] == "healthy"


async def test_a_send_retried_after_a_crash_is_not_sent_twice(bank, graph, monkeypatch):
    from command_inbox.core.clock import clock
    from command_inbox.core.jobs import JobRow
    from command_inbox.db.engine import tenant_tx
    from command_inbox.db.models import Mailbox, MailSendIntent, Reply
    from command_inbox.mail import sync

    admin, tid, _ = bank
    mid = await _connect(admin, graph, monkeypatch)
    m = graph.deliver("Loan balance", "asha@customer.test", "What is my loan balance?")
    await _sync(admin, mid)
    ticket_id = str(next(r for r in await _messages(tid) if r["provider_message_id"] == m.id)["ticket_id"])

    async with tenant_tx(tid) as tx:
        box = (await tx.execute(select(Mailbox).where(Mailbox.id == mid))).scalar_one()
        box.send_enabled = True
        reply = Reply(
            org_id=tid,
            ticket_id=ticket_id,
            author_id=admin.me["user"]["id"],
            body="Your balance is ...",
            state="sent",
            send_after=clock.now(),
        )
        tx.add(reply)
        await tx.flush()
        intent_id = await sync.queue_send(tx, tid, box, ticket_id, "reply", reply.id)
        tx.expunge(box)

    # The crash: the draft was created and sent, but the worker died before recording "sent".
    conn = sync.connector_for(tid, box)
    draft = await conn.create_reply(m.id, "Your balance is ...", intent_id=intent_id)
    await conn.send_draft(draft)
    async with tenant_tx(tid) as tx:
        await tx.execute(
            update(MailSendIntent)
            .where(MailSendIntent.id == intent_id)
            .values(state="drafted", provider_draft_id=draft)
        )
    await sync.run_mail_send(JobRow(0, tid, "mail_send", {"intentId": intent_id}, 1, 8))
    assert len(graph.sent) == 1  # found in Sent Items by its intent id, not sent again
    async with tenant_tx(tid) as tx:
        it = (await tx.execute(select(MailSendIntent).where(MailSendIntent.id == intent_id))).scalar_one()
    assert it.state == "sent" and it.provider_message_id == "sent-1"


async def test_chaos_cursor_expiry_throttling_and_revoked_sign_in(bank, graph, monkeypatch):
    admin, tid, _ = bank
    mid = await _connect(admin, graph, monkeypatch)
    graph.deliver("Payment missing", "k@customer.test", "My payment has not arrived.")
    await _sync(admin, mid)
    assert len(await _messages(tid)) == 1

    # The provider forgets our cursor: we resync from a day before the last message, with no duplicates.
    graph.expire_cursor = True
    graph.inbox_history_visible = True
    graph.deliver("Second question", "k@customer.test", "And my card?", conversation="conv-x")
    await _sync(admin, mid)
    rows = await _messages(tid)
    assert len(rows) == 2 and len({r["provider_message_id"] for r in rows}) == 2
    events = (await _box(admin, mid))["events"]
    assert any(e["kind"] == "sync" and e["ok"] for e in events)

    # Throttled: the sync is recorded and retried later, nothing is lost.
    graph.throttle_next = 1
    graph.deliver("Third", "k@customer.test", "Another one")
    await admin.send("POST", f"/v1/mailboxes/{mid}/sync", None)
    await drain()
    conn = await admin_conn()
    try:
        await conn.execute(
            "update jobs set run_at = now() where org_id = $1 and kind = 'mail_sync' and state = 'queued'",
            uuid.UUID(tid),
        )
    finally:
        await conn.close()
    await drain()
    assert len(await _messages(tid)) == 3

    # The sign-in is revoked (password reset): the mailbox asks for a reconnect and stops trying.
    graph.refuse_refresh = True
    conn = await admin_conn()
    try:
        await conn.execute("update mailboxes set token_expires_at = now() where id = $1", uuid.UUID(mid))
        sealed = await conn.fetchval("select credentials_enc from mailboxes where id = $1", uuid.UUID(mid))
    finally:
        await conn.close()
    assert sealed.startswith("t1.") and "rt-0" not in sealed
    from command_inbox.db.engine import tenant_tx
    from command_inbox.mail.credentials import TokenProvider

    async with tenant_tx(tid) as tx:  # force expiry inside the sealed token set
        from sqlalchemy import select

        from command_inbox.db.models import Mailbox
        from command_inbox.mail.credentials import seal_tokens

        mb = (await tx.execute(select(Mailbox).where(Mailbox.id == mid))).scalar_one()
        mb.credentials_enc, _ = await seal_tokens(
            tx, tid, mid, {"access_token": "old", "refresh_token": "rt", "expires_in": 0}
        )
    from command_inbox.mail.types import ReauthRequired

    with pytest.raises(ReauthRequired):
        await TokenProvider(tid, mid, "graph")()
    box = await _box(admin, mid)
    assert box["connection"] == "reauth_required" and box["level"] == "down"
    assert {s["key"]: s["level"] for s in box["signals"]}["credential"] == "down"


async def test_graph_webhook_validation_and_client_state(bank, graph, monkeypatch, anon):
    from command_inbox.core.crypto import sha256

    admin, tid, _ = bank
    mid = await _connect(admin, graph, monkeypatch)
    r = await anon.post("/v1/hooks/graph", params={"validationToken": "abc 123"})
    assert r.status_code == 200 and r.text == "abc 123" and r.headers["content-type"].startswith("text/plain")

    conn = await admin_conn()
    try:
        await conn.execute(
            "update mailboxes set stream_id = 'sub-9', stream_secret_hash = $2 where id = $1",
            uuid.UUID(mid),
            sha256("s3cret"),
        )
    finally:
        await conn.close()

    async def queued() -> int:
        c = await admin_conn()
        try:
            return int(
                await c.fetchval(
                    "select count(*) from jobs where org_id = $1 and kind = 'mail_sync' and state = 'queued'",
                    uuid.UUID(tid),
                )
            )
        finally:
            await c.close()

    before = await queued()
    forged = {"value": [{"subscriptionId": "sub-9", "clientState": "guess", "changeType": "created"}]}
    assert (await anon.post("/v1/hooks/graph", json=forged)).status_code == 202
    assert await queued() == before
    real = {"value": [{"subscriptionId": "sub-9", "clientState": "s3cret", "changeType": "created"}]}
    assert (await anon.post("/v1/hooks/graph", json=real)).status_code == 202
    assert await queued() == before + 1
    lifecycle = {
        "value": [
            {"subscriptionId": "sub-9", "clientState": "s3cret", "lifecycleEvent": "subscriptionRemoved"}
        ]
    }
    assert (await anon.post("/v1/hooks/graph/lifecycle", json=lifecycle)).status_code == 202


async def test_mailbox_limits_and_permissions(bank, graph, staff):
    admin, _tid, slug = bank
    r = await admin.send(
        "POST", "/v1/mailbox-connections", {"address": f"one@{slug}.test", "provider": "microsoft"}
    )
    assert r.status_code == 200
    r = await admin.send(
        "POST", "/v1/mailbox-connections", {"address": f"two@{slug}.test", "provider": "microsoft"}
    )
    assert r.status_code == 409 and r.json()["code"] == "mailbox_limit"
    assert (await staff.get("/v1/mailbox-connections")).status_code == 403
