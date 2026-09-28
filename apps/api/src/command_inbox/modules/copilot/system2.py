"""Seam between the copilot / query box and a System 2 (LLM) provider.

Facts and filters are always computed deterministically; a model only rephrases an answer or maps
language onto the filter schema. Until `command_inbox.agents.providers` exists (owned by the AI port),
or when no model is configured (tests, offline), both functions return None and callers use the heuristic
path. Contract for the provider module, if it chooses to offer these:

    async def copilot_answer(question: str, facts: str) -> dict | None   # {"headline": str, "lines": [str]}
    async def nl_filter(query: str, departments: list[dict]) -> dict | None  # TicketFilters-shaped dict

Returning None (or raising) means "degraded": the heuristic answer is used.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import structlog

from command_inbox.config import settings
from command_inbox.domain.pii import mask_pii, unmask

log = structlog.get_logger(__name__)


def _provider() -> ModuleType | None:
    if not settings.use_claude:
        return None
    try:
        return importlib.import_module("command_inbox.agents.providers")
    except ModuleNotFoundError:
        return None


async def phrase_answer(question: str, facts: str) -> tuple[str, list[str]] | None:
    """Rephrase deterministic facts for the asker. PII never leaves the process unmasked."""
    fn = getattr(_provider(), "copilot_answer", None)
    if fn is None:
        return None
    q, f = mask_pii(question), mask_pii(facts)
    vault = {**q.vault, **f.vault}
    try:
        out: Any = await fn(q.text, f.text)
    except Exception as err:  # the heuristic answer is always available
        log.warning("copilot provider failed; using heuristic answer", err=str(err))
        return None
    if not out or not isinstance(out.get("headline"), str) or not isinstance(out.get("lines"), list):
        return None
    return unmask(out["headline"], vault), [unmask(str(line), vault) for line in out["lines"]]


async def model_filters(query: str, departments: list[dict[str, str]]) -> dict[str, Any] | None:
    fn = getattr(_provider(), "nl_filter", None)
    if fn is None:
        return None
    try:
        out: Any = await fn(mask_pii(query).text, departments)
    except Exception as err:
        log.warning("nl-filter provider failed; using the parser", err=str(err))
        return None
    return out if isinstance(out, dict) else None
