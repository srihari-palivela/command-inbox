"""Request bodies for evals (mirrors packages/contracts/src/api.ts)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from command_inbox.schemas.base import CamelModel
from command_inbox.schemas.enums import EvalSplit
from command_inbox.schemas.requests import Email, Uuid, trimmed

Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]


class EvalDatasetBody(CamelModel):
    deployment_id: Uuid
    name: trimmed(120)  # type: ignore[valid-type]
    description: Annotated[str, Field(max_length=600)] = ""


class EvalDatasetPatchBody(CamelModel):
    name: trimmed(120) | None = None  # type: ignore[valid-type]
    description: Annotated[str, Field(max_length=600)] | None = None


class EvalCaseInput(CamelModel):
    subject: Annotated[str, Field(max_length=300)]
    body: Annotated[str, Field(max_length=20000)]
    from_email: Email | None = None


class EvalCaseExpected(CamelModel):
    category: Key
    hard_stop: bool = False


class EvalCaseBody(CamelModel):
    input: EvalCaseInput
    expected: EvalCaseExpected
    split: EvalSplit = "test"
    tags: Annotated[list[Annotated[str, Field(max_length=40)]], Field(default_factory=list, max_length=20)]


class EvalCasesBody(CamelModel):
    cases: Annotated[list[EvalCaseBody], Field(min_length=1, max_length=500)]


class StartEvalRunBody(CamelModel):
    deployment_version_id: Uuid
    dataset_id: Uuid
    provider: Literal["anthropic", "openai"] | None = None
