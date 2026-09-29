"""Circuit breaker shared by the decision engine and the System 2 provider.

After `threshold` consecutive failures the circuit opens for `open_seconds`: calls go straight to the
deterministic fallback and a person gets the ticket. The "Agent live" pill reads `agents.status`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import structlog

from command_inbox.agents import status

log = structlog.get_logger(__name__)


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        threshold: int = 3,
        open_seconds: float = 60.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.threshold = threshold
        self.open_seconds = open_seconds
        self._now = now
        self.failures = 0
        self.open_until = 0.0

    @property
    def is_open(self) -> bool:
        return self._now() < self.open_until

    def success(self) -> None:
        self.failures = 0

    def failure(self, err: BaseException | str) -> None:
        self.failures += 1
        log.warning("model backend failure", backend=self.name, failures=self.failures, err=str(err)[:300])
        if self.failures >= self.threshold:
            self.open_until = self._now() + self.open_seconds
            self.failures = 0
            status.trip(self.open_until, source=self.name)
            log.error("circuit open: degrading to deterministic fallback and human lanes", backend=self.name)

    def reset(self) -> None:
        self.failures = 0
        self.open_until = 0.0
