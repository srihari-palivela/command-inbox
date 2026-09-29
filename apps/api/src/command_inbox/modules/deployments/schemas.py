"""Request bodies for deployments (mirrors packages/contracts/src/api.ts)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from command_inbox.schemas.base import CamelModel
from command_inbox.schemas.requests import Uuid, trimmed

Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]


class CreateDeploymentBody(CamelModel):
    key: Key
    name: trimmed(120)  # type: ignore[valid-type]
    description: Annotated[str, Field(max_length=600)] = ""
    copy_from: Uuid | None = None


class NewDraftBody(CamelModel):
    notes: Annotated[str, Field(max_length=2000)] | None = None


class DraftConfigBody(CamelModel):
    config: dict[str, Any]
    notes: Annotated[str, Field(max_length=2000)] | None = None


class BindMailboxesBody(CamelModel):
    mailbox_ids: Annotated[list[Uuid], Field(max_length=200)]


class PromoteBody(CamelModel):
    to: Literal["shadow", "canary", "published"]
    canary_percent: Annotated[int, Field(ge=1, le=99)] | None = None
    acknowledge_single_admin: bool = False


class RollbackBody(CamelModel):
    version_id: Uuid | None = None
