"""OpenAI as System 2 (Responses API, `responses.parse` with a Pydantic text format: strict structured output).

Same stages, prompts and schemas as Claude (`structured.py`); only the call differs. Requests are sent with
`store=False` so nothing is retained for later retrieval on OpenAI's side (zero data retention is still a
contractual matter), and with the agent name as the prompt-cache key so the identical system prompt is
reused across mails. `OPENAI_BASE_URL` points at a regional or proxy endpoint when the bank requires one.
"""

from __future__ import annotations

import re
import time
from typing import Any, ClassVar

import openai
from pydantic import ValidationError

from command_inbox.agents.providers.structured import M, StructuredProvider, cost_of, mask
from command_inbox.agents.providers.types import AgentSpec, ProviderError, Usage
from command_inbox.agents.tracing import generation_span
from command_inbox.config import settings


class OpenAIProvider(StructuredProvider):
    name: ClassVar[str] = "openai"
    key: ClassVar[str] = "openai"
    MODEL_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\b(?:gpt-[a-z0-9.-]+|o\d[a-z0-9.-]*)")

    def __init__(self, client: openai.AsyncOpenAI | None = None) -> None:
        self._client = client

    def default_model(self) -> str:
        return settings.openai_model

    @property
    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            try:
                self._client = openai.AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_base_url or None,
                    max_retries=2,
                    timeout=60.0,
                )
            except openai.OpenAIError as err:
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
            "instructions": system,
            "input": user,
            "text_format": schema,
            "max_output_tokens": max_tokens,
            "store": False,
            "prompt_cache_key": f"ci:{agent.name}"[:64],
        }
        if agent.effort:
            kwargs["reasoning"] = {"effort": agent.effort}
        started = time.perf_counter()
        with generation_span(stage, model=model_id, agent=agent.name, input_text=user) as gen:
            try:
                response = await self.client.responses.parse(**kwargs)
            except openai.APIError as err:
                raise ProviderError(f"{stage}: {type(err).__name__}: {getattr(err, 'message', err)}") from err
            except (ValidationError, ValueError) as err:
                raise ProviderError(f"{stage}: unparseable output ({type(err).__name__})") from err
            if any(
                c.type == "refusal"
                for item in response.output
                if item.type == "message"
                for c in item.content
            ):
                raise ProviderError(f"{stage}: the model declined the request")
            if response.status != "completed":
                reason = (
                    response.incomplete_details.reason if response.incomplete_details else response.status
                )
                raise ProviderError(f"{stage}: incomplete output ({reason})")
            parsed = response.output_parsed
            if parsed is None:
                raise ProviderError(f"{stage}: no parseable output")
            u = response.usage
            input_tokens = u.input_tokens if u else 0  # includes cached tokens
            output_tokens = u.output_tokens if u else 0
            cached = u.input_tokens_details.cached_tokens if u and u.input_tokens_details else 0
            tokens = input_tokens + output_tokens
            cost = cost_of(agent, model_id, tokens)
            gen.finish(
                output=parsed.model_dump_json(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cached or 0,
                cost_minor=cost,
            )
        return parsed, Usage(model_id, tokens, cost, int((time.perf_counter() - started) * 1000))
