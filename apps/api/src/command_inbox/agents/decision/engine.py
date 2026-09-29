"""Engine selection, fallback and instrumentation.

`engine_from_settings()` returns the process-wide backend (one HTTP connection pool). Each triage run wraps
it in a `ResilientEngine`, which traces every primitive, records `decision_latency`, and on any backend
failure answers from the deterministic engine and marks the run degraded (the lane becomes manual).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import structlog
from opentelemetry import trace

from command_inbox.agents.decision.heuristic import HeuristicEngine
from command_inbox.agents.decision.remote import LlamaCppEngine, VllmEngine
from command_inbox.agents.decision.types import (
    Calibration,
    ChoiceResult,
    DecisionEngine,
    DecisionEngineError,
    DecisionState,
    Option,
    ScoreResult,
)
from command_inbox.config import settings
from command_inbox.core.telemetry import decision_latency

log = structlog.get_logger(__name__)
tracer = trace.get_tracer("command_inbox.agents.decision")

_engine: DecisionEngine | None = None


def engine_from_settings() -> DecisionEngine:
    """`auto` means llama.cpp when a URL is configured, otherwise the deterministic engine."""
    global _engine
    if _engine is not None:
        return _engine
    mode = settings.decision_engine
    if mode == "auto":
        mode = "llamacpp" if settings.decision_engine_url else "heuristic"
    if mode in ("llamacpp", "vllm") and not settings.decision_engine_url:
        log.error("decision engine URL missing; using the deterministic engine", engine=mode)
        mode = "heuristic"
    if mode == "llamacpp":
        _engine = LlamaCppEngine(settings.decision_engine_url or "", settings.decision_model)
    elif mode == "vllm":
        _engine = VllmEngine(settings.decision_engine_url or "", settings.decision_model)
    else:
        _engine = HeuristicEngine()
    return _engine


def set_engine_for_tests(engine: DecisionEngine | None) -> None:
    global _engine
    _engine = engine


class ResilientEngine:
    """Per-run wrapper: tracing + metrics + deterministic fallback. `degraded` is set on any fallback."""

    def __init__(self, primary: DecisionEngine, fallback: DecisionEngine | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or HeuristicEngine()
        self.name = primary.name
        self.degraded = False
        self.failures: list[str] = []

    async def _run(self, primitive: str, attrs: dict[str, Any], call: Any, fallback_call: Any) -> Any:
        started = time.perf_counter()
        with tracer.start_as_current_span(
            f"decision.{primitive}",
            attributes={
                "gen_ai.operation.name": "decision",
                "gen_ai.system": self.primary.name,
                "decision.primitive": primitive,
                **attrs,
            },
        ) as span:
            try:
                result = await call()
            except DecisionEngineError as err:
                if self.primary is self.fallback:
                    raise
                self.degraded = True
                self.failures.append(str(err)[:200])
                span.set_attribute("decision.degraded", True)
                span.record_exception(err)
                result = await fallback_call()
            finally:
                decision_latency.labels(self.primary.name, primitive).observe(time.perf_counter() - started)
            return result

    async def choice(
        self,
        state: DecisionState,
        question: str,
        options: Sequence[Option],
        *,
        calibration: Calibration | None = None,
    ) -> ChoiceResult:
        result: ChoiceResult = await self._run(
            "choice",
            {
                "decision.options": len(options),
                "gen_ai.request.model": (calibration and calibration.model) or "",
            },
            lambda: self.primary.choice(state, question, options, calibration=calibration),
            lambda: self.fallback.choice(state, question, options, calibration=calibration),
        )
        if self.degraded and result.engine == self.fallback.name:
            return replace(result, degraded=True)
        return result

    async def score(
        self, state: DecisionState, rubric_levels: Sequence[str], *, calibration: Calibration | None = None
    ) -> ScoreResult:
        result: ScoreResult = await self._run(
            "score",
            {"decision.levels": len(rubric_levels)},
            lambda: self.primary.score(state, rubric_levels, calibration=calibration),
            lambda: self.fallback.score(state, rubric_levels, calibration=calibration),
        )
        return result

    async def boolean(self, state: DecisionState, question: str) -> float:
        result: float = await self._run(
            "boolean",
            {},
            lambda: self.primary.boolean(state, question),
            lambda: self.fallback.boolean(state, question),
        )
        return result

    async def aclose(self) -> None:
        return None
