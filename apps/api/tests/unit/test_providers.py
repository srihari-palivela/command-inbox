"""System 2: the Claude provider's request shape through the real SDK (mock transport), fallback routing."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from command_inbox.agents.providers import (
    AgentSpec,
    ClaudeProvider,
    CustomerContext,
    ProviderRouter,
    TemplateSpec,
    ThreadInput,
    ThreadMessage,
    breaker,
    set_provider_for_tests,
)

AGENT = AgentSpec(
    "Field Extractor", "rules + claude-sonnet-5", "You pull fields out of email.", cost_per_1k_minor=31
)
THREAD = ThreadInput(
    subject="Interest certificate",
    messages=(ThreadMessage("Kavya", "Send the certificate for FY 2025-26. Call me on 9876543210."),),
    customer=CustomerContext("Retail", 0, 0),
)
TEMPLATE = TemplateSpec(
    "ACT-CRT-004", "Interest certificate", ("Account number", "Financial year", "Delivery")
)


def message(payload: dict[str, Any], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 100, "cache_read_input_tokens": 0},
    }


def client_with(handler: Any) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.fixture(autouse=True)
def _reset():
    set_provider_for_tests(None)
    yield
    set_provider_for_tests(None)


async def test_extract_is_one_structured_call_with_a_cached_system_prompt_and_masked_text():
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json=message(
                {
                    "fields": [
                        {
                            "label": "Financial year",
                            "value": "2025-26",
                            "source": "email body",
                            "inferred": False,
                        },
                        {
                            "label": "Delivery",
                            "value": "Registered email",
                            "source": "inferred",
                            "inferred": True,
                        },
                        {"label": "Colour", "value": "blue", "source": "email body", "inferred": False},
                    ],
                    "amount_inr": None,
                }
            ),
        )

    p = ClaudeProvider(client_with(handler))
    staged = await p.extract_fields(THREAD, TEMPLATE, AGENT)
    body = seen["body"]
    assert body["model"] == "claude-sonnet-5"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "9876543210" not in json.dumps(body["messages"]) and "[PHONE_1]" in json.dumps(body["messages"])
    labels = [f.label for f in staged.result.fields]
    assert labels == ["Financial year", "Delivery"]  # unknown labels dropped
    assert staged.result.complete is False  # account number missing
    assert staged.usage.tokens == 1000 and staged.usage.cost_minor == 31


async def test_adjudicate_is_constrained_to_the_candidates():
    def handler(request: httpx2.Request) -> httpx2.Response:
        schema = json.loads(request.content)["output_config"]["format"]["schema"]
        assert set(schema["properties"]["label"]["enum"]) == {
            "Statement re-issue",
            "Certificate requests",
            "unsure",
        }
        return httpx2.Response(200, json=message({"label": "unsure", "reason": "Could be either."}))

    staged = await ClaudeProvider(client_with(handler)).adjudicate(
        "text", ["Statement re-issue", "Certificate requests"], AGENT
    )
    assert staged.result.label is None


async def test_refusal_and_api_errors_fall_back_to_heuristic_and_mark_degraded():
    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message({}, stop="refusal"))

    router = ProviderRouter(primary=ClaudeProvider(client_with(refuse)))
    staged = await router.call("extract_fields", AGENT, lambda p: p.extract_fields(THREAD, TEMPLATE, AGENT))
    assert router.degraded and staged.usage.model == "rules"

    def boom(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}})

    breaker.reset()
    router = ProviderRouter(primary=ClaudeProvider(client_with(boom)))
    for _ in range(3):
        await router.call("brief", AGENT, lambda p: p.brief(THREAD, "Prior contacts: 0", AGENT))
    assert router.degraded and breaker.is_open
    breaker.reset()


async def test_budget_spent_uses_the_fallback():
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return httpx2.Response(200, json=message({"summary": "s", "context": [], "suggestions": []}))

    router = ProviderRouter(primary=ClaudeProvider(client_with(handler)), budget_minor=20)
    await router.call("brief", AGENT, lambda p: p.brief(THREAD, "", AGENT))  # costs 31
    await router.call("brief", AGENT, lambda p: p.brief(THREAD, "", AGENT))
    assert calls["n"] == 1 and router.degraded and "budget" in router.reasons[0]
