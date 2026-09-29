"""A freshly created workspace with nothing in it: every screen's API must answer, not fail.

The demo seed hides a whole class of bugs (a missing default deployment, an empty series, a division by an
empty count). This module creates a tenant the way a real bank starts: one admin, no mailboxes, no
deployments, no knowledge, no tickets, no metrics, and calls every read endpoint the app uses.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from tests.integration.conftest import Client, sign_in

# Endpoints that are not screen reads: streams, redirects to the IdP, and the development sign-in data.
SKIP = re.compile(r"^/v1/(stream|auth/oidc/|auth/demo|dev/)")


async def _empty_workspace(role_emails: dict[str, str]) -> str:
    from command_inbox.db.engine import global_tx
    from command_inbox.db.models import Membership, Org, User

    tag = uuid.uuid4().hex[:8]
    async with global_tx() as g:
        org = Org(
            slug=f"empty-{tag}",
            name="Empty Bank",
            short="EB",
            tint="#333333",
            bg="#EEEEEE",
            plan="Enterprise",
            locale="en-GB",
            currency="GBP",
            time_zone="Europe/London",
        )
        g.add(org)
        await g.flush()
        for role, email in role_emails.items():
            user = User(email=email, name=f"{role.title()} {tag}", initials=role[:2].upper())
            g.add(user)
            await g.flush()
            g.add(Membership(org_id=org.id, user_id=user.id, role=role, title=role))
        return str(org.id)


@pytest.fixture(scope="module")
async def empty(app: Any) -> dict[str, Client]:
    tag = uuid.uuid4().hex[:8]
    emails = {role: f"{role}.{tag}@empty.example" for role in ("admin", "lead", "staff")}
    oid = await _empty_workspace(emails)
    clients = {role: await sign_in(app, email) for role, email in emails.items()}
    for c in clients.values():
        assert c.me["org"]["id"] == oid
    return clients


def _screen_reads(app: Any) -> list[str]:
    paths = app.openapi()["paths"]
    return sorted(
        p
        for p, ops in paths.items()
        if "get" in ops and p.startswith("/v1/") and "{" not in p and not SKIP.match(p)
    )


async def test_the_workspace_starts_empty_in_its_own_locale(empty):
    me = empty["admin"].me
    assert me["org"]["currency"] == "GBP" and me["org"]["timeZone"] == "Europe/London"
    assert len(_screen_reads(empty["admin"].http._transport.app)) > 20  # type: ignore[attr-defined]


@pytest.mark.parametrize("role", ["admin", "lead", "staff"])
async def test_every_read_endpoint_answers_on_an_empty_tenant(app, empty, role):
    failures = []
    for path in _screen_reads(app):
        r = await empty[role].get(path)
        # Reads may refuse a role (403) but must never fail (5xx) or claim the screen is missing (404).
        if r.status_code >= 500 or r.status_code == 404:
            failures.append(f"{path} → {r.status_code} {r.text[:200]}")
    assert not failures, "\n".join(failures)
