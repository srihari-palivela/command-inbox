"""AI observability: OpenTelemetry spans with `gen_ai.*` and Langfuse attributes.

Spans go through the process tracer provider (`core.telemetry.configure_tracing`), so the same trace runs
from the job to every flow node and model call. The Langfuse client is initialised only when its keys are
set (and never in tests); it attaches its span processor to that provider and exports the AI spans. With a
collector in front of Langfuse instead, leave the keys unset: the `gen_ai.*` / `langfuse.*` attributes are
what the collector routes on. Only masked text is ever recorded, truncated.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from command_inbox.config import settings

log = structlog.get_logger(__name__)
SCOPE = "command_inbox.agents"
tracer = trace.get_tracer(SCOPE)
MAX_ATTR = 4000

_langfuse: Any = None
_langfuse_tried = False


def _clip(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= MAX_ATTR else text[: MAX_ATTR - 1] + "…"


def langfuse_enabled() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key) and settings.env != "test"


def init_langfuse() -> Any:
    """Create the Langfuse client once, only when configured. Returns None otherwise (and in tests)."""
    global _langfuse, _langfuse_tried
    if _langfuse_tried:
        return _langfuse
    _langfuse_tried = True
    if not langfuse_enabled():
        return None
    try:
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import ReadableSpan, TracerProvider

        def export(span: ReadableSpan) -> bool:
            scope = span.instrumentation_scope.name if span.instrumentation_scope else ""
            return scope.startswith(SCOPE) or any(
                k.startswith(("gen_ai", "langfuse")) for k in (span.attributes or {})
            )

        provider = trace.get_tracer_provider()
        _langfuse = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            environment=settings.env,
            tracer_provider=provider if isinstance(provider, TracerProvider) else None,
            should_export_span=export,
        )
    except Exception as err:  # observability must never stop triage
        log.warning("langfuse initialisation failed; continuing without it", err=str(err))
        _langfuse = None
    return _langfuse


def flush() -> None:
    if _langfuse is not None:
        try:
            _langfuse.flush()
        except Exception as err:
            log.warning("langfuse flush failed", err=str(err))


@contextmanager
def run_span(name: str, *, ticket_id: str, org_id: str, deployment: str, input_text: str) -> Iterator[Span]:
    """The root span of one flow run: a Langfuse trace (session = ticket, user = tenant)."""
    init_langfuse()
    attrs = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": name,
        "langfuse.trace.name": name,
        "langfuse.observation.type": "agent",
        "session.id": ticket_id,
        "user.id": org_id,
        "langfuse.session.id": ticket_id,
        "langfuse.user.id": org_id,
        "langfuse.trace.tags": [deployment],
        "langfuse.trace.input": _clip(input_text),
        "langfuse.observation.input": _clip(input_text),
        "tenant.id": org_id,
        "ticket.id": ticket_id,
        "deployment": deployment,
    }
    with tracer.start_as_current_span(name, attributes=attrs) as span:
        yield span


def set_output(span: Span, output: Any) -> None:
    span.set_attribute("langfuse.trace.output", _clip(output))
    span.set_attribute("langfuse.observation.output", _clip(output))


@contextmanager
def node_span(node: str, attrs: dict[str, Any] | None = None) -> Iterator[Span]:
    with tracer.start_as_current_span(
        f"node {node}",
        attributes={
            "gen_ai.operation.name": "chain",
            "langfuse.observation.type": "chain",
            "flow.node": node,
            **(attrs or {}),
        },
    ) as span:
        yield span


@dataclass(slots=True)
class Generation:
    span: Span

    def finish(
        self,
        *,
        output: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cost_minor: int | None = None,
    ) -> None:
        s = self.span
        s.set_attribute("langfuse.observation.output", _clip(output))
        s.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        s.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        s.set_attribute(
            "langfuse.observation.usage_details",
            json.dumps({"input": input_tokens, "output": output_tokens, "cache_read": cache_read_tokens}),
        )
        if cost_minor is not None:
            s.set_attribute("model.cost_minor", cost_minor)


@contextmanager
def generation_span(stage: str, *, model: str, agent: str, input_text: str) -> Iterator[Generation]:
    """One model call. Errors are recorded on the span and re-raised."""
    with tracer.start_as_current_span(
        f"gen_ai {stage}",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": model,
            "gen_ai.agent.name": agent,
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": model,
            "langfuse.observation.input": _clip(input_text),
        },
    ) as span:
        try:
            yield Generation(span)
        except Exception as err:
            span.set_status(Status(StatusCode.ERROR, str(err)[:200]))
            span.set_attribute("langfuse.observation.level", "ERROR")
            raise
