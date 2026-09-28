"""Setup response bodies not in the generated DTOs (the TS routes returned these inline)."""

from __future__ import annotations

from pydantic import Field

from command_inbox.schemas import dto
from command_inbox.schemas.base import CamelModel


# The generated DTOs camel-case `volume24h` / `cost_per1k_minor` as `volume24H` / `costPer1KMinor`
# (pydantic's to_camel capitalises after a digit); the contract says `volume24h` / `costPer1kMinor`.
# Explicit aliases win over the generator. Drop these once scripts/gen_dto.py emits the aliases.
class BoardOut(dto.BoardDTO):
    volume24h: int | float = Field(alias="volume24h")


class AgentOut(dto.AgentDTO):
    cost_per1k_minor: int | float = Field(alias="costPer1kMinor")


class AgentsOverviewOut(dto.AgentsOverviewDTO):
    agents: list[AgentOut]  # type: ignore[assignment]


class BoardCreated(CamelModel):
    board: BoardOut
    authorize_url: str | None


class AgentVersionCreated(CamelModel):
    version: int


class SourceConnected(CamelModel):
    id: str
    name: str


class OwnerChanged(CamelModel):
    owner: str
