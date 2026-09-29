"""Per-workspace API rate limit: a sliding window over the last minute, per API process.

The limit is the tenant's `limits.apiPerMinute` (set by an operator in the console), else
`API_RATE_PER_MINUTE`. It protects the shared database and model budget from one runaway client or
script; a person using the app never comes close. With N API replicas behind the load balancer the
effective limit is N times this (the stack is single-tenant, D1, with a small fixed replica count, so this
is predictable without a shared store).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from command_inbox.config import settings
from command_inbox.db.engine import global_tx
from command_inbox.db.models import Org

LIMIT_TTL = 60.0


@dataclass(slots=True)
class _Window:
    minute: int = 0
    current: int = 0
    previous: int = 0


@dataclass(slots=True)
class RateLimiter:
    windows: dict[str, _Window] = field(default_factory=dict)
    limits: dict[str, tuple[float, int]] = field(default_factory=dict)

    async def limit_for(self, org_id: str) -> int:
        cached = self.limits.get(org_id)
        now = time.monotonic()
        if cached and now - cached[0] < LIMIT_TTL:
            return cached[1]
        async with global_tx() as g:
            raw = (await g.execute(select(Org.limits).where(Org.id == org_id))).scalar_one_or_none() or {}
        limit = int(raw.get("apiPerMinute") or settings.api_rate_per_minute)
        self.limits[org_id] = (now, limit)
        return limit

    def hit(self, org_id: str, limit: int, now: float | None = None) -> tuple[bool, int]:
        """Count one request. Returns (allowed, seconds to wait when not)."""
        now = time.time() if now is None else now
        minute = int(now // 60)
        w = self.windows.setdefault(org_id, _Window(minute))
        if minute != w.minute:
            w.previous = w.current if minute == w.minute + 1 else 0
            w.current, w.minute = 0, minute
        elapsed = (now % 60) / 60
        estimate = w.previous * (1 - elapsed) + w.current
        if estimate >= limit:
            return False, max(1, math.ceil(60 - now % 60))
        w.current += 1
        return True, 0

    async def allow(self, org_id: str) -> tuple[bool, int]:
        if settings.api_rate_per_minute <= 0:
            return True, 0
        return self.hit(org_id, await self.limit_for(org_id))

    def reset(self) -> None:
        self.windows.clear()
        self.limits.clear()


limiter = RateLimiter()
