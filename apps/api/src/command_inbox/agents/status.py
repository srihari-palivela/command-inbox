"""Model-provider health for the chrome's "Agent live" pill (the circuit breaker lives in agents/providers)."""

from __future__ import annotations

from typing import Literal

from command_inbox.config import settings

_open_until = 0.0


def trip(until_monotonic: float) -> None:
    global _open_until
    _open_until = until_monotonic


def llm_status() -> tuple[Literal["claude", "heuristic"], bool]:
    import time

    provider: Literal["claude", "heuristic"] = "claude" if settings.use_claude else "heuristic"
    return provider, provider == "claude" and time.monotonic() < _open_until
