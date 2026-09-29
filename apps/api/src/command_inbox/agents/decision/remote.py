"""Open-weight System 1 backends: llama.cpp server and vLLM.

Options are presented as single-token letters (A, B, C, …). One forward pass with `n_predict = 1` returns
the next-token log-probabilities; only the option letters are read, so the answer is always a valid option
and there is no long-label bias from summing multi-token likelihoods. Probabilities are a
temperature-scaled softmax over those letters.
"""

from __future__ import annotations

import asyncio
import math
import random
import string
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import structlog

from command_inbox.agents.breaker import CircuitBreaker
from command_inbox.agents.decision.calibration import cumulative_set, prediction_set, softmax_dict
from command_inbox.agents.decision.types import (
    Calibration,
    ChoiceResult,
    DecisionEngineError,
    DecisionState,
    Option,
    ScoreResult,
)

log = structlog.get_logger(__name__)

LETTERS = string.ascii_uppercase + string.ascii_lowercase + string.digits
MISSING_LOGPROB_GAP = 10.0  # an option letter outside the returned top-k is this far below the weakest seen

INSTRUCTION = (
    "You label email sent to a bank's operations team. Read the email, then answer the question "
    "with the letter of exactly one option."
)


def letters_for(n: int) -> str:
    if n < 1 or n > len(LETTERS):
        raise DecisionEngineError(f"{n} options cannot be presented as single-token letters")
    return LETTERS[:n]


def render_prompt(state: DecisionState, question: str, labels: Sequence[str], details: Sequence[str]) -> str:
    """The same prompt for both backends; the letters are the only tokens read back."""
    lines = [INSTRUCTION, "", "Email:", "<<<"]
    if state.subject:
        lines.append(f"Subject: {state.subject}")
    lines += [state.text.strip(), ">>>"]
    for k, v in state.hints.items():
        if k in ("fallback", "no_match", "category_key"):
            continue
        lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")
    lines += ["", f"Question: {question}", "Options:"]
    for letter, label, detail in zip(letters_for(len(labels)), labels, details, strict=True):
        lines.append(f"{letter}. {label}" + (f" — {detail}" if detail else ""))
    lines.append("Answer with one letter.")
    lines.append("Answer:")
    return "\n".join(lines)


def letter_logprobs(candidates: Sequence[tuple[str, float]], letters: str) -> dict[str, float]:
    """Pick the option letters out of a top-k token list ("A", " A", "A." all count), log-sum-exp merged."""
    wanted = set(letters)
    found: dict[str, list[float]] = {}
    for token, lp in candidates:
        t = token.strip().rstrip(".):")
        if t in wanted and math.isfinite(lp):
            found.setdefault(t, []).append(lp)
    if not found:
        raise DecisionEngineError("no option letter among the returned tokens")
    merged = {k: max(v) + math.log(sum(math.exp(x - max(v)) for x in v)) for k, v in found.items()}
    floor = min(merged.values()) - MISSING_LOGPROB_GAP
    return {letter: merged.get(letter, floor) for letter in letters}


def parse_llamacpp(body: Mapping[str, Any]) -> list[tuple[str, float]]:
    """llama.cpp `/completion` with `n_probs`: new (`top_logprobs`) and legacy (`probs`) shapes."""
    probs = body.get("completion_probabilities") or []
    if not probs:
        raise DecisionEngineError("llama.cpp returned no completion_probabilities (is n_probs set?)")
    first = probs[0]
    if "top_logprobs" in first:
        return [(str(t.get("token", "")), float(t["logprob"])) for t in first["top_logprobs"]]
    if "probs" in first:
        return [(str(t.get("tok_str", "")), math.log(max(float(t["prob"]), 1e-12))) for t in first["probs"]]
    raise DecisionEngineError("unrecognised llama.cpp probability format")


def parse_vllm(body: Mapping[str, Any]) -> list[tuple[str, float]]:
    """vLLM OpenAI-compatible `/v1/completions` with `logprobs`: the first position's top-k map."""
    try:
        top = body["choices"][0]["logprobs"]["top_logprobs"][0]
    except (KeyError, IndexError, TypeError) as err:
        raise DecisionEngineError("vLLM returned no top_logprobs") from err
    return [(str(tok), float(lp)) for tok, lp in top.items()]


