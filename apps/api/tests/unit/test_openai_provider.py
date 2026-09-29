"""System 2 on OpenAI: the Responses request shape through the real SDK (mock transport), per-node routing,
the workspace's provider allow-list and the monthly budget."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import openai
import pytest

from command_inbox.agents.config import DeploymentConfig, NodeAgent
from command_inbox.agents.providers import (
    AgentSpec,
    ClaudeProvider,
    OpenAIProvider,
    ProviderRouter,
    TemplateSpec,
    ThreadInput,
    ThreadMessage,
    provider_for,
    set_provider_for_tests,
)
from command_inbox.agents.specs import agent_specs, policy_problems
from command_inbox.config import settings
from command_inbox.starter import starter_config

AGENT = AgentSpec(
    "Reply Drafter", "gpt-5-mini", "You draft replies.", cost_per_1k_minor=10, provider="openai"
)
THREAD = ThreadInput(
    subject="Interest certificate",
    messages=(ThreadMessage("Kavya", "Send the certificate for FY 2025-26. Call me on 9876543210."),),
)
TEMPLATE = TemplateSpec("ACT-CRT-004", "Interest certificate", ("Account number", "Financial year"))


def response(payload: dict[str, Any] | None, *, refusal: bool = False, status: str = "completed") -> dict:
    content = (
        [{"type": "refusal", "refusal": "I can't help with that."}]
        if refusal
        else [{"type": "output_text", "text": json.dumps(payload), "annotations": []}]
    )
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "gpt-5-mini",
        "status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [
            {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": content}
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 900,
            "input_tokens_details": {"cached_tokens": 512},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 1000,
        },
    }


def client_with(handler: Any) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.fixture(autouse=True)
def _reset():
    set_provider_for_tests(None)
    yield
    set_provider_for_tests(None)


async def test_extract_is_one_strict_structured_call_not_stored_and_masked():
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json=response(
                {
                    "fields": [
                        {
                            "label": "Financial year",
                            "value": "2025-26",
                            "source": "email body",
                            "inferred": False,
                        }
                    ],
                    "amount_inr": None,
                }
            ),
        )

    staged = await OpenAIProvider(client_with(handler)).extract_fields(THREAD, TEMPLATE, AGENT)
    body = seen["body"]
    assert seen["path"].endswith("/responses")
    assert body["model"] == "gpt-5-mini" and body["store"] is False
    assert body["instructions"] == "You draft replies."
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True
    assert "9876543210" not in json.dumps(body["input"]) and "[PHONE_1]" in json.dumps(body["input"])
    assert [f.label for f in staged.result.fields] == ["Financial year"]
    assert staged.usage.tokens == 1000 and staged.usage.cost_minor == 10


async def test_a_claude_model_name_falls_back_to_the_openai_default():
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["model"] = json.loads(request.content)["model"]
        return httpx2.Response(200, json=response({"summary": "s", "context": [], "suggestions": []}))

    agent = AgentSpec("Briefer", "rules + claude-sonnet-5", "Brief.", provider="openai")
    await OpenAIProvider(client_with(handler)).brief(THREAD, "", agent)
    assert seen["model"] == settings.openai_model


@pytest.mark.parametrize("kind", ["refusal", "incomplete", "server_error"])
async def test_refusals_truncation_and_errors_fall_back_to_people(kind: str):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if kind == "server_error":
            return httpx2.Response(500, json={"error": {"message": "boom", "type": "server_error"}})
        if kind == "refusal":
            return httpx2.Response(200, json=response(None, refusal=True))
        return httpx2.Response(
            200, json=response({"summary": "s", "context": [], "suggestions": []}, status="incomplete")
        )

    router = ProviderRouter(primary=OpenAIProvider(client_with(handler)))
    staged = await router.call("brief", AGENT, lambda p: p.brief(THREAD, "", AGENT))
    assert router.degraded and staged.usage.model == "rules"


async def test_the_router_picks_each_nodes_provider_and_enforces_the_workspace_policy(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "a-key")
    monkeypatch.setattr(settings, "openai_api_key", "o-key")
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    router = ProviderRouter(allowed=["anthropic", "openai"])
    assert isinstance(router.resolve(AGENT)[0], OpenAIProvider)
    assert isinstance(router.resolve(AgentSpec("x", "", "", provider=""))[0], ClaudeProvider)

    only_anthropic = ProviderRouter(allowed=["anthropic"])
    p, why = only_anthropic.resolve(AGENT)
    assert p is None and "model policy" in why
    staged = await only_anthropic.call("brief", AGENT, lambda p: p.brief(THREAD, "", AGENT))
    assert only_anthropic.degraded and staged.usage.model == "rules"

    monkeypatch.setattr(settings, "openai_api_key", None)
    p, why = ProviderRouter(allowed=["anthropic", "openai"]).resolve(AGENT)
    assert p is None and "not configured" in why
    assert provider_for("anthropic") is provider_for("anthropic")  # one shared client


async def test_the_monthly_budget_stops_model_calls():
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return httpx2.Response(200, json=response({"summary": "s", "context": [], "suggestions": []}))

    router = ProviderRouter(primary=OpenAIProvider(client_with(handler)), monthly_remaining_minor=5)
    await router.call("brief", AGENT, lambda p: p.brief(THREAD, "", AGENT))  # costs 10
    await router.call("brief", AGENT, lambda p: p.brief(THREAD, "", AGENT))
    assert calls["n"] == 1 and router.budget_hit == "month" and "monthly" in router.reasons[0]
    assert router.spent_by_provider == {"openai": 10}

    exhausted = ProviderRouter(primary=OpenAIProvider(client_with(handler)), monthly_remaining_minor=0)
    await exhausted.call("brief", AGENT, lambda p: p.brief(THREAD, "", AGENT))
    assert calls["n"] == 1 and exhausted.degraded


def test_node_agent_settings_win_over_the_catalogue_and_style_is_appended():
    raw = starter_config().model_dump(mode="json", by_alias=True)
    for node in raw["flow"]["nodes"]:
        if node["type"] == "draft_reply":
            node["agent"] = NodeAgent(
                provider="openai",
                model="gpt-5-mini",
                style_guide="Use British spelling.",
                signature="Customer Care, Apex Bank",
            ).model_dump(mode="json", by_alias=True)
    config = DeploymentConfig.model_validate(raw)
    catalogue = {"drafter": AgentSpec("Old drafter", "claude-sonnet-5", "Catalogue prompt.", 7)}
    specs = agent_specs(config, catalogue)
    d = specs["drafter"]
    assert (d.provider, d.model, d.cost_per_1k_minor) == ("openai", "gpt-5-mini", 7)
    assert d.prompt.startswith("Catalogue prompt.") and "British spelling" in d.prompt
    assert d.prompt.endswith("Customer Care, Apex Bank")
    assert specs["summariser"].prompt  # a built-in default where nothing is configured
    assert policy_problems(config, ["anthropic"]) == [
        "draft_reply would call OpenAI, which the workspace's model policy does not allow."
    ]
    assert policy_problems(config, ["anthropic", "openai"]) == []


def test_agent_settings_only_on_model_nodes_and_unset_settings_keep_the_hash():
    raw = starter_config().model_dump(mode="json", by_alias=True)
    before = DeploymentConfig.model_validate(raw).config_hash()
    raw["flow"]["nodes"][0]["agent"] = {"provider": "openai"}
    with pytest.raises(ValueError, match="does not call a language model"):
        DeploymentConfig.model_validate(raw)
    raw["flow"]["nodes"][0]["agent"] = None
    assert DeploymentConfig.model_validate(raw).config_hash() == before
