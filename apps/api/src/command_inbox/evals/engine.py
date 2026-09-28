"""What an eval scores: one deployment version's System 1 categorisation and hard-stop guard, per case.

The adapter prefers the agents runtime (`command_inbox.agents.decision`: the configured decision engine,
wrapped in its resilient fallback), so an eval measures what triage does. When that package is missing or
does not load, a small deterministic keyword scorer over the deployment's own taxonomy stands in, so evals
run offline and in tests either way. Text is PII-masked before any engine sees it.

Scorers return label *logits* (log-probabilities are fine): the runner fits the temperature on the
calibration split and applies it, so the engine is always called uncalibrated (T = 1).
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from command_inbox.agents.config import DeploymentConfig
from command_inbox.domain.pii import mask_pii

log = structlog.get_logger(__name__)

CATEGORISE_Q = "Which category does this customer email belong to?"


@dataclass(frozen=True, slots=True)
class CaseInput:
    subject: str
    body: str
    from_email: str | None = None


@dataclass(slots=True)
class Scored:
    logits: dict[str, float]
    hard_stops: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    cost_minor: float = 0.0


class Scorer(Protocol):
    name: str

    async def score(self, case: CaseInput) -> Scored: ...


def _forced_category(config: DeploymentConfig, text: str, from_email: str | None) -> str | None:
    """Bucket overrides run before the decision engine (deterministic)."""
    sender = (from_email or "").lower()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    for o in config.rules.bucket_overrides:
        value = o.value.lower()
        if not all(w.lower() in text for w in getattr(o, "requires", ()) or ()):
            continue
        if (
            (o.match == "phrase" and value in text)
            or (o.match == "sender" and value == sender)
            or (o.match == "domain" and value == domain)
            or (o.match == "regex" and re.search(o.value, text, re.IGNORECASE) is not None)
        ):
            return o.category
    return None


def _keyword_stops(config: DeploymentConfig, text: str) -> list[str]:
    return [h.key for h in config.rules.hard_stops if any(k.lower() in text for k in h.keywords if k.strip())]


_WORD = re.compile(r"[a-z][a-z0-9']{2,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "your",
        "you",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "not",
        "but",
        "our",
        "can",
        "will",
        "please",
        "about",
        "into",
        "they",
        "them",
        "their",
        "there",
        "what",
        "when",
        "which",
        "who",
        "why",
        "how",
        "any",
        "all",
        "one",
        "two",
        "out",
        "its",
        "also",
        "mail",
        "email",
        "other",
        "request",
        "requests",
        "query",
        "queries",
        "unclear",
    ]
)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


class KeywordScorer:
    """Deterministic fallback: overlap between the mail and each category's name, description and examples."""

    name = "keyword"

    def __init__(self, config: DeploymentConfig) -> None:
        self.config = config
        self.cats = [
            (c.key, _words(" ".join([c.name, c.description, *c.examples])), [e.lower() for e in c.examples])
            for c in config.taxonomy.categories
        ]

    async def score(self, case: CaseInput) -> Scored:
        started = time.perf_counter()
        text = f"{case.subject}\n{case.body}".lower()
        words = _words(text)
        logits = {}
        for key, vocab, examples in self.cats:
            logits[key] = 1.0 * len(words & vocab) + 3.0 * sum(1 for e in examples if e and e in text)
        fallback = self.config.taxonomy.fallback
        logits[fallback] = max(logits.get(fallback, 0.0), 0.5)
        forced = _forced_category(self.config, text, case.from_email)
        if forced in logits:
            logits[forced] += 10.0
        return Scored(
            logits=logits,
            hard_stops=_keyword_stops(self.config, text),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class AgentsScorer:
    """The agents runtime's decision engine: Choice over the taxonomy, Boolean per hard-stop question."""

    def __init__(self, config: DeploymentConfig, decision: Any) -> None:
        self.config = config
        self.d = decision
        engine = decision.engine_from_settings()
        self.engine = decision.ResilientEngine(engine) if hasattr(decision, "ResilientEngine") else engine
        self.name = f"agents:{getattr(engine, 'name', 'engine')}"
        self.options = tuple(
            decision.Option(
                key=c.key,
                label=c.name,
                description=c.description,
                examples=tuple(c.examples),
                group=c.department,
            )
            for c in config.taxonomy.categories
        )

    async def score(self, case: CaseInput) -> Scored:
        started = time.perf_counter()
        subject, body = mask_pii(case.subject).text, mask_pii(case.body).text
        text = f"{subject}\n{body}".lower()
        state = self.d.DecisionState(
            text=f"{subject}\n{body}",
            subject=subject,
            hints={"fallback": self.config.taxonomy.fallback},
            options=self.options,
        )
        result = await self.engine.choice(
            state,
            CATEGORISE_Q,
            self.options,
            calibration=self.d.Calibration(temperature=1.0, model=self.config.models.decision_model),
        )
        logits = {k: math.log(max(p, 1e-9)) for k, p in result.distribution.items()}
        forced = _forced_category(self.config, text, case.from_email)
        if forced in logits:
            logits[forced] = max(logits.values()) + 10.0
        stops = _keyword_stops(self.config, text)
        for h in self.config.rules.hard_stops:
            if h.key in stops or not h.question:
                continue
            if await self.engine.boolean(state, h.question) >= h.threshold:
                stops.append(h.key)
        return Scored(logits=logits, hard_stops=stops, latency_ms=(time.perf_counter() - started) * 1000)


def load_scorer(config: DeploymentConfig) -> Scorer:
    try:
        from command_inbox.agents import decision
    except Exception as err:  # the agents runtime is optional here
        log.info("agents decision engine unavailable; evals use the keyword scorer", err=str(err))
        return KeywordScorer(config)
    try:
        return AgentsScorer(config, decision)
    except Exception as err:
        log.warning("agents decision engine failed to start; evals use the keyword scorer", err=str(err))
        return KeywordScorer(config)


def fit_temperature(logits: list[list[float]], labels: list[int]) -> float:
    """The agents runtime's calibration when present (one implementation for triage and evals)."""
    try:
        from command_inbox.agents.decision.calibration import fit_temperature as agents_fit

        return float(agents_fit(logits, labels))
    except (ImportError, TypeError, ValueError):
        from command_inbox.evals.metrics import fit_temperature as local_fit

        return local_fit(logits, labels)
