"""Injectable clock so time-dependent rules (SLA, undo and recall windows) are testable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class _Clock:
    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, seconds: float) -> None:
        """Tests only."""
        self._offset += timedelta(seconds=seconds)

    def reset(self) -> None:
        self._offset = timedelta(0)


clock = _Clock()


def iso_ms(dt: datetime) -> str:
    """ISO-8601 in UTC with millisecond precision and a Z suffix (what browsers and the audit chain use)."""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
