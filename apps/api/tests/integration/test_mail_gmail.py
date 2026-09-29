"""Google Workspace end to end against a stateful Gmail fake, and the signed Pub/Sub push."""

from __future__ import annotations

import base64
import json
import time
import uuid
from email import message_from_bytes, policy
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from tests.integration.admin_support import admin_conn, drain
from tests.integration.conftest import Client
from tests.integration.gmail_fake import FakeGmail
from tests.integration.test_mail_graph import _box, _messages, _sync, bank  # noqa: F401


@pytest.fixture
def gmail(monkeypatch: pytest.MonkeyPatch, bank: tuple[Client, str, str]):  # noqa: F811
    from command_inbox.config import settings

    monkeypatch.setattr(settings, "google_client_id", "bank-google-client")
    monkeypatch.setattr(settings, "google_client_secret", "bank-google-secret")
    monkeypatch.setattr(settings, "mail_webhook_base_url", None)
    fake = FakeGmail(account=f"care@{bank[2]}.test")
    with respx.mock(assert_all_called=False) as router:
        router.route(host="test").pass_through()
        fake.mount(router)
        yield fake


async def _connect(admin: Client, fake: FakeGmail, monkeypatch: pytest.MonkeyPatch) -> str:
    from command_inbox.modules.intake import oauth

    mid = (
        await admin.send("POST", "/v1/mailbox-connections", {"address": fake.account, "provider": "google"})
    ).json()["id"]
    url = (await admin.send("POST", f"/v1/mailboxes/{mid}/connect", None)).json()["authorizeUrl"]
    q = parse_qs(urlparse(url).query)
    assert (
        "gmail.modify" in q["scope"][0] and "gmail.send" in q["scope"][0] and q["access_type"] == ["offline"]
    )

    async def exchange(*_a: Any) -> dict[str, Any]:
        return {"access_token": "g-at", "refresh_token": "g-rt", "expires_in": 3600}

    async def who(*_a: Any) -> str:
        return fake.account

    monkeypatch.setattr(oauth, "exchange_code", exchange)
    monkeypatch.setattr(oauth, "account_email", who)
    r = await admin.get(
        "/v1/oauth/google/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False
    )
    assert r.status_code == 302
    await drain()
    return mid


async def test_gmail_ingest_resync_and_reply_in_thread(bank, gmail, monkeypatch):  # noqa: F811
    admin, tid, _ = bank
    mid = await _connect(admin, gmail, monkeypatch)
    assert (await _box(admin, mid))["connection"] == "live"

    auth = "mx.google.com; spf=pass smtp.mailfrom=customer.test; dkim=pass header.i=@customer.test; dmarc=pass header.from=customer.test"
    m = gmail.deliver(
        "Card replacement",
        "meera@customer.test",
        "My card is damaged.",
        headers={"Authentication-Results": auth},
    )
    gmail.deliver("Out of office", "ooo@customer.test", "Away", headers={"Auto-Submitted": "auto-replied"})
    await _sync(admin, mid)
    rows = {r["provider_message_id"]: r for r in await _messages(tid)}
    assert rows[m.id]["outcome"] == "ticket" and rows[m.id]["auth"]["verified"] is True
    assert sorted(r["outcome"] for r in rows.values()) == ["skipped_auto_reply", "ticket"]
    assert gmail.labels.get("CI/triaged") in gmail.find(m.id).labels

    # History gone (404): resync from the mailbox listing, no duplicates.
    gmail.expire_history = True
    gmail.deliver("Another", "meera@customer.test", "Also my PIN.", thread=m.thread)
    await _sync(admin, mid)
    rows = await _messages(tid)
    assert len(rows) == 3 and len({r["provider_message_id"] for r in rows}) == 3
    same_thread = [r for r in rows if r["outcome"] in ("ticket", "thread")]
    assert {str(r["ticket_id"]) for r in same_thread} == {
        str(rows[0]["ticket_id"])
    }  # threaded into one ticket

    # Connection test, sending on, an approved reply goes out in the thread with the right headers.
    await admin.send("POST", f"/v1/mailboxes/{mid}/test", None)
    await drain()
    await _sync(admin, mid)
    assert (await admin.send("PUT", f"/v1/mailboxes/{mid}/sending", {"enabled": True})).status_code == 200
    ticket_id = str(next(r for r in rows if r["provider_message_id"] == m.id)["ticket_id"])
    r = await admin.send("POST", f"/v1/tickets/{ticket_id}/replies", {"body": "We are sending a new card."})
    assert r.status_code == 200, r.text
    conn = await admin_conn()
    try:
        await conn.execute(
            "update jobs set run_at = now() where org_id = $1 and kind = 'send_reply'", uuid.UUID(tid)
        )
    finally:
        await conn.close()
    await drain()
    replies = [x for x in gmail.sent() if "INBOX" not in x.labels]
    assert len(replies) == 1
    sent = message_from_bytes(replies[0].raw, policy=policy.default)
    assert replies[0].thread == m.thread
    # It answers the customer's latest message in the thread (the follow-up), with the thread's headers.
    assert sent["In-Reply-To"] == "<g3@customer.test>" and "<g3@customer.test>" in sent["References"]
    assert sent["Subject"] == "Re: Another" and sent["To"] == "meera@customer.test"
    assert sent["Message-ID"].startswith("<ci-") and sent["x-ci-intent"]
    assert gmail.labels.get("CI/replied") in gmail.find("g3").labels


def _signed(key: RSAKey, **claims: Any) -> str:
    now = int(time.time())
    body = {
        "iss": "https://accounts.google.com",
        "aud": "https://api.bank.test/v1/hooks/gmail",
        "email": "push@bank-project.iam.gserviceaccount.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 300,
        **claims,
    }
    return jwt.encode({"alg": "RS256", "kid": "k1"}, body, key)


async def test_gmail_push_requires_a_google_signed_token(bank, gmail, monkeypatch, anon):  # noqa: F811
    from command_inbox.config import settings
    from command_inbox.mail import hooks

    admin, tid, _ = bank
    mid = await _connect(admin, gmail, monkeypatch)
    conn = await admin_conn()
    try:
        await conn.execute("update mailboxes set stream_id = $2 where id = $1", uuid.UUID(mid), gmail.account)
    finally:
        await conn.close()

    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    other = RSAKey.generate_key(2048, parameters={"kid": "k1"})

    async def keys() -> KeySet:
        return KeySet([key])

    monkeypatch.setattr(hooks, "google_keys", keys)
    monkeypatch.setattr(settings, "google_push_audience", "https://api.bank.test/v1/hooks/gmail")
    monkeypatch.setattr(settings, "google_push_service_account", "push@bank-project.iam.gserviceaccount.com")
    data = base64.b64encode(json.dumps({"emailAddress": gmail.account, "historyId": 5}).encode()).decode()
    body = {"message": {"data": data, "messageId": "1"}, "subscription": "projects/p/subscriptions/s"}

    async def push(token: str | None) -> int:
        headers = {"authorization": f"Bearer {token}"} if token else {}
        return (await anon.post("/v1/hooks/gmail", json=body, headers=headers)).status_code

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
    assert await push(None) == 401
    assert await push(_signed(other)) == 401  # not signed by Google's key
    assert await push(_signed(key, aud="https://evil.test")) == 401
    assert await push(_signed(key, email="someone@evil.iam.gserviceaccount.com")) == 401
    assert await push(_signed(key, email_verified=False)) == 401
    assert await queued() == before
    assert await push(_signed(key)) == 204
    assert await queued() == before + 1
