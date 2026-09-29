"""Database engines and the tenant transaction.

The API and worker connect as `ci_app`, a role that owns nothing, so Postgres row-level security applies
to every tenant table. `tenant_tx` binds `app.org_id` for the length of one transaction: a query that
forgets its `org_id` filter still cannot see another bank's rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from command_inbox.config import settings


def make_engine(url: str, pool_size: int | None = None) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=pool_size or settings.db_pool_size,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        # Statement-level timeout so a runaway query cannot pin a connection.
        connect_args={
            "server_settings": {"statement_timeout": "30000", "application_name": settings.otel_service_name}
        },
    )


engine: AsyncEngine = make_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

Tx = AsyncSession
"""A transaction handle; tenant work always runs inside one."""


@asynccontextmanager
async def tenant_tx(org_id: str) -> AsyncIterator[AsyncSession]:
    """One transaction bound to one tenant. Commits on success, rolls back on any exception."""
    async with Session() as session, session.begin():
        await session.execute(text("select set_config('app.org_id', :org, true)"), {"org": str(org_id)})
        yield session


@asynccontextmanager
async def global_tx() -> AsyncIterator[AsyncSession]:
    """A transaction with no tenant bound: sees only non-tenant tables (users, sessions, jobs, outbox)."""
    async with Session() as session, session.begin():
        yield session


async def rows(tx: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Raw SQL helper returning dicts, for the reporting queries that read better as SQL."""
    result = await tx.execute(text(sql), params or {})
    return [dict(r._mapping) for r in result]


async def dispose() -> None:
    await engine.dispose()
