"""Deterministic System 1: keyword and lexicon signals, no model.

Used in tests, offline development and as the fallback when the model server is unavailable. For the
query types the previous service knew it reproduces that service's confidences exactly (0.95 / 0.88 / 0.80,
−0.12 on a tie, 0.55 for multi-team mail, 0.35 when nothing matches), so lanes match on the seeded mail.
Categories it has no signature for are matched on their example phrases.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from command_inbox.agents.decision.calibration import prediction_set
from command_inbox.agents.decision.questions import INFORMATIONAL_Q, MULTI_INTENT_Q
from command_inbox.agents.decision.types import Calibration, ChoiceResult, DecisionState, Option, ScoreResult
from command_inbox.agents.lexicon import MULTI_JOIN_RE, SIGNATURE_BY_NAME, SIGNATURES, Hit, phrase_hits

NO_MATCH_CONFIDENCE = 0.35


@dataclass(slots=True)
class Analysis:
    hits: list[Hit]
    others: list[Hit]  # hits owned by a different team than the top one
    multi_intent: bool
    confidence: float


def _body(state: DecisionState) -> str:
    return " " + state.text.lower() + " "


def _rank(body: str, options: Sequence[Option]) -> list[Hit]:
    by_label = {o.label: o for o in options}
    hits: list[Hit] = []
    order = 0
    for sig in SIGNATURES:  # known query types first, in signature order
        opt = by_label.get(sig.query_type)
        if opt is None:
            continue
        h = phrase_hits(body, opt.key, sig.any, sig.strong)
        h.signature, h.group, h.order = sig, opt.group, order
        order += 1
        hits.append(h)
    for opt in options:
        if opt.label in SIGNATURE_BY_NAME:
            continue
        phrases = tuple(e.lower().strip() for e in opt.examples if 0 < len(e.strip()) <= 60)
        h = phrase_hits(body, opt.key, phrases, None)
        h.group, h.order = opt.group, order
        order += 1
        hits.append(h)
    ranked = [h for h in hits if h.score > 0]
    ranked.sort(key=lambda h: (-h.score, h.order))
    return ranked


def analyse(state: DecisionState, options: Sequence[Option]) -> Analysis:
    body = _body(state)
    hits = _rank(body, options)
    if not hits:
        return Analysis([], [], False, NO_MATCH_CONFIDENCE)
    top = hits[0]
    others = [h for h in hits[1:] if h.group != top.group and h.score >= 1]
    multi = bool(others) and MULTI_JOIN_RE.search(body) is not None
    conf = 0.95 if top.score >= 3 else 0.88 if top.score == 2 else 0.8
    if multi:
        conf = 0.55
    elif len(hits) > 1 and hits[1].score == top.score:
        conf -= 0.12
    return Analysis(hits, others, multi, round(conf, 2))


def _intent(h: Hit, options: Sequence[Option]) -> str:
    if h.signature is not None:
        return h.signature.intent
    return next((o.label.lower() for o in options if o.key == h.name), h.name)


class HeuristicEngine:
    name = "heuristic"

    async def choice(
        self,
        state: DecisionState,
        question: str,
        options: Sequence[Option],
        *,
        calibration: Calibration | None = None,
    ) -> ChoiceResult:
        started = time.perf_counter()
        if not options:
            raise ValueError("choice needs at least one option")
        a = analyse(state, options)
        keys = [o.key for o in options]
        if not a.hits:
            fallback = state.hints.get("fallback")
            label = fallback if fallback in keys else keys[-1]
            dist = _spread(keys, label, NO_MATCH_CONFIDENCE, {})
            evidence: tuple[str, ...] = ()
            intents: tuple[str, ...] = ()
        else:
            top = a.hits[0]
            label = top.name
            weights = {h.name: float(h.score) for h in a.hits[1:]}
            dist = _spread(keys, label, a.confidence, weights)
            evidence = tuple(top.phrases[:3])
            intents = (_intent(top, options), *(_intent(o, options) for o in a.others))
        cal = calibration or Calibration()
        if cal.qhat is not None:
            pset = prediction_set(dist, cal.qhat)
        else:
            # Keyword scores are not probabilities: the set is the labels tied with the top score.
            top_score = a.hits[0].score if a.hits else 0
            pset = [label, *(h.name for h in a.hits[1:] if h.score == top_score)]
        return ChoiceResult(
            label=label,
            distribution=dist,
            confidence=dist[label],
            prediction_set=pset,
            engine=self.name,
            latency_ms=(time.perf_counter() - started) * 1000,
            evidence=evidence,
            intents=intents,
        )

    async def score(
        self, state: DecisionState, rubric_levels: Sequence[str], *, calibration: Calibration | None = None
    ) -> ScoreResult:
        # No lexical signal for urgency beyond the deterministic priority rules: an uninformative answer.
        n = len(rubric_levels)
        dist = {lvl: 1.0 / n for lvl in rubric_levels}
        return ScoreResult(expectation=(n + 1) / 2, distribution=dist)

    async def boolean(self, state: DecisionState, question: str) -> float:
        if question == MULTI_INTENT_Q:
            return 0.9 if analyse(state, state.options).multi_intent else 0.05
        if question == INFORMATIONAL_Q:
            if state.hints.get("no_match") == "1":
                return 0.9
            sig = SIGNATURE_BY_NAME.get(state.hints.get("category", ""))
            if sig is None:
                return 0.3
            return 0.9 if sig.informational else 0.1
        # Hard-stop questions: the keyword screen already covers them deterministically.
        return 0.0

    async def aclose(self) -> None:
        return None


def _spread(keys: list[str], label: str, top: float, weights: dict[str, float]) -> dict[str, float]:
    """Top label gets `top`; the rest is shared 80/20 between other matches (by score) and everything else."""
    rest = [k for k in keys if k != label]
    if not rest:
        return {label: 1.0}
    remaining = 1.0 - top
    dist = {label: top}
    matched = {k: w for k, w in weights.items() if k in rest}
    unmatched = [k for k in rest if k not in matched]
    share_matched = remaining * (0.8 if unmatched else 1.0) if matched else 0.0
    total_w = sum(matched.values()) or 1.0
    for k, w in matched.items():
        dist[k] = share_matched * w / total_w
    for k in unmatched:
        dist[k] = (remaining - share_matched) / len(unmatched)
    # In a two-label taxonomy a no-match leaves the fallback below the other label; the fixed confidence
    # is kept on purpose (it is what sends the mail to a person).
    return dist
