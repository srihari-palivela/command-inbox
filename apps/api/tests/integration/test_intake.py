"""Intake: the signed webhook (raw-body HMAC, timestamp, replay window, tamper), sender authentication and
the lane, the demo simulator, and session-bound mailbox OAuth with PKCE and the account check."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from command_inbox.core.clock import clock
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import AuditEvent, Customer, Job, Mailbox, Ticket
from command_inbox.domain.lane import LaneInputs, decide_lane
from command_inbox.modules.intake.security import parse_authentication_results, sign
from tests.integration.conftest import sign_in

pytestmark = pytest.mark.integration

AR_PASS = (
    "mx.bank.example; dkim=pass header.d=iyerexports.in; spf=pass smtp.mailfrom=iyerexports.in; dmarc=pass"
)


def _mail(**over: Any) -> dict[str, Any]:
    body = {
        "mailbox": "TradeOps@bank.example",
        "fromName": "Ramesh Iyer",
        "fromEmail": "ramesh@iyerexports.in",
        "subject": f"Stop payment on cheque {time.time_ns()}",
        "body": "Please stop cheque 552310 for ₹4,75,000.",
    }
    body.update(over)
    return body


async def _post(anon: Any, raw: bytes, ts: int | None = None, sig: str | None = None, **headers: str) -> Any:
    ts = int(clock.now().timestamp()) if ts is None else ts
    h = {
        "content-type": "application/json",
        "x-ci-timestamp": str(ts),
        "x-ci-signature": sig or sign(raw, ts),
    }
    h.update(headers)
    return await anon.post("/v1/intake/messages", content=raw, headers=h)


async def _job_payload(org: str, ticket_id: str) -> dict[str, Any]:
    async with tenant_tx(org) as tx:
        return (
            await tx.execute(
                select(Job.payload).where(Job.kind == "triage", Job.dedupe_key == f"triage:{ticket_id}")
            )
        ).scalar_one()


# ── Webhook signature ─────────────────────────────────────────────────────────────


async def test_signed_webhook_opens_a_ticket_and_queues_triage(anon, staff):
    org = staff.me["org"]["id"]
    raw = json.dumps(_mail(authenticationResults=AR_PASS), ensure_ascii=False, indent=1).encode()
    r = await _post(anon, raw)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] is True and out["duplicate"] is False and out["number"] > 0
    payload = await _job_payload(org, out["ticketId"])
    assert payload["ticketId"] == out["ticketId"] and payload["senderVerified"] is True
    assert payload["senderAuth"]["dmarc"] == "pass"
    async with tenant_tx(org) as tx:
        t = (await tx.execute(select(Ticket).where(Ticket.id == out["ticketId"]))).scalar_one()
        ev = (
            await tx.execute(
                select(AuditEvent).where(AuditEvent.ticket_id == t.id, AuditEvent.action == "mail.received")
            )
        ).scalar_one()
        sender = (await tx.execute(select(Customer).where(Customer.id == t.customer_id))).scalar_one()
    assert t.status == "triaging" and t.lane == "manual" and t.mailbox_id
    # An unknown sender is unmatched: no invented customer number or tenure.
    assert sender.cif is None and sender.since_year is None and sender.email == "ramesh@iyerexports.in"
    assert ev.summary == f"Mail from Ramesh Iyer to tradeops@bank.example opened QRY-{out['number']}"
    assert ev.data["senderAuth"]["verified"] is True

    # An exact replay inside the window is accepted but changes nothing.
    again = await _post(anon, raw, ts=int(r.request.headers["x-ci-timestamp"]))
    assert again.status_code == 200 and again.json() == {**out, "created": False, "duplicate": True}


async def test_bad_tampered_stale_and_legacy_signatures_are_refused(anon):
    raw = json.dumps(_mail()).encode()
    now = int(clock.now().timestamp())
    assert (await _post(anon, raw, sig="v1=" + "0" * 64)).status_code == 401
    # Tamper: signed body, one byte changed in transit.
    tampered = raw.replace(b"552310", b"552311")
    r = await _post(anon, tampered, ts=now, sig=sign(raw, now))
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"
    # Signed over a re-serialisation instead of the raw bytes.
    pretty = json.dumps(json.loads(raw), indent=2).encode()
    assert (await _post(anon, pretty, ts=now, sig=sign(raw, now))).status_code == 401
    # Replay window: 5 minutes either side.
    assert (await _post(anon, raw, ts=now - 301)).status_code == 401
    assert (await _post(anon, raw, ts=now + 301)).status_code == 401
    # The TS format (HMAC over JSON.stringify(body), no timestamp) has no replay protection and is refused.
    from command_inbox.config import settings
    from command_inbox.core.crypto import hmac_hex

    legacy = hmac_hex(settings.intake_webhook_secret, raw)
    r = await anon.post(
        "/v1/intake/messages",
        content=raw,
        headers={"content-type": "application/json", "x-ci-signature": legacy},
    )
    assert r.status_code == 401
    assert (await anon.post("/v1/intake/messages", content=raw)).status_code == 401


async def test_valid_signature_but_invalid_body_is_a_validation_problem(anon):
    r = await _post(anon, json.dumps(_mail(fromEmail="not-an-email")).encode())
    assert r.status_code == 400 and r.json()["code"] == "validation"


async def test_unknown_mailbox_is_not_found(anon):
    r = await _post(anon, json.dumps(_mail(mailbox="nobody@nowhere.example")).encode())
    assert r.status_code == 404


async def test_threading_by_subject_adds_to_the_open_ticket(anon, staff):
    first = (await _post(anon, json.dumps(_mail(subject="Cheque book request 77")).encode())).json()
    second = await _post(
        anon, json.dumps(_mail(subject="RE: Fwd: Cheque book request 77", body="Any update?")).encode()
    )
    assert second.json() == {
        "ticketId": first["ticketId"],
        "number": first["number"],
        "created": False,
        "duplicate": False,
    }


# ── Sender authentication and the lane ───────────────────────────────────────────


async def test_unauthenticated_sender_is_recorded_and_never_auto(anon, staff):
    org = staff.me["org"]["id"]
    fail = (
        "mx.bank.example; dkim=pass header.d=iyerexports.in; spf=pass; dmarc=fail header.from=iyerexports.in"
    )
    for extra, headers in (({}, {}), ({"authenticationResults": fail}, {})):
        out = (await _post(anon, json.dumps(_mail(**extra)).encode(), **headers)).json()
        payload = await _job_payload(org, out["ticketId"])
        assert payload["senderVerified"] is False
    # The HTTP header is used when the payload has none.
    out = (await _post(anon, json.dumps(_mail()).encode(), **{"authentication-results": AR_PASS})).json()
    assert (await _job_payload(org, out["ticketId"]))["senderVerified"] is True

    auto = {
        "hard_stop": None,
        "confidence": 0.99,
        "bar": 0.78,
        "query_type_owned": True,
        "multi_intent": False,
        "has_template": True,
        "fields_complete": True,
        "coverage": "full",
        "informational": False,
    }
    assert decide_lane(LaneInputs(**auto, sender_verified=True)).lane == "auto"
    assert decide_lane(LaneInputs(**auto, sender_verified=False)).lane == "manual"


def test_authentication_results_parsing():
    frm = "ramesh@iyerexports.in"
    assert parse_authentication_results(None, frm).verified is False
    assert parse_authentication_results("mx; dmarc=pass header.from=iyerexports.in", frm).verified is True
    assert parse_authentication_results("mx; dkim=pass header.d=iyerexports.in", frm).verified is True
    assert parse_authentication_results("mx; dkim=pass header.d=mail.evil.example", frm).verified is False
    assert (
        parse_authentication_results("mx; spf=pass smtp.mailfrom=bounce@iyerexports.in", frm).verified is True
    )
    assert (
        parse_authentication_results("mx; dkim=pass header.d=iyerexports.in; dmarc=fail", frm).verified
        is False
    )
    v = parse_authentication_results("mx; spf=softfail; dkim=none", frm)
    assert (v.spf, v.dkim, v.dmarc, v.verified) == ("softfail", "none", "none", False)


# ── Demo simulator ─────────────────────────────────────────────────────────────────


async def test_simulated_mail_lands_in_this_workspace(staff):
    r = await staff.send("POST", "/v1/dev/simulate-mail", {"index": 1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] is True
    payload = await _job_payload(staff.me["org"]["id"], out["ticketId"])
    assert payload["senderAuth"]["source"] == "simulator"


# ── Mailbox OAuth ─────────────────────────────────────────────────────────────────


@pytest.fixture
def oauth_env(monkeypatch):
    from command_inbox.modules.intake import oauth

    monkeypatch.setattr(oauth.oauth_settings, "ms_client_id", "ms-client")
    monkeypatch.setattr(oauth.oauth_settings, "ms_client_secret", "ms-secret")
    calls: dict[str, Any] = {"account": "grievance@bank.example"}

    async def exchange(p: str, code: str, verifier: str) -> dict[str, Any]:
        calls["verifier"] = verifier
        return {"access_token": "at-" + code, "refresh_token": "rt"}

    async def account(p: str, tokens: dict[str, Any]) -> str:
        return calls["account"]

    monkeypatch.setattr(oauth, "exchange_code", exchange)
    monkeypatch.setattr(oauth, "account_email", account)
    return calls


async def _mailbox_id(org: str, address: str) -> str:
    async with tenant_tx(org) as tx:
        return (await tx.execute(select(Mailbox.id).where(Mailbox.address == address))).scalar_one()


async def _start(client: Any, mailbox_id: str) -> dict[str, str]:
    r = await client.send("POST", f"/v1/mailboxes/{mailbox_id}/connect")
    assert r.status_code == 200, r.text
    q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["authorizeUrl"]).query).items()}
    assert q["code_challenge_method"] == "S256" and q["client_id"] == "ms-client"
    return q


async def test_oauth_connects_the_mailbox_for_the_same_session(app, admin, oauth_env):
    import base64
    import hashlib

    org = admin.me["org"]["id"]
    mb = await _mailbox_id(org, "grievance@bank.example")
    q = await _start(admin, mb)
    r = await admin.get("/v1/oauth/microsoft/callback", params={"code": "c1", "state": q["state"]})
    assert r.status_code == 302 and r.headers["location"].endswith("/boards?connected=1")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(oauth_env["verifier"].encode()).digest()).rstrip(b"=")
    assert challenge.decode() == q["code_challenge"]  # PKCE: the verifier sent matches the challenge
    async with tenant_tx(org) as tx:
        row = (await tx.execute(select(Mailbox).where(Mailbox.id == mb))).scalar_one()
    assert row.state == "streaming" and row.credentials_enc and "at-c1" not in row.credentials_enc


async def test_oauth_state_from_another_session_or_provider_is_rejected(app, admin, lead, anon, oauth_env):
    org = admin.me["org"]["id"]
    mb = await _mailbox_id(org, "grievance@bank.example")
    q = await _start(admin, mb)
    other_admin = await sign_in(app, "a.kapoor@bank.example")  # same person, different browser session
    for client in (other_admin, lead):
        r = await client.get("/v1/oauth/microsoft/callback", params={"code": "c", "state": q["state"]})
        assert r.status_code == 400 and r.json()["code"] == "bad_state"
    r = await admin.get("/v1/oauth/google/callback", params={"code": "c", "state": q["state"]})
    assert r.json()["code"] == "bad_state"
    assert (
        await anon.get("/v1/oauth/microsoft/callback", params={"code": "c", "state": q["state"]})
    ).status_code == 401
    clock.advance(11 * 60)
    try:
        r = await admin.get("/v1/oauth/microsoft/callback", params={"code": "c", "state": q["state"]})
        assert r.json()["code"] == "expired_state"
    finally:
        clock.reset()


async def test_oauth_account_must_match_the_mailbox(admin, staff, oauth_env):
    org = admin.me["org"]["id"]
    mb = await _mailbox_id(org, "grievance@bank.example")
    oauth_env["account"] = "someone.else@bank.example"
    q = await _start(admin, mb)
    r = await admin.get("/v1/oauth/microsoft/callback", params={"code": "c2", "state": q["state"]})
    assert r.status_code == 422 and r.json()["code"] == "mailbox_mismatch"
    # Staff cannot start a connection at all.
    r = await staff.send("POST", f"/v1/mailboxes/{mb}/connect")
    assert r.status_code == 403
