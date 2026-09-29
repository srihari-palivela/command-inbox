"""Operations: retention, the signed audit export, SIEM streaming and the per-workspace rate limit."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from command_inbox.core.clock import clock
from command_inbox.core.crypto import hmac_hex
from command_inbox.core.ratelimit import RateLimiter, limiter
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import Org
from command_inbox.modules.workspace.operations import REDACTED, apply_retention, push_siem
from tests.integration.admin_support import add_member, admin_conn, audit_actions, last_seq, org_id

pytestmark = pytest.mark.integration


async def test_retention_removes_old_mail_text_but_keeps_the_record(app):
    oid = await org_id("meridian")
    conn = await admin_conn()
    try:
        tid = await conn.fetchval(
            "select id from tickets where org_id = $1 and status in ('resolved','closed') limit 1",
            uuid.UUID(oid),
        )
        if tid is None:
            tid = await conn.fetchval("select id from tickets where org_id = $1 limit 1", uuid.UUID(oid))
            await conn.execute("update tickets set status = 'resolved' where id = $1", tid)
        await conn.execute(
            "update tickets set resolved_at = $2, closed_at = null where id = $1",
            tid,
            clock.now() - timedelta(days=800),
        )
        n_before = await conn.fetchval("select count(*) from messages where ticket_id = $1", tid)
    finally:
        await conn.close()
    seq = await last_seq(oid)
    async with tenant_tx(oid) as tx:
        out = await apply_retention(tx, oid)
    assert out["messages"] >= n_before > 0
    conn = await admin_conn()
    try:
        bodies = await conn.fetch("select body, redacted_at from messages where ticket_id = $1", tid)
        still = await conn.fetchval("select count(*) from tickets where id = $1", tid)
    finally:
        await conn.close()
    assert all(r["body"] == REDACTED and r["redacted_at"] for r in bodies) and still == 1
    assert "retention.applied" in [x["action"] for x in await audit_actions(oid, seq)]
    async with tenant_tx(oid) as tx:
        again = await apply_retention(tx, oid)
    assert again["messages"] == 0  # idempotent


async def test_audit_export_is_a_signed_zip_whose_manifest_verifies(admin):
    r = await admin.get("/v1/audit/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"events.csv", "events.jsonl", "manifest.json"}
    manifest = json.loads(z.read("manifest.json"))
    lines = z.read("events.jsonl").decode().splitlines()
    assert manifest["events"] == len(lines) > 0
    import hashlib

    for f in manifest["files"]:
        assert hashlib.sha256(z.read(f["name"])).hexdigest() == f["sha256"]
    assert json.loads(lines[-1])["hash"] == manifest["lastHash"]
    ok = await admin.send("POST", "/v1/audit/export/verify", manifest)
    assert ok.status_code == 200 and ok.json()["ok"] is True
    tampered = {**manifest, "events": manifest["events"] - 1}
    assert (await admin.send("POST", "/v1/audit/export/verify", tampered)).json()["ok"] is False


@pytest.fixture
def any_host(monkeypatch):
    """Test hosts do not resolve: let the outbound guard through (it has its own tests)."""
    from command_inbox.modules.workspace import operations

    async def ok(url: str) -> None:
        return None

    monkeypatch.setattr(operations, "check_outbound", ok)


async def test_siem_streams_signed_batches_and_retries_from_where_it_stopped(app, any_host):
    a = await add_member(app, "meridian", "admin")
    oid = await org_id("meridian")
    secret = "a-shared-secret-of-enough-length"
    r = await a.send(
        "PUT",
        "/v1/workspace/operations",
        {
            "retentionMailDays": 730,
            "retentionTraceDays": 180,
            "siemUrl": "https://siem.bank.example/in",
            "siemSecret": secret,
        },
    )
    assert r.status_code == 200, r.text
    start = r.json()["siem"]["deliveredSeq"]
    # New events after the stream starts.
    await a.send(
        "PUT",
        "/v1/workspace/operations",
        {"retentionMailDays": 365, "retentionTraceDays": 90, "siemUrl": "https://siem.bank.example/in"},
    )

    received: list[dict] = []
    fail = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        assert request.headers["x-ci-signature"] == f"sha256={hmac_hex(secret, body)}"
        if fail["on"]:
            return httpx.Response(503)
        received.append(json.loads(body))
        return httpx.Response(202)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await push_siem(oid, client) == 0
    ops = (await a.get("/v1/workspace/operations")).json()
    assert ops["siem"]["lastError"] == "HTTP 503" and ops["siem"]["deliveredSeq"] == start
    fail["on"] = False
    delivered = await push_siem(oid, client)
    assert delivered >= 1
    seqs = [e["seq"] for e in received[0]["events"]]
    assert seqs == sorted(seqs) and seqs[0] > start
    ops = (await a.get("/v1/workspace/operations")).json()
    assert ops["siem"]["lastError"] is None and ops["siem"]["deliveredSeq"] == seqs[-1]
    # Turn it off again so other tests' workers do not try to deliver.
    r = await a.send(
        "PUT",
        "/v1/workspace/operations",
        {"retentionMailDays": 730, "retentionTraceDays": 180, "siemUrl": None},
    )
    assert r.status_code == 200


async def test_siem_needs_https_and_a_secret(app, any_host):
    a = await add_member(app, "apex", "admin")
    body = {"retentionMailDays": 730, "retentionTraceDays": 180, "siemUrl": "http://siem.example"}
    assert (await a.send("PUT", "/v1/workspace/operations", body)).status_code == 400
    async with tenant_tx(await org_id("apex")) as tx:
        org = (await tx.execute(select(Org).where(Org.slug == "apex"))).scalar_one()
        had_secret = bool(org.siem_secret_sealed)
    if not had_secret:
        r = await a.send("PUT", "/v1/workspace/operations", {**body, "siemUrl": "https://siem.example"})
        assert r.status_code == 422 and r.json()["code"] == "siem_secret_required"


def test_the_sliding_window_limits_and_recovers():
    rl = RateLimiter()
    t0 = 1_000_020.0  # 20 s into a minute
    assert all(rl.hit("o", 5, t0)[0] for _ in range(5))
    allowed, wait = rl.hit("o", 5, t0)
    assert not allowed and 1 <= wait <= 60
    assert rl.hit("o", 5, t0 + 120)[0]  # two minutes later the window is empty


async def test_a_workspace_over_its_limit_gets_429(app, monkeypatch):
    staff = await add_member(app, "meridian", "staff")
    oid = await org_id("meridian")
    from command_inbox.config import settings

    monkeypatch.setattr(settings, "api_rate_per_minute", 3000)
    limiter.reset()
    limiter.limits[oid] = (1e18, 3)  # cached limit of 3 a minute, never expires in this test
    try:
        codes = [(await staff.get("/v1/me")).status_code for _ in range(5)]
    finally:
        limiter.reset()
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


async def test_the_outbound_guard_refuses_internal_addresses(monkeypatch):
    from command_inbox.config import settings
    from command_inbox.core.errors import AppError
    from command_inbox.core.netguard import check_outbound

    for url in (
        "http://example.com/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest",
        "https://10.1.2.3/in",
    ):
        with pytest.raises(AppError):
            await check_outbound(url)
    monkeypatch.setattr(settings, "outbound_allow_cidrs", ["10.1.0.0/16"])
    await check_outbound("https://10.1.2.3/in")  # an allowlisted internal SIEM
    await check_outbound("https://93.184.215.14/in")  # a public address
