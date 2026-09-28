"""The seed: deterministic for a pinned clock, audit chains that verify, and a valid default deployment.

Runs on its own databases (`<SEED_TEST_DB>_1`, `_2`), never on the shared test template.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from tests.conftest import ADMIN_BASE

pytestmark = pytest.mark.integration

PREFIX = os.environ.get("SEED_TEST_DB", "command_inbox_test_seed")
NOW = datetime(2026, 9, 28, 12, 2, tzinfo=UTC)


def _url(db: str) -> str:
    return f"{ADMIN_BASE.replace('postgresql://', 'postgresql+asyncpg://')}/{db}"


async def _fresh_seeded(db: str) -> str:
    from command_inbox.seed import seed
    from command_inbox.seed.cli import migrate

    conn = await asyncpg.connect(f"{ADMIN_BASE}/postgres")
    try:
        await conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = $1 and pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'drop database if exists "{db}"')
        await conn.execute(f'create database "{db}"')
    finally:
        await conn.close()
    url = _url(db)
    await asyncio.to_thread(migrate, url)  # alembic's env runs its own event loop
    await seed(url, NOW)
    return url


async def _digest(engine: AsyncEngine) -> dict[str, str]:
    """sha256 per table over every column of every row, rows in a canonical order."""
    out: dict[str, str] = {}
    async with engine.connect() as conn:
        tables = [
            r[0]
            for r in await conn.execute(
                text(
                    "select table_name from information_schema.tables where table_schema = 'public' "
                    "and table_type = 'BASE TABLE' order by 1"
                )
            )
        ]
        for table in tables:
            query = text(f'select * from "{table}"')  # noqa: S608 - table names come from the catalog
            rows = (await conn.execute(query)).mappings().all()
            encoded = sorted(json.dumps(dict(r), sort_keys=True, default=str) for r in rows)
            out[table] = f"{len(rows)}:" + hashlib.sha256("\n".join(encoded).encode()).hexdigest()
    return out


@pytest.fixture(scope="module")
async def seeded() -> AsyncIterator[list[AsyncEngine]]:
    engines = [create_async_engine(await _fresh_seeded(f"{PREFIX}_{n}")) for n in (1, 2)]
    yield engines
    for e in engines:
        await e.dispose()


async def test_two_seeds_with_the_same_clock_are_identical(seeded: list[AsyncEngine]) -> None:
    first, second = await _digest(seeded[0]), await _digest(seeded[1])
    assert first == second
    assert int(first["tickets"].split(":")[0]) == 34
    assert int(first["audit_events"].split(":")[0]) > 30


async def test_every_audit_chain_verifies(seeded: list[AsyncEngine]) -> None:
    from command_inbox.core.audit import verify_audit_chain

    async with AsyncSession(seeded[0]) as tx:
        orgs = (await tx.execute(text("select id, slug from orgs order by slug"))).all()
        assert [o.slug for o in orgs] == ["apex", "meridian", "northwind"]
        for org_id, slug in orgs:
            result: dict[str, Any] = await verify_audit_chain(tx, str(org_id))
            assert result["ok"], (slug, result)
            assert result["events"] >= 2


async def test_default_deployment_is_published_valid_and_bound(seeded: list[AsyncEngine]) -> None:
    from command_inbox.agents.config import DeploymentConfig

    async with AsyncSession(seeded[0]) as tx:
        versions = (
            await tx.execute(
                text(
                    """select d.org_id, v.id, v.config, v.config_hash, v.state
                         from deployments d join deployment_versions v on v.id = d.active_version_id"""
                )
            )
        ).all()
        assert len(versions) == 3
        for org_id, version_id, config, config_hash, state in versions:
            assert state == "published"
            assert DeploymentConfig.model_validate(config).config_hash() == config_hash
            unbound = (
                await tx.execute(
                    text(
                        "select count(*) from tickets where org_id = :o "
                        "and deployment_version_id is distinct from :v"
                    ),
                    {"o": org_id, "v": version_id},
                )
            ).scalar()
            assert unbound == 0
        assert (
            await tx.execute(text("select count(*) from mailboxes where deployment_id is null"))
        ).scalar() == 0

        cases = (
            (
                await tx.execute(
                    text(
                        """select c.expected from eval_cases c join eval_datasets s on s.id = c.dataset_id
                         join orgs o on o.id = s.org_id where o.slug = 'apex'"""
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(cases) >= 20
        stops = [c for c in cases if c["hardStop"]]
        assert stops and all(c["lane"] == "manual" for c in stops)
