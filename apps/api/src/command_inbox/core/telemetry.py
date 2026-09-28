"""Logs, traces and metrics.

- Logs: structlog, JSON in production, with the active trace and span ids on every line so a log line
  links to its trace. The same records are exported over OTLP when a collector is configured.
- Traces: OpenTelemetry SDK with FastAPI, SQLAlchemy and httpx instrumentation, exported over OTLP/HTTP to
  the OpenTelemetry Collector. The collector fans out: every span to the tracing backend, and the
  generative-AI spans (LangGraph nodes and model calls, `gen_ai.*` attributes) to Langfuse.
- Metrics: Prometheus, scraped at /metrics (RED metrics, job lag, lane mix, gate decisions, model cost).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram

from command_inbox.config import settings

_configured = False


def _add_trace_ids(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    span = trace.get_current_span()
    sc = span.get_span_context()
    if sc.is_valid:
        event["trace_id"] = format(sc.trace_id, "032x")
        event["span_id"] = format(sc.span_id, "016x")
    return event


_REDACT = {
    "cookie",
    "authorization",
    "x-csrf-token",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "credentials",
}


def _redact(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    for k in list(event):
        if k.lower() in _REDACT:
            event[k] = "[redacted]"
    return event


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace_ids,
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = structlog.processors.JSONRenderer() if settings.log_json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging (uvicorn, sqlalchemy, httpx) through the same JSON pipeline.
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def configure_tracing(app: Any | None = None, service_name: str | None = None) -> None:
    """Install the tracer provider once per process; instrument the app when one is given."""
    global _configured
    if _configured or settings.env == "test":
        return
    _configured = True
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": service_name or settings.otel_service_name,
            "service.namespace": "command-inbox",
            "deployment.environment": settings.env,
        }
    )
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")
            )
        )
    trace.set_tracer_provider(provider)

    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        lp = LoggerProvider(resource=resource)
        lp.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/logs")
            )
        )
        set_logger_provider(lp)
        logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=lp))

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from command_inbox.db.engine import engine

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    HTTPXClientInstrumentor().instrument()
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")


# ── Prometheus metrics ─────────────────────────────────────────────────────────
http_duration = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "route", "status"]
)
jobs_processed = Counter("jobs_processed_total", "Jobs processed", ["kind", "outcome"])
job_lag = Gauge("jobs_oldest_due_seconds", "Age of the oldest due job")
gate_decisions = Counter(
    "gate_decisions_total", "Approval gateway decisions", ["mode", "outcome", "opened_evidence"]
)
lane_decisions = Counter("triage_lane_total", "Lane decisions", ["deployment", "lane"])
model_cost = Counter("model_cost_minor_total", "Model spend in minor currency units", ["agent", "model"])
decision_latency = Histogram("decision_engine_seconds", "Decision engine latency", ["engine", "primitive"])
sse_clients = Gauge("sse_clients", "Connected live-update clients")
