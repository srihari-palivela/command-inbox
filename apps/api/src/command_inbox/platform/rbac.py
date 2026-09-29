"""Platform roles. An operator holds exactly one; none of them can read a tenant's mail or tickets.

- platform_owner: everything, including managing operators.
- operator: create and provision tenants, run lifecycle actions, invite a tenant's first admin.
- support: read-only view of tenants and the platform audit log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from command_inbox.core.errors import forbidden

OperatorRole = Literal["platform_owner", "operator", "support"]

CAPABILITIES: dict[str, frozenset[str]] = {
    "platform_owner": frozenset(
        {
            "tenants.view",
            "tenants.create",
            "tenants.lifecycle",
            "tenants.invite",
            "audit.view",
            "operators.manage",
        }
    ),
    "operator": frozenset(
        {"tenants.view", "tenants.create", "tenants.lifecycle", "tenants.invite", "audit.view"}
    ),
    "support": frozenset({"tenants.view", "audit.view"}),
}


@dataclass(frozen=True, slots=True)
class OperatorCtx:
    """The signed-in operator, resolved from the platform session cookie on every request."""

    id: str
    email: str
    name: str
    role: OperatorRole
    session_id: str
    request_id: str
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities


def require_platform(op: OperatorCtx, capability: str, doing: str) -> None:
    if not op.can(capability):
        raise forbidden(f"Your platform role cannot {doing}.", "platform_forbidden")
