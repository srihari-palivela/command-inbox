"""Request bodies for members, invitations and permissions (mirrors packages/contracts/src/api.ts)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from command_inbox.core.context import Role
from command_inbox.schemas.base import CamelModel
from command_inbox.schemas.enums import PolicyEffect
from command_inbox.schemas.requests import Email


class MemberRoleBody(CamelModel):
    role: Role


class InvitationBody(CamelModel):
    email: Email
    role: Role
    expires_in_days: Annotated[int, Field(ge=1, le=30)] = 7


class PermissionOverride(CamelModel):
    role: Role  # admin rows are refused by validate_override (403 locked), not by the schema
    capability: Annotated[str, Field(max_length=60)]
    effect: PolicyEffect | None  # null removes the override


class PermissionOverridesBody(CamelModel):
    overrides: Annotated[list[PermissionOverride], Field(min_length=1, max_length=100)]
