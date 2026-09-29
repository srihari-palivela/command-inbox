"""Helpers for the tenant-administration tests (deployments, evals, members).

These tests change roles, permissions and deployments, so they work in their own users and — where they
mutate shared state — in the `meridian` and `northwind` workspaces, leaving `apex` (used by most other
test modules) as seeded.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from sqlalchemy import select

from tests.conftest import ADMIN_BASE, TEST_DB
from tests.integration.conftest import Client, sign_in


async def admin_conn() -> asyncpg.Connection:
    """A schema-owner connection (bypasses RLS) for arranging and inspecting rows."""
    return await asyncpg.connect(f"{ADMIN_BASE}/{TEST_DB}")


async def org_id(slug: str) -> str:
    from command_inbox.db.engine import global_tx
    from command_inbox.db.models import Org

    async with global_tx() as g:
        return str((await g.execute(select(Org.id).where(Org.slug == slug))).scalar_one())


async def add_member(app: Any, slug: str, role: str, name: str | None = None) -> Client:
    """Create a user with one role in the workspace and sign them in (demo login)."""
    from command_inbox.db.engine import global_tx
    from command_inbox.db.models import Membership, User

    tag = uuid.uuid4().hex[:8]
    email = f"{role}.{tag}@{slug}.example"
    name = name or f"{role.title()} {tag}"
    oid = await org_id(slug)
    async with global_tx() as g:
        user = User(email=email, name=name, initials=name[:2].upper())
        g.add(user)
        await g.flush()
        g.add(Membership(org_id=oid, user_id=user.id, role=role, title=role))
    client = await sign_in(app, email)
    assert client.me["org"]["id"] == oid, "the new member signs in to the workspace they joined"
    return client


async def drain() -> int:
    from command_inbox.core.jobs import Worker
    from command_inbox.worker_registry import register_all

    return await register_all(Worker()).drain()


def desk_config(**overrides: Any) -> dict[str, Any]:
    """A small, well-separated taxonomy the deterministic engines categorise correctly."""
    from command_inbox.agents.config import standard_flow

    config: dict[str, Any] = {
        "taxonomy": {
            "categories": [
                {
                    "key": "cheque_stop",
                    "name": "Cheque stop request",
                    "description": "The customer asks the bank to stop a cheque.",
                    "examples": ["stop the cheque", "cancel cheque number", "do not honour the cheque"],
                    "department": "Operations",
                    "defaultLane": "auto",
                },
                {
                    "key": "statement_copy",
                    "name": "Statement copy request",
                    "description": "The customer wants a copy of an account statement.",
                    "examples": ["copy of my statement", "statement for the last", "email the statement"],
                    "department": "Service",
                    "defaultLane": "draft",
                },
                {
                    "key": "other",
                    "name": "Anything else",
                    "description": "Mail that fits no other category.",
                    "examples": ["general question", "just wanted to ask", "quick query about"],
                    "department": "Service",
                    "defaultLane": "manual",
                },
            ],
            "fallback": "other",
        },
        "rules": {
            "hardStops": [
                {
                    "key": "regulator_named",
                    "label": "Regulator named",
                    "keywords": ["ombudsman", "regulator"],
                }
            ]
        },
        "flow": standard_flow().model_dump(mode="json", by_alias=True),
    }
    config.update(overrides)
    return config


def case(category: str, split: str, *, hard_stop: bool = False, body: str | None = None) -> dict[str, Any]:
    bodies = {
        "cheque_stop": "Please stop the cheque, cancel cheque number 004512 and do not honour the cheque.",
        "statement_copy": "I need a copy of my statement: the statement for the last quarter. Please email the statement.",
        "other": "Just wanted to ask a general question, a quick query about your branch hours.",
    }
    text_ = body or bodies[category]
    if hard_stop and body is None:
        text_ += " Otherwise I will write to the banking ombudsman."
    return {
        "input": {
            "subject": f"Re: {category.replace('_', ' ')}",
            "body": text_,
            "fromEmail": "customer@mail.example",
        },
        "expected": {"category": category, "hardStop": hard_stop},
        "split": split,
    }


def passing_cases() -> list[dict[str, Any]]:
    """20 calibration cases (enough to certify 95% conformal coverage) and 12 test cases, 3 with hard stops."""
    cats = ["cheque_stop", "statement_copy", "other"]
    calibration = [case(cats[i % 3], "calibration") for i in range(20)]
    test = [case(c, "test") for c in cats for _ in range(3)]
    test += [case("cheque_stop", "test", hard_stop=True), case("statement_copy", "test", hard_stop=True)]
    test += [case("other", "test", hard_stop=True)]
    return calibration + test


async def make_deployment(c: Client, key: str, config: dict[str, Any] | None = None) -> tuple[str, str]:
    """Create a deployment with its draft v1 set to `config`; returns (deployment id, version id)."""
    r = await c.send("POST", "/v1/deployments", {"key": key, "name": key.replace("_", " ").title()})
    assert r.status_code == 200, r.text
    dep = r.json()
    vid = dep["draftVersionId"]
    r = await c.send(
        "PUT", f"/v1/deployments/{dep['id']}/versions/{vid}/config", {"config": config or desk_config()}
    )
    assert r.status_code == 200, r.text
    return dep["id"], vid


async def make_dataset(c: Client, deployment_id: str, cases: list[dict[str, Any]] | None = None) -> str:
    r = await c.send(
        "POST",
        "/v1/evals/datasets",
        {"deploymentId": deployment_id, "name": f"golden {uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code == 200, r.text
    ds = r.json()["id"]
    r = await c.send("POST", f"/v1/evals/datasets/{ds}/cases", {"cases": cases or passing_cases()})
    assert r.status_code == 200, r.text
    return ds


async def run_eval(c: Client, version_id: str, dataset_id: str) -> dict[str, Any]:
    r = await c.send("POST", "/v1/evals/runs", {"deploymentVersionId": version_id, "datasetId": dataset_id})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "queued"
    await drain()
    r = await c.get(f"/v1/evals/runs/{r.json()['id']}")
    assert r.status_code == 200, r.text
    return r.json()


async def audit_actions(oid: str, since_seq: int = 0) -> list[dict[str, Any]]:
    conn = await admin_conn()
    try:
        rows = await conn.fetch(
            "select seq, action, summary, data from audit_events where org_id = $1 and seq > $2 order by seq",
            uuid.UUID(oid),
            since_seq,
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def last_seq(oid: str) -> int:
    conn = await admin_conn()
    try:
        return int(
            await conn.fetchval(
                "select coalesce(max(seq), 0) from audit_events where org_id = $1", uuid.UUID(oid)
            )
        )
    finally:
        await conn.close()


async def outbox_topics(oid: str, since_id: int = 0) -> list[str]:
    conn = await admin_conn()
    try:
        rows = await conn.fetch(
            "select topic from outbox where org_id = $1 and id > $2 order by id", uuid.UUID(oid), since_id
        )
    finally:
        await conn.close()
    return [r["topic"] for r in rows]


async def last_outbox_id() -> int:
    conn = await admin_conn()
    try:
        return int(await conn.fetchval("select coalesce(max(id), 0) from outbox"))
    finally:
        await conn.close()


async def set_stage(org_id: str, stage: str) -> None:
    """Put a workspace at a pilot stage directly (the sign-off itself is covered by test_pilot)."""
    conn = await admin_conn()
    try:
        await conn.execute(
            "update orgs set status = $2, status_changed_at = now() where id = $1", org_id, stage
        )
    finally:
        await conn.close()
