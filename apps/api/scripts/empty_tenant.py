"""Create (once) an empty workspace for smoke tests: one admin, and nothing else.

    uv run python scripts/empty_tenant.py      # prints the admin's email

Development and CI only. It is how a real bank starts before onboarding: no mailboxes, deployments,
knowledge, tickets or metrics. The Playwright smoke suite signs in as its admin and opens every screen.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

SLUG = "empty-smoke"
ADMIN_EMAIL = "admin@empty-smoke.example"


async def ensure() -> str:
    from command_inbox.config import settings
    from command_inbox.db.engine import dispose, global_tx
    from command_inbox.db.models import Membership, Org, User

    if settings.is_prod:
        raise SystemExit("refusing to create a smoke-test workspace in production")
    try:
        async with global_tx() as g:
            if (await g.execute(select(Org.id).where(Org.slug == SLUG))).scalar_one_or_none():
                return ADMIN_EMAIL
            org = Org(
                slug=SLUG,
                name="Empty Bank",
                short="EB",
                tint="#3D4451",
                bg="#ECEEF1",
                plan="Enterprise",
                locale="en-GB",
                currency="GBP",
                time_zone="Europe/London",
            )
            user = User(email=ADMIN_EMAIL, name="Empty Admin", initials="EA")
            g.add_all([org, user])
            await g.flush()
            g.add(Membership(org_id=org.id, user_id=user.id, role="admin", title="Administrator"))
        return ADMIN_EMAIL
    finally:
        await dispose()


if __name__ == "__main__":
    print(asyncio.run(ensure()))
