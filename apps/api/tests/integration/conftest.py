"""Fixtures for API integration tests: a fresh test database, the ASGI app, and signed-in clients."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
import pytest

from tests.conftest import ADMIN_BASE, TEMPLATE_DB, TEST_DB


async def _recreate_test_db() -> None:
    conn = await asyncpg.connect(f"{ADMIN_BASE}/postgres")
    try:
        await conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = $1 and pid <> pg_backend_pid()",
            TEST_DB,
        )
        await conn.execute(f'drop database if exists "{TEST_DB}"')
        await conn.execute(f'create database "{TEST_DB}" template "{TEMPLATE_DB}"')
    finally:
        await conn.close()


def _migrate_test_db() -> None:
    """Bring the clone up to the latest migration (the template may be older than head)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
async def app() -> AsyncIterator[Any]:
    import asyncio

    await _recreate_test_db()
    await asyncio.to_thread(_migrate_test_db)  # env.py runs its own event loop
    from command_inbox.config import settings
    from command_inbox.main import create_app

    # The suite makes thousands of requests a minute as a few people; the limiter has its own tests.
    settings.api_rate_per_minute = 0
    settings.api_rate_per_user_per_minute = 0
    application = create_app()
    yield application
    from command_inbox.db.engine import dispose

    await dispose()


@dataclass
class Client:
    """A signed-in browser: cookie jar + CSRF token, like the SPA."""

    http: httpx.AsyncClient
    me: dict[str, Any]

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.http.get(url, **kw)

    async def send(
        self, method: str, url: str, json: Any = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        h = {"x-csrf-token": self.me["csrfToken"], **(headers or {})}
        return await self.http.request(method, url, json=json, headers=h)

    async def ticket(self, id_or_number: str) -> dict[str, Any]:
        r = await self.get(f"/v1/tickets/{id_or_number}")
        assert r.status_code == 200, r.text
        return r.json()


async def sign_in(app: Any, email: str) -> Client:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await http.post("/v1/auth/login", json={"email": email})
    assert r.status_code == 200, r.text
    return Client(http, r.json())


@pytest.fixture(scope="session")
async def staff(app: Any) -> Client:
    return await sign_in(app, "p.sharma@bank.example")


@pytest.fixture(scope="session")
async def lead(app: Any) -> Client:
    return await sign_in(app, "r.menon@bank.example")


@pytest.fixture(scope="session")
async def admin(app: Any) -> Client:
    return await sign_in(app, "a.kapoor@bank.example")


@pytest.fixture
async def anon(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
