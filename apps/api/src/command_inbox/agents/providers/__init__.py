"""System 2 providers: Claude and OpenAI (structured outputs) and the deterministic fallback.

`ProviderRouter` is created per triage run. For each stage it picks the provider the node's agent names
(else the platform default), refuses one the workspace's model policy does not allow or this installation
has no key for, enforces the deployment's per-mail budget and the workspace's monthly budget, trips a
per-provider circuit breaker after repeated failures, and answers from the heuristic provider whenever the
model cannot, marking the run degraded so the ticket goes to a person. A budget is never exceeded silently:
the reason is recorded on the run.

The module-level `copilot_answer` / `nl_filter` are the seam the copilot and query box call
(`modules/copilot/system2.py`); they return None whenever no model is configured or it fails.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog

from command_inbox.agents.breaker import CircuitBreaker
from command_inbox.agents.providers.claude import ClaudeProvider
from command_inbox.agents.providers.heuristic import HeuristicProvider
from command_inbox.agents.providers.openai_provider import OpenAIProvider
from command_inbox.agents.providers.structured import StructuredProvider
from command_inbox.agents.providers.types import (
    TEMPLATE_FIELDS,
    Adjudication,
    AgentSpec,
    BriefResult,
    BudgetExceeded,
    CustomerContext,
    DraftResult,
    ExtractedField,
    ExtractResult,
    GroundingDoc,
    Provider,
    ProviderError,
    Staged,
    TemplateSpec,
    ThreadInput,
    ThreadMessage,
    Usage,
)
from command_inbox.config import settings
from command_inbox.core.telemetry import model_cost

log = structlog.get_logger(__name__)
T = TypeVar("T")

PROVIDER_KEYS = ("anthropic", "openai")
heuristic = HeuristicProvider()
breaker = CircuitBreaker("llm")  # Anthropic (the historical name the status pill reports)
breakers: dict[str, CircuitBreaker] = {"claude": breaker, "openai": CircuitBreaker("llm-openai")}
_instances: dict[str, Provider] = {}
_override: Provider | None = None


def provider_for(key: str) -> Provider:
    """The shared client for a provider key ("anthropic" | "openai" | "heuristic")."""
    if _override is not None:
        return _override
    if key not in PROVIDER_KEYS:
        return heuristic
    if key not in _instances:
        _instances[key] = ClaudeProvider() if key == "anthropic" else OpenAIProvider()
    return _instances[key]


def available(key: str) -> bool:
    """Whether this installation can call the provider (a key is set, or it is the explicit default)."""
    return key in settings.configured_providers or key == settings.default_provider


def provider_from_settings() -> Provider:
    return provider_for(settings.default_provider)


def set_provider_for_tests(p: Provider | None) -> None:
    global _override
    _override = p
    _instances.clear()
    for b in breakers.values():
        b.reset()


def _breaker(p: Provider) -> CircuitBreaker:
    return breakers.get(p.name) or breaker


class ProviderRouter:
    """Provider choice, policy, budgets, breaker and fallback for one run.

    `primary` pins every stage to one provider (tests, the test bench). `allowed` is the workspace's
    provider allow-list (None: any configured one). `monthly_remaining_minor` is what is left of the
    workspace's monthly model budget (None: no cap).
    """

    def __init__(
        self,
        primary: Provider | None = None,
        budget_minor: int | None = None,
        *,
        allowed: list[str] | None = None,
        monthly_remaining_minor: int | None = None,
    ) -> None:
        self.pinned = primary
        self.primary = primary or provider_from_settings()
        self.budget_minor = budget_minor
        self.allowed = allowed
        self.monthly_remaining_minor = monthly_remaining_minor
        self.spent_minor = 0
        self.spent_by_provider: dict[str, int] = defaultdict(int)
        self.calls_by_provider: dict[str, int] = defaultdict(int)
        self.degraded = False
        self.reasons: list[str] = []
        self.budget_hit: str | None = None  # "mail" | "month" once a cap stopped a call

    @property
    def name(self) -> str:
        return self.primary.name

    def resolve(self, agent: AgentSpec) -> tuple[Provider | None, str]:
        """The provider for this agent, or None and why it may not be used."""
        if self.pinned is not None:
            return self.pinned, ""
        key = agent.provider or settings.default_provider
        if key == "heuristic":
            return heuristic, ""
        if key not in PROVIDER_KEYS:
            return None, f"unknown provider {key!r}"
        if self.allowed is not None and key not in self.allowed:
            return None, f"provider {key} is not allowed by the workspace's model policy"
        if not available(key):
            return None, f"provider {key} is not configured on this installation"
        return provider_for(key), ""

    async def call(
        self, stage: str, agent: AgentSpec, fn: Callable[[Provider], Awaitable[Staged[T]]]
    ) -> Staged[T]:
        p, why = self.resolve(agent)
        if p is None:
            return await self._fallback(stage, fn, why)
        if p.name == "heuristic":
            return await fn(p)
        b = _breaker(p)
        if b.is_open:
            return await self._fallback(stage, fn, "circuit open")
        if self.budget_minor is not None and self.spent_minor >= self.budget_minor:
            self.budget_hit = self.budget_hit or "mail"
            return await self._fallback(stage, fn, "per-mail model budget spent")
        if self.monthly_remaining_minor is not None and self.spent_minor >= self.monthly_remaining_minor:
            self.budget_hit = "month"
            return await self._fallback(stage, fn, "monthly model budget reached")
        try:
            staged = await fn(p)
        except ProviderError as err:
            b.failure(err)
            return await self._fallback(stage, fn, str(err))
        b.success()
        cost = staged.usage.cost_minor or 0
        self.spent_minor += cost
        key = getattr(p, "key", p.name)
        self.spent_by_provider[key] += cost
        self.calls_by_provider[key] += 1
        if cost:
            model_cost.labels(agent.name, staged.usage.model).inc(cost)
        return staged

    async def _fallback(
        self, stage: str, fn: Callable[[Provider], Awaitable[Staged[T]]], why: str
    ) -> Staged[T]:
        self.degraded = True
        self.reasons.append(f"{stage}: {why}"[:200])
        log.warning("system 2 degraded to the heuristic provider", stage=stage, reason=why[:200])
        return await fn(heuristic)


async def copilot_answer(question: str, facts: str) -> dict[str, Any] | None:
    """Rephrase deterministic facts (inputs already masked by the caller). None = use the heuristic answer."""
    p = provider_from_settings()
    if p.name == "heuristic" or _breaker(p).is_open:
        return None
    model = p.default_model() if isinstance(p, StructuredProvider) else settings.copilot_model
    try:
        out = await p.copilot_answer(question, facts, model)
    except ProviderError as err:
        _breaker(p).failure(err)
        return None
    _breaker(p).success()
    return out


async def nl_filter(query: str, departments: list[dict[str, str]]) -> dict[str, Any] | None:
    p = provider_from_settings()
    if not isinstance(p, StructuredProvider) or _breaker(p).is_open:
        return None
    try:
        out = await p.nl_filter(query, departments, p.default_model())
    except ProviderError as err:
        _breaker(p).failure(err)
        return None
    _breaker(p).success()
    return out


__all__ = [
    "PROVIDER_KEYS",
    "TEMPLATE_FIELDS",
    "Adjudication",
    "AgentSpec",
    "BriefResult",
    "BudgetExceeded",
    "ClaudeProvider",
    "CustomerContext",
    "DraftResult",
    "ExtractResult",
    "ExtractedField",
    "GroundingDoc",
    "HeuristicProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "ProviderRouter",
    "Staged",
    "StructuredProvider",
    "TemplateSpec",
    "ThreadInput",
    "ThreadMessage",
    "Usage",
    "available",
    "copilot_answer",
    "nl_filter",
    "provider_for",
    "provider_from_settings",
    "set_provider_for_tests",
]
