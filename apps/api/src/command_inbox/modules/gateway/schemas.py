"""Response bodies for the gateway routes that `packages/contracts` does not (yet) define as DTOs."""

from __future__ import annotations

from typing import Literal

from command_inbox.schemas.base import CamelModel

ApproveOutcome = Literal["awaiting_checker", "scheduled", "sending", "taken"]


class ApproveResult(CamelModel):
    outcome: ApproveOutcome


class BatchApproveItem(CamelModel):
    ticket_id: str
    ok: bool
    outcome: ApproveOutcome | None = None
    error: str | None = None


class BatchApproveResult(CamelModel):
    results: list[BatchApproveItem]


class ReplyScheduled(CamelModel):
    reply_id: str
    send_after: str
