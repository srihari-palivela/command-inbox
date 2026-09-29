"""Verify every tenant's audit hash chain in a database (restore drills, incident checks).

    DRILL_DATABASE_URL=postgresql+asyncpg://owner:...@host/db python -m command_inbox.core.audit_verify_all

Exits 1 if any chain is broken, printing the tenant and the first sequence number that does not verify.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from command_inbox.core.audit import verify_audit_chain


async def main() -> int:
    url = os.environ.get("DRILL_DATABASE_URL") or os.environ.get("DATABASE_ADMIN_URL")
    if not url:
        print("set DRILL_DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    broken = 0
    try:
        async with sessions() as s:
            orgs = (await s.execute(text("select id, slug from orgs order by slug"))).all()
        for org_id, slug in orgs:
            async with sessions() as s, s.begin():
                await s.execute(text("select set_config('app.org_id', :o, true)"), {"o": str(org_id)})
                r = await verify_audit_chain(s, str(org_id))
            status = "ok" if r["ok"] else f"BROKEN at {r['brokenAt']}"
            print(f"{slug}: {r['events']} events, {status}")
            broken += not r["ok"]
    finally:
        await engine.dispose()
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
