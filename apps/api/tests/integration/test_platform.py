"""Platform guarantees: authentication, CSRF, tenant isolation, audit immutability, access control."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_unauthenticated_is_a_401_problem(anon):
    r = await anon.get("/v1/inbox")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "unauthenticated"


async def test_lookalike_paths_are_not_public(anon):
    assert (await anon.get("/v1/auth/login-other")).status_code == 401


async def test_state_change_without_csrf_is_refused(staff):
    r = await staff.http.post("/v1/sessions/revoke-others")
    assert r.status_code == 403
    assert r.json()["code"] == "csrf"


async def test_one_role_capabilities(staff, lead, admin):
    assert staff.me["role"] == "staff" and "action.approve_checker" not in staff.me["capabilities"]
    assert "action.approve_checker" in lead.me["capabilities"]
    assert {"deployment.publish", "rbac.manage", "members.manage"} <= set(admin.me["capabilities"])
    assert "deployment.publish" not in lead.me["capabilities"]


async def test_rls_hides_rows_without_a_tenant(app):
    from command_inbox.db.engine import global_tx

    async with global_tx() as tx:
        assert (await tx.execute(text("select count(*) from tickets"))).scalar() == 0


async def test_rls_scopes_to_one_tenant(app, staff):
    from command_inbox.db.engine import tenant_tx

    async with tenant_tx(staff.me["org"]["id"]) as tx:
        n, orgs = (await tx.execute(text("select count(*), count(distinct org_id) from tickets"))).one()
    assert n > 10 and orgs == 1


async def test_audit_log_is_append_only(app, staff):
    from command_inbox.db.engine import tenant_tx

    for statement in ("update audit_events set summary = 'tampered'", "delete from audit_events"):
        with pytest.raises(Exception):  # noqa: B017 - either the trigger or the revoked grant fires
            async with tenant_tx(staff.me["org"]["id"]) as tx:
                await tx.execute(text(statement))


async def test_audit_chain_verifies_including_chains_written_by_the_previous_service(app, staff):
    from command_inbox.core.audit import verify_audit_chain
    from command_inbox.db.engine import tenant_tx

    async with tenant_tx(staff.me["org"]["id"]) as tx:
        result = await verify_audit_chain(tx, staff.me["org"]["id"])
    assert result["ok"], result


async def test_tenant_cannot_grant_checker_to_staff():
    from command_inbox.core.errors import AppError
    from command_inbox.rbac.policy import validate_override

    for role, cap in (
        ("staff", "action.approve_checker"),
        ("lead", "deployment.publish"),
        ("admin", "setup.view"),
    ):
        with pytest.raises(AppError):
            validate_override(role, cap, "allow")
    validate_override("staff", "insights.view", "allow")
