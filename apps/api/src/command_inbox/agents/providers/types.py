"""System 2 contract: generation (extraction, grounded drafts, briefs), adjudication and the copilot.

Every input is PII-masked before it reaches a provider. A provider never decides the lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Coverage = Literal["full", "partial", "none"]
Sentiment = Literal["neutral", "upset", "escalating", "vulnerable"]


class ProviderError(RuntimeError):
    """The provider could not answer (API error, refusal, unparseable output, budget). Callers fall back."""


class BudgetExceeded(ProviderError):
    """The deployment's per-mail model budget is spent."""


@dataclass(frozen=True, slots=True)
class ThreadMessage:
    sender: str
    body: str  # masked


@dataclass(frozen=True, slots=True)
class CustomerContext:
    segment: str = "Retail"
    prior_contacts: int = 0
    prior_same_topic: int = 0


@dataclass(frozen=True, slots=True)
class ThreadInput:
    subject: str  # masked
    messages: tuple[ThreadMessage, ...]
    customer: CustomerContext = field(default_factory=CustomerContext)

    def text(self) -> str:
        """Subject and bodies, as the classifiers read them."""
        return self.subject + "\n" + "\n".join(m.body for m in self.messages)

    def rendered(self) -> str:
        """The thread as a model reads it."""
        parts = [f"Subject: {self.subject}"]
        parts += [f"--- Message {i + 1} from {m.sender}\n{m.body}" for i, m in enumerate(self.messages)]
        c = self.customer
        return (
            "\n\n".join(parts) + f"\n\nCustomer segment: {c.segment}. Prior contacts: {c.prior_contacts} "
            f"({c.prior_same_topic} on the same topic)."
        )


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Which configured agent runs a stage: its display name, model and system prompt."""

    name: str
    model: str
    prompt: str
    cost_per_1k_minor: int = 0
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    code: str
    name: str
    fields: tuple[str, ...]


@dataclass(slots=True)
class ExtractedField:
    label: str
    value: str
    source: str
    inferred: bool

    def as_json(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value, "source": self.source, "inferred": self.inferred}


@dataclass(slots=True)
class ExtractResult:
    fields: list[ExtractedField]
    complete: bool
    amount_inr: float | None


@dataclass(frozen=True, slots=True)
class GroundingDoc:
    n: int
    title: str
    section: str
    body: str


@dataclass(slots=True)
class DraftResult:
    body: str
    citations: list[int]
    flagged: list[str]
    coverage: Coverage
    gap_question: str | None


@dataclass(slots=True)
class BriefResult:
    summary: str
    context: list[dict[str, str]]
    suggestions: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class Adjudication:
    label: str | None  # one of the candidates, or None when the model is unsure (a person decides)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Usage:
    model: str
    tokens: int | None
    cost_minor: int | None
    latency_ms: int


@dataclass(frozen=True, slots=True)
class Staged[T]:
    result: T
    usage: Usage


@runtime_checkable
class Provider(Protocol):
    @property
    def name(self) -> str: ...  # "claude" | "heuristic"

    async def extract_fields(
        self, thread: ThreadInput, template: TemplateSpec, agent: AgentSpec
    ) -> Staged[ExtractResult]: ...

    async def draft_reply(
        self, thread: ThreadInput, docs: list[GroundingDoc], agent: AgentSpec, customer_name: str
    ) -> Staged[DraftResult]: ...

    async def brief(self, thread: ThreadInput, facts: str, agent: AgentSpec) -> Staged[BriefResult]: ...

    async def adjudicate(
        self, text: str, candidate_labels: list[str], agent: AgentSpec
    ) -> Staged[Adjudication]: ...

    async def copilot_answer(self, question: str, facts: str, model: str) -> dict[str, Any] | None: ...


# Field specs per action template (what the extractor must fill).
TEMPLATE_FIELDS: dict[str, tuple[str, ...]] = {
    "ACT-STP-014": (
        "Account number",
        "Cheque number",
        "Amount",
        "Instrument date",
        "Reason code",
        "Requested by",
    ),
    "ACT-STM-002": ("Account number", "Period from", "Period to", "Format", "Delivery", "Charge"),
    "ACT-CRT-004": ("Account number", "Financial year", "Delivery"),
    "ACT-LTR-011": ("Account number", "Purpose", "Delivery"),
    "ACT-CHQ-001": ("Account number", "Leaves", "Collection branch"),
    "ACT-KYC-021": ("Account number", "New mobile number", "OTP verification"),
    "ACT-PAY-025": ("Account number", "Amount", "Beneficiary", "Transfer date"),
    "ACT-CRD-008": ("Card number", "Hold type", "Location"),
    "ACT-FEE-017": ("Card number", "Fee", "Reason"),
    "ACT-LON-007": ("Loan account", "Quote date"),
    "ACT-SI-009": ("Account number", "Payee", "Frequency"),
}
