"""Claude as System 2 (Anthropic Messages API, `messages.parse` with a Pydantic output format).

The agent's own prompt is the system prompt and is cache-marked (it is identical across mails, so repeated
calls read it from the prompt cache); the mail goes in the user turn. Every string that leaves the process is
PII-masked here again, whatever the caller did.
"""

from __future__ import annotations

import re
import time
from typing import Any, ClassVar

import anthropic
from pydantic import ValidationError

from command_inbox.agents.providers.structured import M, StructuredProvider, cost_of, mask
from command_inbox.agents.providers.types import AgentSpec, ProviderError, Usage
from command_inbox.agents.tracing import generation_span
from command_inbox.config import settings


class ClaudeProvider(StructuredProvider):
    name: ClassVar[str] = "claude"
    key: ClassVar[str] = "anthropic"
    MODEL_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"claude-[a-z0-9-]+")

    def __init__(self, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._client = client

    def default_model(self) -> str:
        return settings.copilot_model

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            try:
                self._client = anthropic.AsyncAnthropic(
                    api_key=settings.anthropic_api_key, max_retries=2, timeout=60.0
                )
            except anthropic.AnthropicError as err:
                raise ProviderError(f"model client unavailable: {err}") from err
        return self._client

    async def _call(
        self,
        stage: str,
        agent: AgentSpec,
        system: str,
        user: str,
        schema: type[M],
        *,
        max_tokens: int = 4000,
        model: str | None = None,
    ) -> tuple[M, Usage]:
        model_id = model or self.model_for(agent.model)
        user = mask(user)
        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
        }
        if agent.effort:
            kwargs["output_config"] = {"effort": agent.effort}
        started = time.perf_counter()
        with generation_span(stage, model=model_id, agent=agent.name, input_text=user) as gen:
            try:
                response = await self.client.messages.parse(**kwargs)
            except anthropic.APIError as err:
                raise ProviderError(f"{stage}: {type(err).__name__}: {getattr(err, 'message', err)}") from err
            except (ValidationError, ValueError) as err:
                # The SDK validates the output while parsing; a refusal or truncation lands here.
                raise ProviderError(f"{stage}: unparseable output ({type(err).__name__})") from err
            if response.stop_reason == "refusal":
                raise ProviderError(f"{stage}: the model declined the request")
            parsed = response.parsed_output
            if parsed is None:
                raise ProviderError(f"{stage}: no parseable output (stop: {response.stop_reason})")
            u = response.usage
            input_tokens = (
                u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
            )
            tokens = input_tokens + u.output_tokens
            cost = cost_of(agent, model_id, tokens)
            gen.finish(
                output=parsed.model_dump_json(),
                input_tokens=input_tokens,
                output_tokens=u.output_tokens,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cost_minor=cost,
            )
        usage = Usage(model_id, tokens, cost, int((time.perf_counter() - started) * 1000))
        return parsed, usage