class RemoteEngine:
    """Shared HTTP plumbing: timeouts, bounded retries with jitter, a circuit breaker."""

    name = "remote"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        retries: int = 2,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.retries = retries
        self.breaker = breaker or CircuitBreaker(f"decision:{self.name}")
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 3.0)),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.breaker.is_open:
            raise DecisionEngineError(f"{self.name} circuit open")
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = await self._client.post(self.base_url + path, json=payload)
                if r.status_code >= 500 or r.status_code == 429:
                    raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                body = r.json()
                self.breaker.success()
                return body  # type: ignore[no-any-return]
            except httpx.HTTPStatusError as err:
                last = err
                if err.response.status_code < 500 and err.response.status_code != 429:
                    break  # a 4xx will not get better on retry
            except (httpx.TransportError, ValueError) as err:
                last = err
            if attempt < self.retries:
                await asyncio.sleep(min(2.0, 0.2 * 2**attempt) * (0.75 + random.random() * 0.5))  # noqa: S311
        self.breaker.failure(last or "unknown error")
        raise DecisionEngineError(f"{self.name} request failed: {last}")

    async def _letter_logprobs(self, prompt: str, letters: str, model: str) -> dict[str, float]:
        raise NotImplementedError

    async def choice(
        self,
        state: DecisionState,
        question: str,
        options: Sequence[Option],
        *,
        calibration: Calibration | None = None,
    ) -> ChoiceResult:
        started = time.perf_counter()
        cal = calibration or Calibration()
        letters = letters_for(len(options))
        prompt = render_prompt(state, question, [o.label for o in options], [o.description for o in options])
        lps = await self._letter_logprobs(prompt, letters, cal.model or self.model)
        dist = softmax_dict(
            {o.key: lps[letter] for o, letter in zip(options, letters, strict=True)}, cal.temperature
        )
        label = max(dist, key=lambda k: dist[k])
        pset = prediction_set(dist, cal.qhat) if cal.qhat is not None else cumulative_set(dist, cal.coverage)
        return ChoiceResult(
            label=label,
            distribution=dist,
            confidence=dist[label],
            prediction_set=pset,
            engine=self.name,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def score(
        self, state: DecisionState, rubric_levels: Sequence[str], *, calibration: Calibration | None = None
    ) -> ScoreResult:
        from command_inbox.agents.decision.questions import PRIORITY_Q

        opts = [Option(key=lvl, label=lvl) for lvl in rubric_levels]
        r = await self.choice(state, PRIORITY_Q, opts, calibration=calibration)
        exp = sum((i + 1) * r.distribution[lvl] for i, lvl in enumerate(rubric_levels))
        return ScoreResult(expectation=exp, distribution=r.distribution)

    async def boolean(self, state: DecisionState, question: str) -> float:
        r = await self.choice(state, question, [Option("yes", "Yes"), Option("no", "No")])
        return r.distribution["yes"]


class LlamaCppEngine(RemoteEngine):
    name = "llamacpp"

    async def _letter_logprobs(self, prompt: str, letters: str, model: str) -> dict[str, float]:
        payload = {
            "prompt": prompt,
            "n_predict": 1,
            "n_probs": max(20, 2 * len(letters)),
            "temperature": 0.0,
            "cache_prompt": True,
            # A uniform bias on every option letter keeps them in the top-k without changing their ratios.
            "logit_bias": [[letter, 8.0] for letter in letters],
        }
        body = await self._post("/completion", payload)
        return letter_logprobs(parse_llamacpp(body), letters)


class VllmEngine(RemoteEngine):
    name = "vllm"

    async def _letter_logprobs(self, prompt: str, letters: str, model: str) -> dict[str, float]:
        payload = {"model": model, "prompt": prompt, "max_tokens": 1, "temperature": 0.0, "logprobs": 20}
        body = await self._post("/v1/completions", payload)
        return letter_logprobs(parse_vllm(body), letters)
