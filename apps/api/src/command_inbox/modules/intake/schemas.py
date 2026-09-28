"""Intake request and response bodies not (yet) in `packages/contracts`."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from command_inbox.schemas.base import CamelModel
from command_inbox.schemas.requests import IntakeMessageBody


class IntakeWebhookBody(IntakeMessageBody):
    """The contract's `IntakeMessageBody` plus the receiving MTA's `Authentication-Results` header value(s),
    passed through by the relay so the DKIM/SPF/DMARC verdict can be recorded and used by the lane policy."""

    authentication_results: Annotated[str, Field(max_length=8000)] | None = None


class IngestResult(CamelModel):
    ticket_id: str
    number: int
    created: bool
    duplicate: bool


class SimulateMailBody(CamelModel):
    index: Annotated[int, Field(ge=0)] | None = None


class AuthorizeUrlResult(CamelModel):
    authorize_url: str
