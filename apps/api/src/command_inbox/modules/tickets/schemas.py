"""Response bodies the tickets endpoints return that `schemas/dto.py` does not define (TS returned them
as inline objects)."""

from __future__ import annotations

from command_inbox.schemas.base import CamelModel


class AssignResult(CamelModel):
    assignee: str
    reason: str


class EscalateResult(CamelModel):
    to: str


class SplitResult(CamelModel):
    children: list[str]
