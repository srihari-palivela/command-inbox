"""Per-request identity and the actors that appear in the audit log."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["staff", "lead", "admin"]
ROLES: tuple[Role, ...] = ("staff", "lead", "admin")
ROLE_LABEL: dict[str, str] = {"staff": "Staff", "lead": "Team lead", "admin": "Admin"}


@dataclass(frozen=True, slots=True)
class Actor:
    kind: Literal["user", "ai", "system"]
    id: str | None
    name: str
    initials: str


AI_ACTOR = Actor("ai", None, "Command Inbox", "AI")
SYSTEM_ACTOR = Actor("system", None, "System", "SY")


@dataclass(frozen=True, slots=True)
class UserRef:
    id: str
    name: str
    initials: str
    email: str


@dataclass(frozen=True, slots=True)
class Ctx:
    """Who is acting, in which tenant, with which single role. Resolved from the session on every request."""

    org_id: str
    session_id: str
    request_id: str
    role: Role
    user: UserRef
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities


def actor_of(ctx: Ctx) -> Actor:
    return Actor("user", ctx.user.id, ctx.user.name, ctx.user.initials)
