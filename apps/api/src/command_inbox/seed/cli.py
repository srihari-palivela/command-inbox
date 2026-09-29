"""`command-inbox-seed --demo`: migrate a database to head, then seed the demo tenants (development and CI).

    command-inbox-seed --demo [--reset] [--if-empty] [--database-url URL] [--now 2026-09-28T12:02:00Z]

The demo banks, people and mail are fiction for development, tests and walkthroughs. `--demo` must be
given explicitly and the command refuses to run in production; a deployment runs `command-inbox-migrate`.
`--reset` drops and recreates the public schema first (like `pnpm db:reset`).
`--now` pins the seed clock, which makes the seeded rows identical from run to run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from command_inbox.migrate import migrate
from command_inbox.seed import asyncpg_url, has_data, seed


async def reset(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("drop schema if exists drizzle cascade"))
            await conn.execute(text("drop schema public cascade"))
            await conn.execute(text("create schema public"))
    finally:
        await engine.dispose()


def _parse_now(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    from command_inbox.config import settings

    p = argparse.ArgumentParser(prog="command-inbox-seed", description=__doc__.splitlines()[0])
    p.add_argument("--demo", action="store_true", help="required: confirm you want the fictional demo data")
    p.add_argument(
        "--reset", action="store_true", help="drop and recreate the schema first (not in production)"
    )
    p.add_argument(
        "--if-empty", action="store_true", help="do nothing when the database already has a workspace"
    )
    p.add_argument("--database-url", default=None, help="schema-owner URL (default: DATABASE_ADMIN_URL)")
    p.add_argument("--now", type=_parse_now, default=None, help="pin the seed clock (ISO-8601, UTC)")
    args = p.parse_args(argv)

    if not args.demo:
        print(
            "the seed writes fictional demo tenants; pass --demo to confirm (use command-inbox-migrate "
            "to prepare a real database)",
            file=sys.stderr,
        )
        return 2
    if settings.is_prod:
        print("refusing to seed demo data in production; run command-inbox-migrate", file=sys.stderr)
        return 2
    url = asyncpg_url(args.database_url or settings.database_admin_url)
    if args.reset:
        asyncio.run(reset(url))
    migrate(url)
    if asyncio.run(has_data(url)):
        if args.if_empty:
            print("database already seeded; leaving it alone")
            return 0
        print("database already holds a workspace; use --reset to start over", file=sys.stderr)
        return 1
    result = asyncio.run(seed(url, args.now))
    print(f"seeded {len(result.orgs)} workspaces at {result.now.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
