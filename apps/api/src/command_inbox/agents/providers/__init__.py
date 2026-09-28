"""System 2 providers: Claude (structured outputs) and the deterministic fallback.

`ProviderRouter` is created per triage run: it enforces the deployment's per-mail budget, trips the shared
circuit breaker after repeated failures, and answers from the heuristic provider whenever Claude cannot —
marking the run degraded so the ticket goes to a person.

The module-level `copilot_answer` / `nl_filter` are the seam the copilot and query box call
(`modules/copilot/system2.py`); they return None whenever no model is configured or it fails.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog

from command_inbox.agents.breaker import CircuitBreaker
from command_inbox.agents.providers.claude import ClaudeProvider
from command_inbox.agents.providers.heuristic import HeuristicProvider
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

heuristic = HeuristicProvider()
breaker = CircuitBreaker("llm")
_primary: Provider | None = None


def provider_from_settings() -> Provider:
    global _primary
    if _primary is None:
        chosen: Provider = ClaudeProvider() if settings.use_claude else heuristic
        _primary = chosen
        return chosen
    return _primary


def set_provider_for_tests(p: Provider | None) -> None:
    global _primary
    _primary = p
    breaker.reset()


class ProviderRouter:
    """Budget + breaker + fallback for one run. `degraded` becomes True on any fallback from Claude."""

    def __init__(self, primary: Provider | None = None, budget_minor: int | None = None) -> None:
        self.primary = primary or provider_from_settings()
        self.budget_minor = budget_minor
        self.spent_minor = 0
        self.degraded = False
        self.reasons: list[str] = []

    @property
    def name(self) -> str:
        return self.primary.name

    async def call(
        self, stage: str, agent: AgentSpec, fn: Callable[[Provider], Awaitable[Staged[T]]]
    ) -> Staged[T]:
        if self.primary.name == "heuristic":
            return await fn(self.primary)
        if breaker.is_open:
            return await self._fallback(stage, fn, "circuit open")
        if self.budget_minor is not None and self.spent_minor >= self.budget_minor:
            return await self._fallback(stage, fn, "per-mail model budget spent")
        try:
            staged = await fn(self.primary)
        except ProviderError as err:
            breaker.failure(err)
            return await self._fallback(stage, fn, str(err))
        breaker.success()
        cost = staged.usage.cost_minor or 0
        self.spent_minor += cost
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
    if p.name == "heuristic" or breaker.is_open:
        return None
    try:
        out = await p.copilot_answer(question, facts, settings.copilot_model)
    except ProviderError as err:
        breaker.failure(err)
        return None
    breaker.success()
    return out


async def nl_filter(query: str, departments: list[dict[str, str]]) -> dict[str, Any] | None:
    p = provider_from_settings()
    if not isinstance(p, ClaudeProvider) or breaker.is_open:
        return None
    try:
        out = await p.nl_filter(query, departments, settings.copilot_model)
    except ProviderError as err:
        breaker.failure(err)
        return None
    breaker.success()
    return out


__all__ = [
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
    "Provider",
    "ProviderError",
    "ProviderRouter",
    "Staged",
    "TemplateSpec",
    "ThreadInput",
    "ThreadMessage",
    "Usage",
    "copilot_answer",
    "nl_filter",
    "provider_from_settings",
    "set_provider_for_tests",
]
