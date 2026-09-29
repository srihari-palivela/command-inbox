"""Claude as System 2: every stage is one structured-output call (`messages.parse` with a Pydantic model), so
the flow only ever receives schema-valid data.

The agent's own prompt is the system prompt and is cache-marked (it is identical across mails, so repeated
calls read it from the prompt cache); the mail goes in the user turn. Every string that leaves the process is
PII-masked here again, whatever the caller did. Refusals, API errors and unparseable output raise
`ProviderError`; the router degrades to the heuristic provider and a human lane.
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel, Field, ValidationError, create_model

from command_inbox.agents.providers.types import (
    Adjudication,
    AgentSpec,
    BriefResult,
    DraftResult,
    ExtractedField,
    ExtractResult,
    GroundingDoc,
    ProviderError,
    Staged,
    TemplateSpec,
    ThreadInput,
    Usage,
)
from command_inbox.agents.tracing import generation_span
from command_inbox.config import settings
from command_inbox.domain.pii import mask_pii

M = TypeVar("M", bound=BaseModel)
UNSURE = "unsure"


class _Field(BaseModel):
    label: str
    value: str = Field(description="Verbatim from the thread, or an empty string if not present")
    source: str = Field(description='"email body", or "inferred" when not stated verbatim')
    inferred: bool


class _Extract(BaseModel):
    fields: list[_Field]
    amount_inr: float | None = Field(description="Amount at stake in rupees, or null if none is stated")


class _Draft(BaseModel):
    body: str = Field(
        description="The reply, citing sources as [n]. Plain paragraphs separated by blank lines."
    )
    citations: list[int] = Field(description="Source numbers the reply relies on")
    flagged: list[str] = Field(
        description="Verbatim sentences from the body that state a gap or need checking"
    )
    coverage: Literal["full", "partial", "none"]
    gap_question: str = Field(description="The part of the question with no approved source, or empty string")


class _Pair(BaseModel):
    label: str
    value: str


class _Suggestion(BaseModel):
    label: str
    meta: str


class _Brief(BaseModel):
    summary: str
    context: list[_Pair]
    suggestions: list[_Suggestion]


class _Answer(BaseModel):
    headline: str
    lines: list[str]


def api_model(model: str) -> str:
    """Agent rows may read "rules + <model>"; take the model id, else the platform default."""
    m = re.search(r"claude-[a-z0-9-]+", model or "")
    return m.group(0) if m else settings.copilot_model


def _mask(text: str) -> str:
    return mask_pii(text).text


class ClaudeProvider:
    name: Literal["claude"] = "claude"

    def __init__(self, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._client = client

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
        model_id = model or api_model(agent.model)
        user = _mask(user)
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
            cost = round(tokens / 1000 * agent.cost_per_1k_minor) if agent.cost_per_1k_minor else None
            gen.finish(
                output=parsed.model_dump_json(),
                input_tokens=input_tokens,
                output_tokens=u.output_tokens,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cost_minor=cost,
            )
        usage = Usage(model_id, tokens, cost, int((time.perf_counter() - started) * 1000))
        return parsed, usage

    async def extract_fields(
        self, thread: ThreadInput, template: TemplateSpec, agent: AgentSpec
    ) -> Staged[ExtractResult]:
        user = (
            f"Action template {template.code} — {template.name}.\n"
            f"Fill exactly these fields: {', '.join(template.fields)}.\n"
            "Never guess amounts, account numbers or dates: leave a field empty when the thread does not "
            f"state it.\n\n{thread.rendered()}"
        )
        r, usage = await self._call("extract_fields", agent, agent.prompt, user, _Extract)
        fields = [f for f in r.fields if f.value.strip() and f.label in template.fields]
        return Staged(
            ExtractResult(
                fields=[
                    ExtractedField(f.label, f.value, "inferred" if f.inferred else f.source, f.inferred)
                    for f in fields
                ],
                complete=all(any(f.label == label for f in fields) for label in template.fields),
                amount_inr=r.amount_inr,
            ),
            usage,
        )

    async def draft_reply(
        self, thread: ThreadInput, docs: list[GroundingDoc], agent: AgentSpec, customer_name: str
    ) -> Staged[DraftResult]:
        sources = "\n\n".join(f"[{d.n}] {d.title} {d.section}\n{d.body}" for d in docs)
        user = (
            "Approved sources (the only material you may state as fact; cite each statement as [n]):\n\n"
            f"{sources or '(none)'}\n\nWrite the reply to {customer_name}.\n\n{thread.rendered()}"
        )
        r, usage = await self._call("draft_reply", agent, agent.prompt, user, _Draft, max_tokens=6000)
        valid = {d.n for d in docs}
        return Staged(
            DraftResult(
                body=r.body,
                citations=[n for n in r.citations if n in valid],  # never trust a citation we did not supply
                flagged=[f for f in r.flagged if f in r.body],
                coverage=r.coverage if docs else "none",
                gap_question=r.gap_question or None,
            ),
            usage,
        )

    async def brief(self, thread: ThreadInput, facts: str, agent: AgentSpec) -> Staged[BriefResult]:
        r, usage = await self._call(
            "brief", agent, agent.prompt, f"Records:\n{facts}\n\n{thread.rendered()}", _Brief
        )
        return Staged(
            BriefResult(
                summary=r.summary,
                context=[p.model_dump() for p in r.context[:6]],
                suggestions=[s.model_dump() for s in r.suggestions[:4]],
            ),
            usage,
        )

    async def adjudicate(
        self, text: str, candidate_labels: list[str], agent: AgentSpec
    ) -> Staged[Adjudication]:
        if not candidate_labels:
            raise ProviderError("adjudicate: no candidates")
        choice_type: Any = Literal[tuple([*candidate_labels, UNSURE])]  # type: ignore[misc]
        schema = create_model(
            "Adjudication",
            label=(choice_type, Field(description=f'One of the candidates, or "{UNSURE}"')),
            reason=(str, Field(description="One sentence")),
        )
        system = (
            f"{agent.prompt}\n\nA fast classifier could not decide between a few query types. Choose exactly "
            f'one of the candidates given, or "{UNSURE}" if the email does not clearly fit one of them.'
        )
        user = "Candidates:\n" + "\n".join(f"- {c}" for c in candidate_labels) + f"\n\nEmail:\n{text}"
        r, usage = await self._call("adjudicate", agent, system, user, schema, max_tokens=1000)
        label = getattr(r, "label", UNSURE)
        return Staged(
            Adjudication(label if label in candidate_labels else None, str(getattr(r, "reason", ""))), usage
        )

    async def copilot_answer(self, question: str, facts: str, model: str) -> dict[str, Any] | None:
        agent = AgentSpec(
            name="Copilot",
            model=model,
            prompt=(
                "You answer questions from a bank support team about their live queue. Use only the facts "
                "given; never invent numbers. Lead with a one-sentence headline, then up to three short "
                "supporting lines."
            ),
        )
        r, _ = await self._call(
            "copilot_answer",
            agent,
            agent.prompt,
            f"Facts:\n{facts}\n\nQuestion: {question}",
            _Answer,
            max_tokens=2000,
            model=model,
        )
        return {"headline": r.headline, "lines": r.lines[:4]}

    async def nl_filter(self, query: str, departments: list[dict[str, str]], model: str) -> dict[str, Any]:
        from command_inbox.schemas.requests import TicketFilters

        system = (
            "Translate a support lead's request into ticket filters. Only use these keys and values:\n"
            "status: triage | approval | executing | human | customer | resolved\n"
            "lane: auto | draft | manual\n"
            "team: one of these department ids: "
            + ", ".join(f"{d['id']} ({d['name']})" for d in departments)
            + "\nowner: mine | ai | unassigned\ndue: risk | open | closed\nconf: low | high\n"
            "pri: P1 | P2 | P3 | P4\nq: free text search, only if nothing else fits.\n"
            "Omit keys the request does not mention."
        )
        agent = AgentSpec(name="Query box", model=model, prompt=system)
        r, _ = await self._call(
            "nl_filter", agent, system, query, TicketFilters, max_tokens=1000, model=model
        )
        return r.model_dump(by_alias=True, exclude_none=True)
