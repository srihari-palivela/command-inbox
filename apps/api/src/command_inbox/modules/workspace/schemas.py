"""Response bodies the generated DTOs do not cover (the TS routes returned these inline)."""

from __future__ import annotations

from pydantic import Field

from command_inbox.schemas import dto
from command_inbox.schemas.base import CamelModel


class MessageOut(CamelModel):
    """A one-line confirmation the web app shows as a toast."""

    message: str


class MailboxOut(dto.MailboxDTO):
    """`volume24h` as the contract spells it (the generated DTO emits `volume24H`)."""

    volume24h: int | float = Field(alias="volume24h")


class AdminOut(dto.AdminDTO):
    mailboxes: list[MailboxOut]  # type: ignore[assignment]
