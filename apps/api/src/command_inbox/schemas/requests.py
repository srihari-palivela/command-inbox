"""Request bodies and query parameters, ported from packages/contracts/src/api.ts (same names, same limits).

Zod's `.trim()` / `.toLowerCase()` transforms become Pydantic `BeforeValidator`s so behaviour matches.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, StringConstraints

from command_inbox.schemas.base import CamelModel
from command_inbox.schemas.enums import (
    AgentTemplate,
    ClearanceLevel,
    KnowledgeKind,
    KpiMetric,
    Lane,
    MailProvider,
    NotificationKind,
    Priority,
    RejectReason,
    RiskCell,
    TicketStatus,
)

UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
Uuid = Annotated[str, Field(pattern=UUID_RE)]


def _strip(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


def _lower(v: object) -> object:
    return v.strip().lower() if isinstance(v, str) else v


def _nonempty(v: str) -> str:
    if not v:
        raise ValueError("must not be empty")
    return v


def trimmed(max_len: int) -> type[str]:
    return Annotated[str, BeforeValidator(_strip), Field(max_length=max_len), AfterValidator(_nonempty)]  # type: ignore[return-value]


Email = Annotated[str, BeforeValidator(_lower), Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)]


# ── Session & settings ──────────────────────────────────────────────────────
class SettingsBody(CamelModel):
    prefs: dict[str, bool] | None = None
    signature: Annotated[str, Field(max_length=2000)] | None = None


# ── Tickets ─────────────────────────────────────────────────────────────────
StatusGroup = Literal["triage", "approval", "executing", "human", "customer", "resolved"]


class TicketFilters(CamelModel):
    board: str | None = None
    status: StatusGroup | None = None
    lane: Lane | None = None
    team: str | None = None
    owner: str | None = None  # 'mine' | 'ai' | 'unassigned' | <userId>
    due: Literal["risk", "open", "closed"] | None = None
    conf: Literal["low", "high"] | None = None
    pri: Priority | None = None
    bucket: str | None = None
    q: Annotated[str, Field(max_length=200)] | None = None


FilterKey = Literal["board", "status", "lane", "team", "owner", "due", "conf", "pri", "bucket", "q"]
InboxFilter = Literal["all", "auto", "draft", "manual", "late"]


class NlFilterBody(CamelModel):
    query: trimmed(300)  # type: ignore[valid-type]


class TransitionBody(CamelModel):
    to: TicketStatus


class AssignBody(CamelModel):
    user_id: Uuid | None = None


class OverrideLaneBody(CamelModel):
    lane: Lane


class CommentBody(CamelModel):
    kind: Literal["note", "public"]
    body: trimmed(5000)  # type: ignore[valid-type]


class SubtaskBody(CamelModel):
    done: bool


class WatchBody(CamelModel):
    watching: bool


class MergeBody(CamelModel):
    into_number: Annotated[str, Field(pattern=r"^QRY-\d+$")]


# ── Approval gateway ────────────────────────────────────────────────────────
class ApproveBody(CamelModel):
    opened_evidence: bool = False


class RejectBody(CamelModel):
    reason: RejectReason


class BatchApproveBody(CamelModel):
    ticket_ids: Annotated[list[Uuid], Field(min_length=1, max_length=50)]


class DraftBody(CamelModel):
    body: Annotated[str, Field(max_length=20000)]


class ReplyBody(CamelModel):
    body: trimmed(20000)  # type: ignore[valid-type]


class ActionField(CamelModel):
    label: str
    value: Annotated[str, Field(max_length=500)]


class ActionFieldsBody(CamelModel):
    fields: Annotated[list[ActionField], Field(min_length=1)]


class SaveCallBody(CamelModel):
    discard: bool = False


# ── People & insights ───────────────────────────────────────────────────────
class ClearanceBody(CamelModel):
    user_id: Uuid
    department_id: Uuid
    level: ClearanceLevel


class KpiBody(CamelModel):
    name: trimmed(80)  # type: ignore[valid-type]
    metric: KpiMetric
    viz: Literal["bars", "number"]
    scope: Literal["team", "me"]
    target: Annotated[float, Field(allow_inf_nan=False)]


class AskBody(CamelModel):
    question: trimmed(500)  # type: ignore[valid-type]


# ── Learning ────────────────────────────────────────────────────────────────
class NotificationBody(CamelModel):
    title: trimmed(160)  # type: ignore[valid-type]
    kind: NotificationKind
    body: Annotated[str, Field(max_length=2000)] | None = None
    course_id: Uuid | None = None


class MarkReadBody(CamelModel):
    ids: list[Uuid] | None = None
    all: bool | None = None


class CourseCompleteBody(CamelModel):
    answers: Annotated[list[Annotated[int, Field(ge=0)]], Field(max_length=50)]


# ── Setup ───────────────────────────────────────────────────────────────────
class BoardBody(CamelModel):
    name: trimmed(80)  # type: ignore[valid-type]
    provider: MailProvider
    mailbox: Email
    department_id: Uuid | None = None


class AgentBody(CamelModel):
    name: trimmed(60)  # type: ignore[valid-type]
    template: AgentTemplate
    model: Annotated[str, Field(min_length=3, max_length=80)]
    prompt: trimmed(8000)  # type: ignore[valid-type]
    board_ids: Annotated[list[Uuid], Field(min_length=1)]


class AgentVersionBody(CamelModel):
    prompt: trimmed(8000)  # type: ignore[valid-type]
    model: Annotated[str, Field(min_length=3, max_length=80)] | None = None


class AgentBoardsBody(CamelModel):
    board_ids: list[Uuid]


class DialBody(CamelModel):
    cell: RiskCell
    level: Annotated[int, Field(ge=0, le=2)]


class ActionTemplateBody(CamelModel):
    name: trimmed(120)  # type: ignore[valid-type]
    system: trimmed(60)  # type: ignore[valid-type]
    cell: RiskCell


class RuleToggleBody(CamelModel):
    enabled: bool


class DecideBody(CamelModel):
    approve: bool


class KnowledgeSourceBody(CamelModel):
    kind: KnowledgeKind
    name: trimmed(80) | None = None  # type: ignore[valid-type]


class OwnerBody(CamelModel):
    user_id: Uuid | None = None


# ── Intake ──────────────────────────────────────────────────────────────────
class IntakeMessageBody(CamelModel):
    mailbox: Email
    from_name: trimmed(120)  # type: ignore[valid-type]
    from_email: Email
    subject: trimmed(300)  # type: ignore[valid-type]
    body: trimmed(50000)  # type: ignore[valid-type]
    message_id: Annotated[str, Field(max_length=300)] | None = None
    in_reply_to: Annotated[str, Field(max_length=300)] | None = None


class WorkspaceProfileBody(CamelModel):
    legal_name: Annotated[str, Field(min_length=1, max_length=200)]
    support_email: Email | Literal[""]
    locale: Annotated[str, Field(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    time_zone: Annotated[str, Field(min_length=1, max_length=64)]


class WorkspaceSsoBody(CamelModel):
    provider: Literal["entra", "google"]
    directory_id: Annotated[str, Field(min_length=1, max_length=120)]
    client_id: Annotated[str, Field(min_length=1, max_length=200)]
    client_secret: Annotated[str, Field(min_length=8, max_length=500)] | None = None


class CreateMailboxBody(CamelModel):
    address: Email
    provider: Literal["microsoft", "google"]
    team_label: Annotated[str, Field(max_length=80)] = ""


class MailboxSendingBody(CamelModel):
    enabled: bool


class KnowledgeReviewBody(CamelModel):
    reason: Annotated[str, Field(max_length=500)] = ""


class KnowledgeSearchBody(CamelModel):
    query: Annotated[str, Field(min_length=2, max_length=2000)]
    department_id: Annotated[str, Field(pattern=UUID_RE)] | None = None


class ModelPolicyBody(CamelModel):
    allowed_providers: Annotated[list[Literal["anthropic", "openai"]], Field(max_length=2)]
    monthly_budget_minor: Annotated[int, Field(ge=0, le=1_000_000_000_000)] | None


class DepartmentBody(CamelModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    risk: bool = False


class QueryTypeBody(CamelModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    department_id: Uuid | None
    default_lane: Literal["draft", "manual"]
    live: bool = True


class SlaPolicyBody(CamelModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    priority: Literal["P1", "P2", "P3", "P4"] | None
    segment: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None
    escalation: bool = False
    minutes: Annotated[int, Field(ge=5, le=43_200)]


class SlaPoliciesBody(CamelModel):
    policies: Annotated[list[SlaPolicyBody], Field(min_length=1, max_length=40)]
