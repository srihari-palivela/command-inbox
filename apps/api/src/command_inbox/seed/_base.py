"""Seeding primitives: a fixed seed clock with JavaScript `Date` arithmetic, deterministic ids and inserts.

The TypeScript seed computes every timestamp as `new Date(now.getTime() ± minutes * 60_000)`, which
truncates fractional milliseconds. `SeedClock` reproduces that arithmetic exactly, so both implementations
write the same instants for the same `now`. Ids are UUIDv5 over (table, ordinal), and columns whose database
default is `now()` are filled with the seed clock, so a seed is a pure function of `now`.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from sqlalchemy import Table, event, insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.db.models import AuditEvent, Base

MIN = 60_000
DAY = 24 * 60 * MIN
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SEED_NAMESPACE = uuid.UUID("6f1c2d0e-5b7a-4c1e-9a53-0c1d5eed0001")


def to_ms(dt: datetime) -> int:
    """Whole milliseconds since the epoch (what a JavaScript Date holds)."""
    delta = dt.astimezone(UTC) - EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


def from_ms(ms: float) -> datetime:
    """`new Date(ms)`: the time value is truncated toward zero to whole milliseconds."""
    return EPOCH + timedelta(milliseconds=math.trunc(ms))


def js_round(x: float) -> float:
    """`Math.round`: halves round up, unlike Python's banker's rounding."""
    return math.floor(x + 0.5)


def to_fixed(x: float, digits: int = 2) -> str:
    """`Number.prototype.toFixed` for non-negative numbers (exact value, ties away from zero)."""
    return str(Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def initials_of(name: str) -> str:
    """Port of `initialsOf` in packages/contracts."""
    cleaned = "".join(ch for ch in name if ch.isascii() and (ch.isalpha() or ch in " ."))
    parts = [p for p in cleaned.replace(".", " ").split() if p]
    return "".join(p[0].upper() for p in parts)[:2]


class SeedClock:
    def __init__(self, now: datetime) -> None:
        self.now_ms = to_ms(now)
        self.now = from_ms(self.now_ms)

    def ago(self, minutes: float) -> datetime:
        return from_ms(self.now_ms - minutes * MIN)

    def in_min(self, minutes: float) -> datetime:
        return from_ms(self.now_ms + minutes * MIN)

    def days_ago(self, days: float) -> datetime:
        return from_ms(self.now_ms - days * DAY)

    def day(self, dt: datetime) -> date:
        """`d.toISOString().slice(0, 10)`: the UTC calendar day."""
        return dt.astimezone(UTC).date()


def _now_default_columns(model: type[Base]) -> list[str]:
    cols = []
    for c in model.__table__.columns:
        default = c.server_default
        if default is not None and getattr(default.arg, "text", "") == "now()":
            cols.append(c.key)
    return cols


def _has_generated_id(model: type[Base]) -> bool:
    col = model.__table__.columns.get("id")
    return (
        col is not None
        and col.server_default is not None
        and "gen_random_uuid" in str(col.server_default.arg)
    )


class Writer:
    """Inserts rows with deterministic ids and `now()` defaults taken from the seed clock."""

    def __init__(self, tx: AsyncSession, clock: SeedClock) -> None:
        self.tx = tx
        self.clock = clock
        self._ordinal: dict[str, int] = defaultdict(int)
        # audit() adds AuditEvent objects without an id; give them deterministic ones before they flush.
        event.listen(tx.sync_session, "before_flush", self._assign_audit_ids)

    def new_id(self, table: str) -> str:
        self._ordinal[table] += 1
        return str(uuid.uuid5(SEED_NAMESPACE, f"{table}:{self._ordinal[table]}"))

    def _assign_audit_ids(self, session: Any, _ctx: Any, _instances: Any) -> None:
        for obj in session.new:
            if isinstance(obj, AuditEvent) and obj.id is None:
                obj.id = self.new_id("audit_events")

    def detach(self) -> None:
        event.remove(self.tx.sync_session, "before_flush", self._assign_audit_ids)

    async def insert(self, model: type[Base], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert rows (all with the same keys) and return them with their ids filled in."""
        if not rows:
            return []
        table = model.__tablename__
        with_id = _has_generated_id(model)
        now_cols = _now_default_columns(model)
        for row in rows:
            if with_id and "id" not in row:
                row["id"] = self.new_id(table)
            for c in now_cols:
                row.setdefault(c, self.clock.now)
        # Core insert on the table: column names, not ORM attribute names (e.g. `text` vs `text_`).
        table_ = cast(Table, model.__table__)
        await self.tx.execute(insert(table_), rows)
        return rows

    async def one(self, model: type[Base], row: dict[str, Any]) -> dict[str, Any]:
        return (await self.insert(model, [row]))[0]
