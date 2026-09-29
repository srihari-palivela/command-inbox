"""System 1 contract: three primitives that always answer with a valid option and a probability."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class DecisionEngineError(RuntimeError):
    """The engine could not answer (network, bad response, circuit open). Callers fall back."""


@dataclass(frozen=True, slots=True)
class Option:
    """One answer System 1 can choose. `group` is the owning team (used to spot multi-team mail)."""

    key: str
    label: str
    description: str = ""
    examples: tuple[str, ...] = ()
    group: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionState:
    """What the engine reads: PII-masked text plus short hints (e.g. the chosen category)."""

    text: str
    subject: str = ""
    hints: Mapping[str, str] = field(default_factory=dict)
    options: tuple[Option, ...] = ()  # the taxonomy, for questions that depend on it


@dataclass(frozen=True, slots=True)
class Calibration:
    """Per-deployment calibration: softmax temperature and the split-conformal threshold."""

    temperature: float = 1.0
    qhat: float | None = None
    coverage: float = 0.95
    model: str = ""  # the deployment's decision model; empty = the platform default


@dataclass(frozen=True, slots=True)
class ChoiceResult:
    label: str  # an option key, always one of the options
    distribution: dict[str, float]  # option key -> probability, sums to 1
    confidence: float  # probability of `label`
    prediction_set: list[str]  # conformal set, most likely first; never empty
    engine: str = ""
    latency_ms: float = 0.0
    degraded: bool = False  # answered by the fallback engine
    evidence: tuple[str, ...] = ()  # phrases that drove the choice (deterministic engine only)
    intents: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoreResult:
    expectation: float  # 1-based: 1.0 = first rubric level
    distribution: dict[str, float]
    degraded: bool = False


@runtime_checkable
class DecisionEngine(Protocol):
    name: str

    async def choice(
        self,
        state: DecisionState,
        question: str,
        options: Sequence[Option],
        *,
        calibration: Calibration | None = None,
    ) -> ChoiceResult: ...

    async def score(
        self, state: DecisionState, rubric_levels: Sequence[str], *, calibration: Calibration | None = None
    ) -> ScoreResult: ...

    async def boolean(self, state: DecisionState, question: str) -> float: ...

    async def aclose(self) -> None: ...
