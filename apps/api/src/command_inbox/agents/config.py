"""A deployment version's configuration: the contract between the deployments module and the agent runtime.

A tenant runs several mailbox categorisations (deployments). Each published version is an immutable JSON
document validated by `DeploymentConfig`. The triage runner compiles `flow` into a LangGraph graph over the
node registry; the lane is always decided by deterministic policy code, never by a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from command_inbox.schemas.base import CamelModel

Lane = Literal["auto", "draft", "manual"]
Sensitivity = Literal["standard", "restricted", "secret"]
Short = Annotated[str, Field(min_length=1, max_length=120)]
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]


class Category(CamelModel):
    """One label System 1 can choose. The description and examples are what the decision engine reads."""

    key: Key
    name: Short
    description: str = Field(default="", max_length=600)
    examples: list[Annotated[str, Field(max_length=400)]] = Field(default_factory=list, max_length=12)
    department: Short
    default_lane: Lane = "draft"
    action_template: str | None = Field(default=None, max_length=60)
    sensitivity: Sensitivity = "standard"


class Taxonomy(CamelModel):
    categories: list[Category] = Field(min_length=2, max_length=64)
    # Mail that fits no category is routed here (a person looks at it).
    fallback: Key = "other"

    @model_validator(mode="after")
    def _unique(self) -> Taxonomy:
        keys = [c.key for c in self.categories]
        if len(set(keys)) != len(keys):
            raise ValueError("category keys must be unique")
        return self


class HardStop(CamelModel):
    """A signal that sends mail straight to a person (manual lane). Keyword match OR a System 1 boolean."""

    key: Key
    label: Short
    keywords: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=60)
    question: str | None = Field(default=None, max_length=300)  # yes/no question for the decision engine
    threshold: float = Field(default=0.5, ge=0.05, le=0.95)


class BucketOverride(CamelModel):
    """Force a category when a sender, domain, phrase or pattern matches; applied before the decision engine.

    `regex` is matched case-insensitively against subject + body; `requires` lists words that must all be
    present as well (carries over the setup's deterministic bucketing rules exactly).
    """

    match: Literal["sender", "domain", "phrase", "regex"]
    value: Short
    category: Key
    requires: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _valid_regex(self) -> BucketOverride:
        if self.match == "regex":
            try:
                re.compile(self.value)
            except re.error as err:
                raise ValueError(f"invalid bucket override pattern: {err}") from err
        return self


class PriorityRuleSpec(CamelModel):
    key: Key
    description: str = Field(max_length=300)
    target: Literal["p1", "p2", "p3", "p4"]
    hard: bool = False
    keywords: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=40)
    # A weighted (non-hard) rule can be switched off; hard rules always fire.
    enabled: bool = True


class Rules(CamelModel):
    hard_stops: list[HardStop] = Field(default_factory=list, max_length=32)
    bucket_overrides: list[BucketOverride] = Field(default_factory=list, max_length=200)
    priority: list[PriorityRuleSpec] = Field(default_factory=list, max_length=40)


NodeType = Literal[
    "mask_pii",  # required, first
    "sender_trust",  # DKIM/SPF/DMARC verdict
    "hard_stop_guard",  # required
    "categorise",  # System 1 Choice over the taxonomy
    "adjudicate",  # System 2, only when System 1 is unsure; constrained to the conformal set
    "priority",  # System 1 Score + rules
    "multi_intent",  # System 1 boolean
    "extract_fields",  # System 2 structured extraction for the action template
    "lane_policy",  # required, deterministic
    "draft_reply",  # System 2 grounded draft
    "brief",  # System 2 summary for the manual lane
    "route",  # clearance and load
    "approval_gate",  # required, last
]
REQUIRED_ORDER: tuple[NodeType, ...] = ("mask_pii", "hard_stop_guard", "lane_policy", "approval_gate")


SYSTEM2_NODES: dict[str, str] = {
    # node type → the agent role that runs it
    "adjudicate": "adjudicator",
    "extract_fields": "extractor",
    "draft_reply": "drafter",
    "brief": "summariser",
}
ProviderKey = Literal["", "anthropic", "openai"]  # "" = the deployment's (else the platform's) default


class NodeAgent(CamelModel):
    """The model settings of one System 2 node: this is what an "agent" is inside a deployment.

    Empty fields inherit: provider from `models.system2Provider` (else the platform default), model from
    `models.system2Model` (else the provider's default), prompt from the workspace's agent catalogue.
    """

    name: str = Field(default="", max_length=80)
    provider: ProviderKey = ""
    model: str = Field(default="", max_length=120)
    prompt: str = Field(default="", max_length=12_000)
    max_tokens: int | None = Field(default=None, ge=256, le=32_000)
    effort: Literal["low", "medium", "high"] | None = None
    cost_per_1k_minor: int | None = Field(default=None, ge=0, le=100_000)
    # Drafting only: the house style and sign-off, appended to the prompt.
    style_guide: str = Field(default="", max_length=4000)
    signature: str = Field(default="", max_length=600)


class FlowNode(CamelModel):
    type: NodeType
    params: dict[str, Any] = Field(default_factory=dict)
    agent: NodeAgent | None = None

    @model_validator(mode="after")
    def _agent_on_model_nodes(self) -> FlowNode:
        if self.agent is not None and self.type not in SYSTEM2_NODES:
            raise ValueError(
                f"the {self.type} node does not call a language model; it takes no agent settings"
            )
        return self


class Flow(CamelModel):
    """Nodes run in order; conditional branches (adjudicate, draft vs brief) are decided by node outputs."""

    template: Literal["standard", "high_risk", "extraction_heavy"] = "standard"
    nodes: list[FlowNode] = Field(min_length=4, max_length=20)

    @model_validator(mode="after")
    def _safety_nodes(self) -> Flow:
        types = [n.type for n in self.nodes]
        if len(set(types)) != len(types):
            raise ValueError("each node type may appear once")
        positions = []
        for required in REQUIRED_ORDER:
            if required not in types:
                raise ValueError(f"the {required} node is required")
            positions.append(types.index(required))
        if positions != sorted(positions) or types[0] != "mask_pii" or types[-1] != "approval_gate":
            raise ValueError(
                "mask_pii must be first, approval_gate last, with hard_stop_guard before lane_policy"
            )
        return self


class Models(CamelModel):
    decision_model: str = Field(default="", max_length=120)  # empty = the platform default
    temperature: float = Field(default=1.0, gt=0.05, le=10.0)  # fitted by calibration
    conformal_qhat: float | None = Field(default=None, ge=0.0, le=1.0)  # fitted by calibration
    system2_model: str = Field(default="", max_length=120)
    system2_provider: ProviderKey = ""
    max_cost_minor_per_mail: int = Field(default=50, ge=0, le=10_000)


class Thresholds(CamelModel):
    auto_min_confidence: float = Field(default=0.92, ge=0.5, le=1.0)
    draft_min_confidence: float = Field(default=0.7, ge=0.3, le=1.0)
    escalate_below: float = Field(default=0.7, ge=0.0, le=1.0)  # System 1 below this → adjudicator
    conformal_coverage: float = Field(default=0.95, ge=0.8, le=0.995)

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if self.draft_min_confidence > self.auto_min_confidence:
            raise ValueError("draftMinConfidence must not exceed autoMinConfidence")
        return self


class Gates(CamelModel):
    hard_stop_recall: float = Field(default=1.0, ge=0.9, le=1.0)
    macro_f1: float = Field(default=0.85, ge=0.0, le=1.0)
    accuracy: float = Field(default=0.9, ge=0.0, le=1.0)
    ece_max: float = Field(default=0.05, ge=0.0, le=0.5)
    selective_accuracy: float = Field(default=0.98, ge=0.0, le=1.0)
    min_coverage: float = Field(default=0.7, ge=0.0, le=1.0)
    auto_lane_on_hard_stop_max: int = Field(default=0, ge=0, le=0)  # locked: never


class DeploymentConfig(CamelModel):
    taxonomy: Taxonomy
    rules: Rules = Field(default_factory=Rules)
    flow: Flow
    models: Models = Field(default_factory=Models)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    gates: Gates = Field(default_factory=Gates)

    @model_validator(mode="after")
    def _references(self) -> DeploymentConfig:
        keys = {c.key for c in self.taxonomy.categories}
        if self.taxonomy.fallback not in keys:
            raise ValueError(f"fallback category {self.taxonomy.fallback!r} is not in the taxonomy")
        for o in self.rules.bucket_overrides:
            if o.category not in keys:
                raise ValueError(f"bucket override points at unknown category {o.category!r}")
        return self

    def config_hash(self) -> str:
        """Stable hash of the canonical config; eval runs and publishing are bound to it."""
        body = self.model_dump(mode="json", by_alias=True)
        # Settings added later are left out while unset, so the hash of an older config does not change.
        if not body["models"]["system2Provider"]:
            del body["models"]["system2Provider"]
        for node in body["flow"]["nodes"]:
            if node["agent"] is None:
                del node["agent"]
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def standard_flow() -> Flow:
    return Flow(
        template="standard",
        nodes=[
            FlowNode(type=t)
            for t in (
                "mask_pii",
                "sender_trust",
                "hard_stop_guard",
                "categorise",
                "adjudicate",
                "priority",
                "multi_intent",
                "extract_fields",
                "lane_policy",
                "draft_reply",
                "brief",
                "route",
                "approval_gate",
            )
        ],
    )
