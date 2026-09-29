"""Knowledge end to end: upload → scan → parse → approve → retrieved → cited by a draft."""

from __future__ import annotations

import io
import uuid
from typing import Any

from tests.integration.admin_support import admin_conn, drain
from tests.integration.conftest import Client
from tests.integration.test_mail_graph import bank  # noqa: F401

STATEMENTS = b"""# Statement services

## Copies of statements
A copy of an account statement is emailed to the registered email address within one working day.
Statements for the last seven years can be requested. There is no fee for an emailed statement copy.

## Paper statements
Paper statements are posted to the registered address and cost 50 per request.
"""

CARDS = b"""# Card services

A lost or stolen card is blocked immediately when reported. A replacement card arrives within five
working days at the registered address. Emergency cash can be arranged at any branch.
"""


async def _upload(c: Client, name: str, data: bytes, **form: str) -> dict[str, Any]:
    r = await c.http.post(
        "/v1/knowledge/documents",
        files={"file": (name, io.BytesIO(data), "text/markdown")},
        data=form,
        headers={"x-csrf-token": c.me["csrfToken"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _search(c: Client, q: str) -> list[dict[str, Any]]:
    r = await c.send("POST", "/v1/knowledge/search", {"query": q})
    assert r.status_code == 200, r.text
    return r.json()["hits"]


async def test_upload_scan_parse_approve_and_retrieve(bank, staff, admin):  # noqa: F811
    other_bank_admin = admin
    admin, _tid, _ = bank
    doc = await _upload(admin, "statements.md", STATEMENTS, title="Statement services")
    assert doc["status"] == "pending" and doc["parseStatus"] == "queued" and doc["canApprove"] is False
    dup = await admin.http.post(
        "/v1/knowledge/documents",
        files={"file": ("again.md", io.BytesIO(STATEMENTS), "text/markdown")},
        headers={"x-csrf-token": admin.me["csrfToken"]},
    )
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate"
    bad = await admin.http.post(
        "/v1/knowledge/documents",
        files={"file": ("tool.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
        headers={"x-csrf-token": admin.me["csrfToken"]},
    )
    assert bad.status_code == 422 and bad.json()["code"] == "unsupported_type"

    await drain()
    detail = (await admin.get(f"/v1/knowledge/documents/{doc['id']}")).json()
    assert (
        detail["parseStatus"] == "ready" and detail["avStatus"] == "not_scanned" and detail["chunkCount"] == 2
    )
    assert [c["section"] for c in detail["chunks"]] == [
        "Statement services › Copies of statements",
        "Statement services › Paper statements",
    ]
    # Pending knowledge is never retrievable.
    assert await _search(admin, "copy of my statement by email") == []

    # Only someone with approve clearance may approve (staff get "resolve" by default).
    assert (await staff.send("POST", f"/v1/knowledge/documents/{doc['id']}/approve", None)).status_code in (
        403,
        404,
    )
    r = await admin.send("POST", f"/v1/knowledge/documents/{doc['id']}/approve", None)
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    hits = await _search(admin, "Can you email me a copy of my statement?")
    assert hits and hits[0]["title"] == "Statement services" and "Copies of statements" in hits[0]["section"]
    assert hits[0]["textMatch"] is True
    assert await _search(admin, "mortgage prepayment penalty") == []  # no source: say so, don't guess

    # Another tenant never sees it (row-level security under the query).
    other = await _search(other_bank_admin, "copy of my statement by email")
    assert all(h["docId"] != doc["id"] for h in other)


async def test_versions_replace_and_expiry_stops_citation(bank):  # noqa: F811
    admin, _tid, _ = bank
    v1 = await _upload(admin, "cards.md", CARDS, title="Card services")
    await drain()
    await admin.send("POST", f"/v1/knowledge/documents/{v1['id']}/approve", None)
    v2 = await _upload(admin, "cards-v2.md", CARDS.replace(b"five", b"three"), replacesId=v1["id"])
    assert v2["version"] == 2 and v2["title"] == "Card services"
    await drain()
    await admin.send("POST", f"/v1/knowledge/documents/{v2['id']}/approve", None)
    docs = {d["id"]: d for d in (await admin.get("/v1/knowledge/documents")).json()}
    assert docs[v1["id"]]["status"] == "retired" and docs[v2["id"]]["status"] == "approved"
    hits = await _search(admin, "when does my replacement card arrive")
    assert {h["docId"] for h in hits} == {v2["id"]} and "three" in hits[0]["text"]

    conn = await admin_conn()
    try:
        await conn.execute(
            "update knowledge_docs set expires_at = now() - interval '1 minute' where id = $1",
            uuid.UUID(v2["id"]),
        )
    finally:
        await conn.close()
    assert await _search(admin, "when does my replacement card arrive") == []  # expired: never cited
    from command_inbox.knowledge.service import expire_sweep

    assert await expire_sweep() >= 1
    assert {d["id"]: d for d in (await admin.get("/v1/knowledge/documents")).json()}[v2["id"]][
        "status"
    ] == "stale"


async def test_an_infected_upload_is_discarded_and_an_unreachable_scanner_blocks(bank, monkeypatch):  # noqa: F811
    from command_inbox.knowledge import av

    admin, _tid, _ = bank

    async def infected(_data: bytes) -> tuple[str, str]:
        return "infected", "Eicar-Test-Signature"

    monkeypatch.setattr(av, "scan", infected)
    doc = await _upload(admin, "eicar.txt", b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR", title="Bad")
    await drain()
    d = (await admin.get(f"/v1/knowledge/documents/{doc['id']}")).json()
    assert d["parseStatus"] == "infected" and d["status"] == "rejected" and d["chunks"] == []
    conn = await admin_conn()
    try:
        assert (
            await conn.fetchval("select blob_sealed from knowledge_docs where id = $1", uuid.UUID(doc["id"]))
            is None
        )
    finally:
        await conn.close()

    async def down(_data: bytes) -> tuple[str, str]:
        return "error", "scanner unreachable"

    monkeypatch.setattr(av, "scan", down)
    doc = await _upload(admin, "later.txt", b"Branch hours are nine to five on weekdays.", title="Hours")
    await drain()
    d = (await admin.get(f"/v1/knowledge/documents/{doc['id']}")).json()
    assert (
        d["parseStatus"] in ("queued", "scanning") and d["chunks"] == []
    )  # retried later, never parsed unscanned


async def test_a_triaged_mail_cites_the_uploaded_passage(bank):  # noqa: F811
    from command_inbox.modules.intake import service as intake
    from command_inbox.modules.intake.security import SIMULATED
    from command_inbox.schemas.requests import IntakeMessageBody

    admin, tid, slug = bank
    doc = await _upload(admin, "statements.md", STATEMENTS, title="Statement services")
    await drain()
    await admin.send("POST", f"/v1/knowledge/documents/{doc['id']}/approve", None)
    conn = await admin_conn()
    try:
        await conn.execute(
            "insert into mailboxes (org_id, address, provider, state, deployment_id) "
            "select $1, $2, 'dev', 'observe', id from deployments where org_id = $1 and key = 'default'",
            uuid.UUID(tid),
            f"care@{slug}.test",
        )
    finally:
        await conn.close()
    result = await intake.ingest(
        IntakeMessageBody.model_validate(
            {
                "mailbox": f"care@{slug}.test",
                "fromName": "Ravi",
                "fromEmail": "ravi@customer.test",
                "subject": "Copy of my statement",
                "body": "Please email me a copy of my account statement for the last quarter. Is there a fee for an emailed statement copy?",
            }
        ),
        SIMULATED,
        org_id=tid,
    )
    await drain()
    conn = await admin_conn()
    try:
        draft = await conn.fetchrow(
            "select current_body, citations::text as c from drafts where ticket_id = $1",
            uuid.UUID(result.ticket_id),
        )
    finally:
        await conn.close()
    assert draft is not None, "a statement request with an approved source is drafted"
    import json

    cites = json.loads(draft["c"])
    assert cites and cites[0]["docId"] == doc["id"] and cites[0]["chunkId"]
    assert "Copies of statements" in cites[0]["section"]
