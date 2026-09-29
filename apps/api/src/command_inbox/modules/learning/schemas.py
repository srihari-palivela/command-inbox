"""Learning response bodies not in the generated DTOs (the TS routes returned these inline)."""

from __future__ import annotations

from command_inbox.schemas.base import CamelModel


class Recipients(CamelModel):
    recipients: int


class CourseResult(CamelModel):
    score: int
    total: int
