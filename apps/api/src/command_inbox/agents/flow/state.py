"""The state a flow run carries between nodes, and the per-run dependencies nodes read.

State holds only masked text and decisions (each node records its output under `outputs[node]` for the
explanation shown to staff); the PII vault and database rows live in `RunDeps`, never in state or traces.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig

from command_inbox.agents.config import Category, DeploymentConfig
from command_inbox.agents.decision import ResilientEngine
from command_inbox.agents.providers import AgentSpec, ProviderRouter, Usage


def _merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class SpanRec(TypedDict):
    offset_ms: int
    agent: str
    model: str
    action: str
    output: str
    latency_ms: int
    tokens: int | None
    cost_minor: int | None
    status: str  # ok | flag | stop


class FlowState(TypedDict, total=False):
    thread: Any  # ThreadInput (masked)
    text: str  # masked subject + bodies, what System 1 reads
    sender_verified: bool
    guard: dict[str, Any]
    choice: dict[str, Any]
    category: str  # taxonomy key
    confidence: float
    escalate: bool
    escalated_unresolved: bool
    informational: bool
    multi_intent: bool
    intents: list[str]
    template_code: str | None
    extraction: Any  # ExtractResult | None
    priority: dict[str, Any]
    lane: str
    lane_note: str
    want_draft: bool
    draft: Any  # DraftResult | None
    grounding: list[Any]  # the DocRows the draft was written from, in citation-number order
    brief: Any  # BriefResult | None
    chain: str | None
    route: dict[str, Any]
    degraded: Annotated[bool, operator.or_]
    spans: Annotated[list[SpanRec], operator.add]
    outputs: Annotated[dict[str, Any], _merge]
    visited: Annotated[list[str], operator.add]


@dataclass(frozen=True, slots=True)
class CategoryMeta:
    category: Category
    query_type_id: str | None
    department_id: str | None
    department_name: str | None
    owned: bool
    bucket: str  # what the ticket shows as its bucket / subcategory
    is_fallback: bool = False


@dataclass(frozen=True, slots=True)
class TemplateRow:
    id: str
    code: str
    name: str
    reversible: bool
    money_moves: bool
    approval: str


@dataclass(frozen=True, slots=True)
class DocRow:
    id: str
    title: str
    section: str
    body: str
    owner: str
    department_id: str | None
    verified_at: datetime | None
    chunk_id: str | None = None  # set when the row is a retrieved passage of an uploaded document


@dataclass(slots=True)
class RunDeps:
    config: DeploymentConfig
    deployment: str  # label for metrics and traces
    engine: ResilientEngine
    providers: ProviderRouter
    ticket_id: str
    org_id: str
    subject: str  # raw; masked by the first node
    messages: list[tuple[str, str]]  # (sender name, raw body), oldest first
    sender_email: str
    customer_name: str  # how the draft addresses the customer
    customer_facts: str | None  # "Name · CIF" when matched
    segment: str
    ticket_priority: str
    received_at: datetime
    now: datetime
    prior_contacts: int
    prior_same_topic: int
    categories: dict[str, CategoryMeta]
    templates: dict[str, TemplateRow]
    dial: dict[str, int]
    docs: list[DocRow]
    agents: dict[str, AgentSpec]
    # Hybrid retrieval over approved knowledge: (query, department id) → passages. None: use `docs` as is.
    retrieve: Callable[[str, str | None], Awaitable[list[DocRow]]] | None = None
    sender_verified: bool = True
    force_lane: str | None = None
    vault: dict[str, str] = field(default_factory=dict)
    started: float = field(default_factory=time.perf_counter)

    def agent(self, role: str) -> AgentSpec:
        return self.agents.get(role) or AgentSpec(
            name=role, model=self.config.models.system2_model, prompt="You are a careful banking assistant."
        )

    def meta(self, key: str | None) -> CategoryMeta | None:
        return self.categories.get(key or "")

    def span(
        self,
        agent: str,
        action: str,
        output: str,
        status: str,
        *,
        usage: Usage | None = None,
        model: str | None = None,
    ) -> SpanRec:
        latency = usage.latency_ms if usage else 5
        elapsed = int((time.perf_counter() - self.started) * 1000)
        return SpanRec(
            offset_ms=max(0, elapsed - latency),
            agent=agent,
            model=model or (usage.model if usage else "—"),
            action=action,
            output=output,
            latency_ms=latency,
            tokens=usage.tokens if usage else None,
            cost_minor=usage.cost_minor if usage else None,
            status=status,
        )


def deps_of(config: RunnableConfig) -> RunDeps:
    deps = (config.get("configurable") or {}).get("deps")
    if not isinstance(deps, RunDeps):
        raise RuntimeError("flow run without RunDeps in config['configurable']['deps']")
    return deps
