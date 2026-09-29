"""Authorisation: Casbin RBAC-with-domains over a single role per user per tenant.

Every user holds exactly one role in a tenant (the `memberships` primary key is (org_id, user_id) with one
`role` column, constrained to staff | lead | admin). The role is the Casbin subject, the tenant is the
domain, and a capability `object.action` is what is checked.

Two layers of policy:
- The product baseline (`policy.csv`, domain "*"), reviewed as code.
- Tenant overrides written by that tenant's admin (`role_policies`), limited to DELEGABLE capabilities.
  Separation-of-duties rules are LOCKED: a tenant cannot let staff act as checker, cannot hand autonomy,
  audit or access control to a non-admin, and cannot lock its own admins out.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import casbin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import ROLE_LABEL, ROLES, Ctx
from command_inbox.core.errors import bad_request, forbidden

_HERE = Path(__file__).parent

CAPABILITIES: tuple[str, ...] = (
    "ticket.work",
    "ticket.reply",
    "ticket.assign",
    "ticket.override_up",
    "action.approve_maker",
    "action.approve_checker",
    "people.edit_clearance",
    "people.auto_assign",
    "insights.view",
    "kpi.manage",
    "learning.send",
    "setup.view",
    "setup.edit",
    "autonomy.change",
    "rules.edit",
    "audit.verify",
    "deployment.view",
    "deployment.edit",
    "deployment.publish",
    "evals.view",
    "evals.run",
    "members.manage",
    "rbac.manage",
)

# What a tenant admin may grant to or remove from staff and team leads.
DELEGABLE: frozenset[str] = frozenset(
    {
        "ticket.assign",
        "ticket.override_up",
        "people.edit_clearance",
        "people.auto_assign",
        "insights.view",
        "kpi.manage",
        "learning.send",
        "setup.view",
        "rules.edit",
        "deployment.view",
        "deployment.edit",
        "evals.view",
        "evals.run",
    }
)
# Never grantable to these roles, whatever a tenant configures (separation of duties).
NEVER: dict[str, frozenset[str]] = {
    "staff": frozenset({"action.approve_checker"}),
    "lead": frozenset(),
    "admin": frozenset(),
}
ADMIN_ONLY: frozenset[str] = frozenset(
    {
        "autonomy.change",
        "audit.verify",
        "members.manage",
        "rbac.manage",
        "deployment.publish",
        "setup.edit",
    }
)

CAPABILITY_LABEL: dict[str, str] = {
    "ticket.work": "Work tickets",
    "ticket.reply": "Reply to customers",
    "ticket.assign": "Reassign tickets",
    "ticket.override_up": "Give the AI more autonomy on a ticket",
    "action.approve_maker": "Approve actions (maker)",
    "action.approve_checker": "Counter-approve (checker)",
    "people.edit_clearance": "Edit clearances",
    "people.auto_assign": "Run auto-assign",
    "insights.view": "See performance and results",
    "kpi.manage": "Manage team KPIs",
    "learning.send": "Send learning updates",
    "setup.view": "See AI setup",
    "setup.edit": "Edit AI setup",
    "autonomy.change": "Change the autonomy dial",
    "rules.edit": "Edit rules and policies",
    "audit.verify": "Verify the audit log",
    "deployment.view": "See deployments",
    "deployment.edit": "Edit deployment drafts",
    "deployment.publish": "Publish deployments",
    "evals.view": "See eval results",
    "evals.run": "Run evals",
    "members.manage": "Manage members and roles",
    "rbac.manage": "Change role permissions",
}


class PolicyStore:
    """Process-wide Casbin enforcer with per-tenant override rows, cached briefly."""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._enforcer = casbin.Enforcer(str(_HERE / "model.conf"), str(_HERE / "policy.csv"))
        self._loaded: dict[str, float] = {}
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    def baseline(self, role: str) -> frozenset[str]:
        return frozenset(c for c in CAPABILITIES if self._enforcer.enforce(role, "*", *c.split(".", 1)))

    async def _ensure_tenant(self, tx: AsyncSession, org_id: str) -> None:
        loaded = self._loaded.get(org_id)
        if loaded and time.monotonic() - loaded < self._ttl:
            return
        from command_inbox.db.models import RolePolicy

        async with self._lock:
            rows = (await tx.execute(select(RolePolicy).where(RolePolicy.org_id == org_id))).scalars().all()
            self._enforcer.remove_filtered_policy(1, org_id)
            for r in rows:
                obj, act = r.capability.split(".", 1)
                self._enforcer.add_policy(r.role, org_id, obj, act, r.effect)
            self._loaded[org_id] = time.monotonic()

    def invalidate(self, org_id: str) -> None:
        self._loaded.pop(org_id, None)

    async def capabilities(self, tx: AsyncSession, org_id: str, role: str) -> frozenset[str]:
        await self._ensure_tenant(tx, org_id)
        allowed = {c for c in CAPABILITIES if self._enforcer.enforce(role, org_id, *c.split(".", 1))}
        # Locked rules win over anything a tenant row says.
        allowed -= NEVER.get(role, frozenset())
        if role != "admin":
            allowed -= ADMIN_ONLY
        return frozenset(allowed)


policies = PolicyStore()


def validate_override(role: str, capability: str, effect: str) -> None:
    """Reject a tenant override that would break a locked rule."""
    if role not in ROLES:
        raise bad_request("bad_role", f"Unknown role {role!r}.")
    if capability not in CAPABILITIES:
        raise bad_request("bad_capability", f"Unknown capability {capability!r}.")
    if effect not in ("allow", "deny"):
        raise bad_request("bad_effect", "Effect must be allow or deny.")
    if role == "admin":
        raise forbidden("Admins keep every capability; a tenant cannot lock its own admins out.", "locked")
    if capability not in DELEGABLE or capability in NEVER.get(role, frozenset()):
        raise forbidden(
            f"{CAPABILITY_LABEL[capability]} is fixed by policy and cannot be changed per tenant.", "locked"
        )


def require(ctx: Ctx, capability: str, doing: str) -> None:
    if capability not in ctx.capabilities:
        raise forbidden(f"{ROLE_LABEL[ctx.role]} cannot {doing}.", "capability_required")
