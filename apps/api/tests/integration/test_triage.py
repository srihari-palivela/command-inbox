"""Triage runner: parity with the previous service on the seeded mail, idempotency, safety invariants.

Runs against its own clone of the seeded template (the whole seeded corpus is re-triaged, which would
disturb other tests' data), with the deterministic decision engine and heuristic System 2 provider.

The expectations below were produced by running the TypeScript pipeline (`apps/server`, heuristic
provider) over the same template: every ticket set to `triaging`, triaged in (tenant slug, number) order.
Priority is not compared (it depends on the wall clock through the SLA deadline).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy import select, text

from tests.conftest import ADMIN_BASE, TEMPLATE_DB, TEST_DB

pytestmark = pytest.mark.integration

TS_REFERENCE: list[tuple[str, int, str, str, str, float, str]] = [
    ("apex", 41022, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
    ("apex", 43990, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
    ("apex", 44201, "manual", "with_human", "Stop payment instruction", 0.95, "Held back — third contact"),
    ("apex", 45877, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
    ("apex", 46003, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
    (
        "apex",
        46620,
        "manual",
        "with_human",
        "Certificate requests",
        0.88,
        "Action matched but fields or confidence fall short",
    ),
    ("apex", 47120, "manual", "with_human", "Stop payment instruction", 0.95, "Held back — third contact"),
    ("apex", 47755, "draft", "awaiting_approval", "Trade finance advisory", 0.8, "Cited draft ready to send"),
    ("apex", 48012, "manual", "with_human", "Disputed transactions", 0.8, "Held back — third contact"),
    ("apex", 48088, "manual", "with_human", "Disputed transactions", 0.8, "Held back — third contact"),
    (
        "apex",
        48174,
        "manual",
        "with_human",
        "Credit limit explanations",
        0.55,
        "Two separate questions in one email",
    ),
    (
        "apex",
        48178,
        "manual",
        "with_human",
        "Account maintenance",
        0.8,
        "No approved content covers this — handed to a person",
    ),
    (
        "apex",
        48181,
        "auto",
        "awaiting_approval",
        "Foreclosure quotes",
        0.8,
        "Filled in, waiting on one approver",
    ),
    ("apex", 48186, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
    (
        "apex",
        48188,
        "draft",
        "awaiting_approval",
        "Trade finance advisory",
        0.88,
        "Not confident enough — read it before sending",
    ),
    (
        "apex",
        48190,
        "manual",
        "with_human",
        "Certificate requests",
        0.8,
        "Action matched but fields or confidence fall short",
    ),
    ("apex", 48192, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
    (
        "apex",
        48195,
        "manual",
        "with_human",
        "Statement re-issue",
        0.95,
        "Action matched but fields or confidence fall short",
    ),
    (
        "apex",
        48196,
        "draft",
        "awaiting_approval",
        "Balance & charge queries",
        0.95,
        "Cited draft ready to send",
    ),
    (
        "apex",
        48198,
        "manual",
        "with_human",
        "Account maintenance",
        0.88,
        "Held back — vulnerable-customer signal",
    ),
    (
        "apex",
        48199,
        "manual",
        "with_human",
        "Disputed transactions",
        0.95,
        "Held back — regulator named + third contact",
    ),
    ("apex", 48201, "manual", "with_human", "Locker rent waiver", 0.8, "No team owns this query type yet"),
    (
        "apex",
        48204,
        "manual",
        "with_human",
        "EMI reschedule requests",
        0.88,
        "No approved content covers this — handed to a person",
    ),
    (
        "apex",
        48206,
        "manual",
        "with_human",
        "Chargeback status",
        0.95,
        "No approved content covers this — handed to a person",
    ),
    ("apex", 48207, "draft", "awaiting_approval", "Account maintenance", 0.8, "Cited draft ready to send"),
    (
        "apex",
        48209,
        "manual",
        "with_human",
        "Account maintenance",
        0.8,
        "No approved content covers this — handed to a person",
    ),
    ("apex", 48211, "manual", "with_human", "Stop payment instruction", 0.95, "Held back — third contact"),
    (
        "apex",
        48212,
        "manual",
        "with_human",
        "Disputed transactions",
        0.88,
        "Action matched but fields or confidence fall short",
    ),
    (
        "apex",
        48213,
        "manual",
        "with_human",
        "Account maintenance",
        0.8,
        "No approved content covers this — handed to a person",
    ),
    ("apex", 48214, "draft", "awaiting_approval", "SWIFT trace requests", 0.95, "Cited draft ready to send"),
    (
        "apex",
        48215,
        "manual",
        "with_human",
        "Balance & charge queries",
        0.88,
        "No approved content covers this — handed to a person",
    ),
    (
        "apex",
        48216,
        "auto",
        "executing",
        "Certificate requests",
        0.88,
        "Can be undone — the AI already did it",
    ),
    ("meridian", 1201, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
    ("northwind", 3301, "manual", "with_human", "Unclassified", 0.35, "No team owns this query type yet"),
]

DB = f"{TEST_DB}_triage"
API_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
async def triage_db() -> AsyncIterator[None]:
    """A private clone of the template; the runner's tenant transactions are pointed at it."""
    conn = await asyncpg.connect(f"{ADMIN_BASE}/postgres")
    try:
        await conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = $1 and pid <> pg_backend_pid()",
            DB,
        )
        await conn.execute(f'drop database if exists "{DB}"')
        await conn.execute(f'create database "{DB}" template "{TEMPLATE_DB}"')
    finally:
        await conn.close()
    host = ADMIN_BASE.split("@", 1)[1]
    # The template may predate the newest migration; bring the private clone to head.
    env = {**os.environ, "DATABASE_ADMIN_URL": f"postgresql+asyncpg://postgres:postgres@{host}/{DB}"}
    proc = subprocess.run(  # noqa: ASYNC221 - one-off setup of a test database
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from command_inbox.agents import status
    from command_inbox.agents.decision import set_engine_for_tests
    from command_inbox.agents.providers import set_provider_for_tests
    from command_inbox.db import engine as engine_mod

    eng = engine_mod.make_engine(f"postgresql+asyncpg://ci_app:ci_app@{host}/{DB}", pool_size=2)
    saved = engine_mod.Session
    engine_mod.Session = async_sessionmaker(eng, expire_on_commit=False, autoflush=False)
    set_engine_for_tests(None)
    set_provider_for_tests(None)
    status.reset()
    try:
        yield
    finally:
        engine_mod.Session = saved
        await eng.dispose()


async def _admin(sql: str, *args: Any) -> list[Any]:
    conn = await asyncpg.connect(f"{ADMIN_BASE}/{DB}")
    try:
        return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


def _job(org_id: str, ticket_id: str, **payload: Any) -> Any:
    from command_inbox.core.jobs import JobRow

    return JobRow(1, org_id, "triage", {"ticketId": ticket_id, "senderVerified": True, **payload}, 1, 5)


async def _ticket(ticket_id: str) -> dict[str, Any]:
    rows = await _admin("select * from tickets where id = $1", ticket_id)
    return dict(rows[0])


async def test_seeded_mail_lands_where_the_previous_service_put_it(triage_db):
    from command_inbox.agents.runner import run_triage_job

    # Parity is about the setup-synthesised deployment; detach any backfilled deployment.
    await _admin("update tickets set status = 'triaging', deployment_id = null, deployment_version_id = null")
    await _admin("update mailboxes set deployment_id = null")
    tickets = await _admin(
        "select t.id::text, t.org_id::text, t.number, o.slug from tickets t join orgs o on o.id = t.org_id "
        "order by o.slug, t.number"
    )
    for t in tickets:
        await run_triage_job(_job(t["org_id"], t["id"]))

    got = {
        (r["slug"], r["number"]): r
        for r in await _admin(
            "select o.slug, t.number, t.lane, t.status, t.bucket, t.confidence, t.lane_note "
            "from tickets t join orgs o on o.id = t.org_id"
        )
    }
    mismatches = []
    for slug, number, lane, status_, bucket, conf, note in TS_REFERENCE:
        r = got[(slug, number)]
        actual = (r["lane"], r["status"], r["bucket"], round(r["confidence"], 2), r["lane_note"])
        if actual != (lane, status_, bucket, conf, note):
            mismatches.append((number, actual, (lane, status_, bucket, conf, note)))
    assert not mismatches, mismatches

    # Every run left an explanation (run + spans), an audit entry with the previous action name, and events.
    counts = (
        await _admin(
            "select (select count(*) from triage_runs) runs, (select count(*) from trace_spans) spans, "
            "(select count(*) from audit_events where action = 'triage.completed') audits, "
            "(select count(*) from outbox where topic = 'ticket.created') created"
        )
    )[0]
    assert counts["runs"] >= len(TS_REFERENCE) and counts["spans"] > 5 * len(TS_REFERENCE)
    assert counts["audits"] >= len(TS_REFERENCE) and counts["created"] >= len(TS_REFERENCE)


async def test_auto_lane_writes_the_action_and_queues_execution(triage_db):
    rows = await _admin(
        "select t.id::text, a.state, a.chain, (select count(*) from jobs j where j.kind = 'execute_action' "
        "and j.payload->>'actionId' = a.id::text) queued from tickets t join action_instances a on a.ticket_id = t.id "
        "where t.number = 48216"
    )
    assert (
        rows and rows[-1]["state"] == "scheduled" and rows[-1]["chain"] == "auto" and rows[-1]["queued"] == 1
    )


async def test_draft_lane_writes_a_cited_draft(triage_db):
    rows = await _admin(
        "select d.citations, d.current_body from drafts d join tickets t on t.id = d.ticket_id where t.number = 48214"
    )
    assert rows and "[1]" in rows[0]["current_body"]


async def test_rerun_is_idempotent(triage_db):
    from command_inbox.agents.runner import run_triage_job

    t = (await _admin("select id::text, org_id::text, version from tickets where number = 48216"))[0]
    before = await _admin("select count(*) n from triage_runs where ticket_id = $1", t["id"])
    await run_triage_job(_job(t["org_id"], t["id"]))  # already triaged: nothing happens
    after = await _admin("select count(*) n from triage_runs where ticket_id = $1", t["id"])
    assert before[0]["n"] == after[0]["n"]
    assert (await _ticket(t["id"]))["version"] == t["version"]


async def test_unverified_sender_never_reaches_auto(triage_db):
    from command_inbox.agents.runner import run_triage_job

    t = (await _admin("select id::text, org_id::text from tickets where number = 48181"))[0]
    await _admin("delete from action_instances where ticket_id = $1", t["id"])
    await _admin("update tickets set status = 'triaging' where id = $1", t["id"])
    await run_triage_job(_job(t["org_id"], t["id"], senderVerified=False))
    row = await _ticket(t["id"])
    assert row["lane"] == "manual" and row["lane_note"].startswith("Sender could not be verified")


async def test_hard_stop_is_always_manual_even_when_forced(triage_db):
    from command_inbox.agents.runner import run_triage_job

    t = (await _admin("select id::text, org_id::text from tickets where number = 48199"))[0]
    await _admin("update tickets set status = 'triaging' where id = $1", t["id"])
    await run_triage_job(_job(t["org_id"], t["id"], forceLane="auto"))
    row = await _ticket(t["id"])
    assert row["lane"] == "manual" and row["lane_note"].startswith("Held back")


async def test_published_deployment_version_is_used_and_recorded(triage_db):
    from command_inbox.agents.config import DeploymentConfig
    from command_inbox.agents.runner import run_triage_job
    from command_inbox.agents.synthesis import synthesise_config
    from command_inbox.db import engine as engine_mod
    from command_inbox.db.models import BucketRule, Department, PriorityRule, QueryType

    t = (await _admin("select id::text, org_id::text from tickets where number = 48214"))[0]
    org = t["org_id"]
    async with engine_mod.tenant_tx(org) as tx:
        qts = list((await tx.execute(select(QueryType).where(QueryType.org_id == org))).scalars())
        depts = {str(d.id): d.name for d in (await tx.execute(select(Department))).scalars()}
        brs = list((await tx.execute(select(BucketRule))).scalars())
        prs = list((await tx.execute(select(PriorityRule))).scalars())
        config = synthesise_config(
            confidence_bar=0.78, departments=depts, query_types=qts, bucket_rules=brs, priority_rules=prs
        ).config
        cfg = DeploymentConfig.model_validate(config.model_dump(mode="json", by_alias=True))
        dep_id = (
            await tx.execute(
                text(
                    "insert into deployments (org_id, key, name) values (:o, 'retail_test', 'Retail test') "
                    "returning id::text"
                ),
                {"o": org},
            )
        ).scalar_one()
        ver_id = (
            await tx.execute(
                text(
                    "insert into deployment_versions (org_id, deployment_id, version, state, config) "
                    "values (:o, :d, 1, 'published', cast(:c as jsonb)) returning id::text"
                ),
                {"o": org, "d": dep_id, "c": cfg.model_dump_json(by_alias=True)},
            )
        ).scalar_one()
        await tx.execute(
            text("update deployments set active_version_id = :v where id = :d"), {"v": ver_id, "d": dep_id}
        )
        await tx.execute(
            text("update tickets set status = 'triaging', deployment_id = :d where id = :t"),
            {"d": dep_id, "t": t["id"]},
        )
    await run_triage_job(_job(org, t["id"]))
    row = await _ticket(t["id"])
    assert str(row["deployment_version_id"]) == ver_id
    assert row["lane"] == "draft" and row["bucket"] == "SWIFT trace requests"


async def test_final_failure_hands_the_ticket_to_a_person(triage_db):
    from command_inbox.agents.runner import handle_final_failure

    t = (await _admin("select id::text, org_id::text from tickets where number = 48215"))[0]
    await _admin("update tickets set status = 'triaging' where id = $1", t["id"])
    await handle_final_failure(_job(t["org_id"], t["id"]))
    row = await _ticket(t["id"])
    assert row["lane"] == "manual" and row["status"] == "with_human"
    audits = await _admin(
        "select count(*) n from audit_events where action = 'triage.failed' and ticket_id = $1", t["id"]
    )
    assert audits[0]["n"] == 1


async def test_backfilled_default_deployment_keeps_the_lanes(triage_db):
    """The default deployment migration 0004 builds from the same setup triages the seeded mail into the
    same lanes and buckets (wording of notes may differ)."""
    from command_inbox.agents.runner import run_triage_job

    dv = await _admin(
        "select d.org_id::text, d.id::text dep, d.active_version_id::text ver from deployments d where d.key = 'default'"
    )
    if not dv:
        pytest.skip("no backfilled default deployment in this template")
    for r in dv:
        await _admin(
            "update tickets set status = 'triaging', deployment_id = $2::uuid, deployment_version_id = $3::uuid "
            "where org_id = $1::uuid",
            r["org_id"],
            r["dep"],
            r["ver"],
        )
    await _admin("delete from action_instances")
    tickets = await _admin(
        "select t.id::text, t.org_id::text, t.number, o.slug from tickets t join orgs o on o.id = t.org_id "
        "order by o.slug, t.number"
    )
    for t in tickets:
        await run_triage_job(_job(t["org_id"], t["id"]))
    got = {
        (r["slug"], r["number"]): (r["lane"], r["bucket"])
        for r in await _admin(
            "select o.slug, t.number, t.lane, t.bucket from tickets t join orgs o on o.id = t.org_id"
        )
    }
    mismatches = [
        (n, got[(s, n)], (lane, bucket))
        for s, n, lane, _st, bucket, _c, _note in TS_REFERENCE
        if got[(s, n)] != (lane, bucket)
    ]
    assert not mismatches, mismatches
