"""System 1: letter-token parsing, llama.cpp / vLLM backends over a mock transport, fallback and breaker."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest

from command_inbox.agents import status
from command_inbox.agents.breaker import CircuitBreaker
from command_inbox.agents.decision import (
    Calibration,
    DecisionEngineError,
    DecisionState,
    HeuristicEngine,
    LlamaCppEngine,
    Option,
    ResilientEngine,
    VllmEngine,
    engine_from_settings,
)
from command_inbox.agents.decision.remote import letter_logprobs, parse_llamacpp, parse_vllm, render_prompt

OPTIONS = [
    Option("statement", "Statement re-issue", "Copies of account statements"),
    Option("fees", "Balance & charge queries", "Why a fee or charge was debited"),
    Option("other", "Unclassified"),
]
STATE = DecisionState(text="Why was ₹590 debited as an annual card fee?")


def llamacpp_body(tops: list[tuple[str, float]]) -> dict[str, Any]:
    return {
        "content": "B",
        "completion_probabilities": [
            {
                "id": 1,
                "token": tops[0][0],
                "logprob": tops[0][1],
                "top_logprobs": [{"id": i, "token": t, "logprob": lp} for i, (t, lp) in enumerate(tops)],
            }
        ],
    }


def test_letter_parsing_merges_variants_and_floors_missing_letters():
    lps = letter_logprobs(
        [(" B", math.log(0.5)), ("B", math.log(0.2)), ("A.", math.log(0.1)), ("the", -0.1)], "ABC"
    )
    assert math.isclose(lps["B"], math.log(0.7), rel_tol=1e-9)
    assert math.isclose(lps["A"], math.log(0.1), rel_tol=1e-9)
    assert lps["C"] < lps["A"] - 5  # not in the top-k: far below the weakest seen
    with pytest.raises(DecisionEngineError):
        letter_logprobs([("Hello", -0.1)], "AB")


def test_parse_llamacpp_new_and_legacy_formats():
    new = parse_llamacpp(llamacpp_body([("B", -0.1), ("A", -2.5)]))
    assert new == [("B", -0.1), ("A", -2.5)]
    legacy = parse_llamacpp(
        {"completion_probabilities": [{"content": "B", "probs": [{"tok_str": "B", "prob": 0.9}]}]}
    )
    assert legacy[0][0] == "B" and math.isclose(legacy[0][1], math.log(0.9))
    with pytest.raises(DecisionEngineError):
        parse_llamacpp({"content": "B"})


def test_parse_vllm():
    body = {"choices": [{"logprobs": {"top_logprobs": [{"A": -1.0, " B": -0.2}]}}]}
    assert dict(parse_vllm(body)) == {"A": -1.0, " B": -0.2}


def test_prompt_presents_options_as_single_letters():
    p = render_prompt(STATE, "Which?", [o.label for o in OPTIONS], [o.description for o in OPTIONS])
    assert "A. Statement re-issue — Copies of account statements" in p
    assert "C. Unclassified" in p and p.rstrip().endswith("Answer:")


async def test_llamacpp_choice_reads_only_the_option_letters():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200, json=llamacpp_body([("B", math.log(0.6)), (" A", math.log(0.2)), ("Yes", math.log(0.15))])
        )

    engine = LlamaCppEngine(
        "http://llama:8090", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    r = await engine.choice(STATE, "Which query type?", OPTIONS)
    assert seen["path"] == "/completion"
    assert seen["payload"]["n_predict"] == 1 and seen["payload"]["n_probs"] >= 3
    assert [b[0] for b in seen["payload"]["logit_bias"]] == ["A", "B", "C"]
    assert r.label == "fees" and math.isclose(sum(r.distribution.values()), 1.0)
    assert math.isclose(r.distribution["fees"] / r.distribution["statement"], 3.0, rel_tol=1e-6)  # 0.6 / 0.2
    assert r.confidence == r.distribution["fees"] and r.prediction_set[0] == "fees"

    # Temperature > 1 flattens the distribution without changing the order.
    hot = await engine.choice(STATE, "Which query type?", OPTIONS, calibration=Calibration(temperature=3.0))
    assert hot.label == "fees" and hot.confidence < r.confidence
    # With a conformal threshold the set is every label with 1 − p ≤ q̂.
    tight = await engine.choice(STATE, "q", OPTIONS, calibration=Calibration(qhat=0.3))
    assert tight.prediction_set == ["fees"]
    wide = await engine.choice(STATE, "q", OPTIONS, calibration=Calibration(qhat=1.0))
    assert set(wide.prediction_set) == {"fees", "statement", "other"}


async def test_vllm_choice_and_boolean():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert (
            request.url.path == "/v1/completions" and payload["logprobs"] == 20 and payload["max_tokens"] == 1
        )
        return httpx.Response(
            200, json={"choices": [{"logprobs": {"top_logprobs": [{"A": -0.1, "B": -2.4}]}}]}
        )

    engine = VllmEngine(
        "http://vllm:8000", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    p = await engine.boolean(STATE, "Is this a complaint?")
    assert 0.85 < p < 0.95
    s = await engine.score(STATE, ["P1", "P2"])
    assert 1.0 < s.expectation < 1.2


async def test_backend_failure_falls_back_to_heuristic_and_trips_the_breaker():
    status.reset()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "loading model"})

    breaker = CircuitBreaker("decision:test", threshold=2, open_seconds=60)
    primary = LlamaCppEngine(
        "http://llama:8090",
        "m",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retries=1,
        breaker=breaker,
    )
    primary.breaker = breaker
    engine = ResilientEngine(primary)
    r = await engine.choice(STATE, "q", OPTIONS)
    assert engine.degraded and r.degraded and r.engine == "heuristic"
    assert calls["n"] == 2  # one retry
    await engine.boolean(STATE, "q")
    assert breaker.is_open and "decision:test" in status.degraded_sources()
    before = calls["n"]
    await engine.choice(STATE, "q", OPTIONS)
    assert calls["n"] == before  # open circuit: no request at all
    status.reset()


async def test_heuristic_reproduces_the_previous_confidences():
    e = HeuristicEngine()
    opts = [
        Option("stop", "Stop payment instruction", group="Trade"),
        Option("fees", "Balance & charge queries", group="Retail"),
        Option("other", "Unclassified", group="Unassigned"),
    ]
    stop = DecisionState(text="Stop payment\nPlease place a stop on cheque number 123456 immediately.")
    r = await e.choice(stop, "q", opts)
    assert (
        r.label == "stop"
        and r.confidence == 0.95
        and r.evidence == ("stop payment", "stop on cheque", "place a stop")
    )
    none = await e.choice(DecisionState(text="hello there", hints={"fallback": "other"}), "q", opts)
    assert none.label == "other" and none.confidence == 0.35
    assert engine_from_settings().name == "heuristic"  # tests run with DECISION_ENGINE=heuristic
