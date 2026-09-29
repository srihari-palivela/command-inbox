"""The studio: the test bench (nothing saved, draft and live side by side) and the labelling queue."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.admin_support import (
    add_member,
    admin_conn,
    audit_actions,
    desk_config,
    last_seq,
    make_dataset,
    make_deployment,
    org_id,
)

pytestmark = pytest.mark.integration

MAIL = {
    "subject": "Stop cheque",
    "body": "Please stop the cheque, cancel cheque number 004512 and do not honour the cheque. "
    "Call me on 9876543210.",
}


async def _counts(oid: str) -> tuple[int, int, int]:
    conn = await admin_conn()
    try:
        row = await conn.fetchrow(
            """select (select count(*) from tickets where org_id = $1) t,
                      (select count(*) from triage_runs where org_id = $1) r,
                      (select count(*) from drafts where org_id = $1) d""",
            uuid.UUID(oid),
        )
    finally:
        await conn.close()
    return row["t"], row["r"], row["d"]


async def test_the_bench_runs_draft_and_live_side_by_side_and_saves_nothing(app):
    a = await add_member(app, "meridian", "admin")
    oid = await org_id("meridian")
    dep, vid = await make_deployment(a, "bench_desk")
    before = await _counts(oid)
    seq = await last_seq(oid)

    r = await a.send("POST", f"/v1/deployments/{dep}/versions/{vid}/bench", {**MAIL, "compareWith": "active"})
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    assert len(runs) == 1  # a new deployment has nothing live yet
    run = runs[0]
    assert run["version"] == 1 and run["state"] == "draft"
    assert run["category"] == "Cheque stop request" and run["lane"] in ("draft", "manual", "auto")
    assert run["spans"] and run["spans"][0]["agent"]
    serialised = str(run)
    assert "9876543210" not in serialised  # personal data stays masked
    assert await _counts(oid) == before
    assert "deployment.bench_run" in [x["action"] for x in await audit_actions(oid, seq)]

    staff = await add_member(app, "meridian", "staff")
    r = await staff.send("POST", f"/v1/deployments/{dep}/versions/{vid}/bench", MAIL)
    assert r.status_code == 403


async def test_the_bench_compares_with_the_live_version(app, admin):
    r = await admin.get("/v1/deployments")
    dep = next(d for d in r.json() if d["key"] == "default")
    r = await admin.send("POST", f"/v1/deployments/{dep['id']}/versions", {})
    draft_id = r.json()["id"] if r.status_code == 200 else None
    if draft_id is None:  # a draft already exists
        detail = (await admin.get(f"/v1/deployments/{dep['id']}")).json()
        draft_id = next(v["id"] for v in detail["versions"] if v["state"] == "draft")
    r = await admin.send(
        "POST", f"/v1/deployments/{dep['id']}/versions/{draft_id}/bench", {**MAIL, "compareWith": "active"}
    )
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    assert [x["state"] for x in runs] == ["draft", "published"]


async def test_labelling_turns_real_mail_into_masked_cases_once(app, admin):
    oid = admin.me["org"]["id"]
    dep = next(d for d in (await admin.get("/v1/deployments")).json() if d["key"] == "default")
    r = await admin.send(
        "POST", "/v1/evals/datasets", {"deploymentId": dep["id"], "name": f"real mail {uuid.uuid4().hex[:6]}"}
    )
    assert r.status_code == 200, r.text
    ds = r.json()["id"]

    r = await admin.get(f"/v1/evals/datasets/{ds}/labelling?limit=5")
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["candidates"] and q["categories"] and q["labelled"] == 0
    first = q["candidates"][0]
    category = first["suggested"]["category"] or q["categories"][0]["key"]
    seq = await last_seq(oid)
    r = await admin.send(
        "POST",
        f"/v1/evals/datasets/{ds}/labels",
        {
            "ticketId": first["ticketId"],
            "category": category,
            "hardStop": first["suggested"]["hardStop"],
            "lane": "draft",
            "draftAcceptable": True,
            "split": "calibration",
        },
    )
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["labelled"] == 1 and after["calibration"] == 1
    assert first["ticketId"] not in [c["ticketId"] for c in after["candidates"]]
    assert "eval.case_labelled" in [x["action"] for x in await audit_actions(oid, seq)]

    cases = (await admin.get(f"/v1/evals/datasets/{ds}/cases")).json()
    assert len(cases) == 1 and cases[0]["ticketId"] == first["ticketId"] and cases[0]["source"] == "labelled"
    assert cases[0]["expected"] == {
        "category": category,
        "hardStop": first["suggested"]["hardStop"],
        "lane": "draft",
        "draftAcceptable": True,
    }
    assert cases[0]["input"]["fromEmail"].startswith("customer@")

    r = await admin.send(
        "POST",
        f"/v1/evals/datasets/{ds}/labels",
        {"ticketId": first["ticketId"], "category": category, "hardStop": False},
    )
    assert r.status_code == 409 and r.json()["code"] == "already_labelled"
    r = await admin.send(
        "POST",
        f"/v1/evals/datasets/{ds}/labels",
        {"ticketId": q["candidates"][1]["ticketId"], "category": "not_a_category", "hardStop": False},
    )
    assert r.status_code == 400 and r.json()["code"] == "unknown_category"


async def test_new_eval_labels_keep_old_dataset_snapshots(app):
    """Cases saved without the new optional labels hash exactly as before."""
    a = await add_member(app, "meridian", "admin")
    dep, _ = await make_deployment(a, "snapshot_desk", desk_config())
    ds = await make_dataset(a, dep)
    cases = (await a.get(f"/v1/evals/datasets/{ds}/cases")).json()
    assert all(set(c["expected"]) == {"category", "hardStop", "lane", "draftAcceptable"} for c in cases)
    conn = await admin_conn()
    try:
        stored = await conn.fetch("select expected from eval_cases where dataset_id = $1", uuid.UUID(ds))
    finally:
        await conn.close()
    import json

    assert all(set(json.loads(r["expected"])) == {"category", "hardStop"} for r in stored)
