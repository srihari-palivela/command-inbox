"""Model-backend health for the chrome's "Agent live" pill (the circuit breakers live in agents/breaker.py)."""

from __future__ import annotations

import time
from typing import Literal

from command_inbox.config import settings

_open_until: dict[str, float] = {}


def trip(until_monotonic: float, source: str = "llm") -> None:
    """Record that a backend's circuit is open until the given `time.monotonic()` instant."""
    _open_until[source] = until_monotonic


def degraded_sources() -> list[str]:
    now = time.monotonic()
    return sorted(k for k, v in _open_until.items() if now < v)


def reset() -> None:
    """Tests only."""
    _open_until.clear()


def llm_status() -> tuple[Literal["claude", "openai", "heuristic"], bool]:
    default = settings.default_provider
    provider: Literal["claude", "openai", "heuristic"] = "claude" if default == "anthropic" else default
    return provider, bool(degraded_sources())
