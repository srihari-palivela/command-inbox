"""The mailbox health model (production plan §7.5). Every signal is computed from recorded facts."""

from __future__ import annotations

from datetime import datetime, timedelta

from command_inbox.db.models import Mailbox
from command_inbox.schemas import dto

ORDER = {"healthy": 0, "unknown": 1, "degraded": 2, "down": 3}


def _ago(now: datetime, at: datetime | None) -> str:
    if at is None:
        return "never"
    s = int((now - at).total_seconds())
    if s < 90:
        return f"{max(s, 0)} s ago"
    if s < 5400:
        return f"{s // 60} min ago"
    if s < 172_800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def signals(
    mb: Mailbox,
    now: datetime,
    *,
    streaming: bool,
    sends_ok: int,
    sends_failed: int,
    throttled: int,
    calls: int,
) -> list[dto.MailboxHealthSignalDTO]:
    out: list[dto.MailboxHealthSignalDTO] = []

    def add(key: str, label: str, level: str, value: str) -> None:
        out.append(dto.MailboxHealthSignalDTO(key=key, label=label, level=level, value=value))  # type: ignore[arg-type]

    if streaming and mb.stream_expires_at:
        left = mb.stream_expires_at - now
        level = "healthy" if left > timedelta(hours=48) else "degraded" if left > timedelta(0) else "down"
        add(
            "stream",
            "Change notifications",
            level,
            f"expires in {max(0, int(left.total_seconds() // 3600))} h",
        )
    else:
        add("stream", "Change notifications", "unknown", "polling (no public webhook URL)")

    if mb.lag_seconds is None:
        add("lag", "Arrival to ticket", "unknown", "no mail yet")
    else:
        lag = mb.lag_seconds
        add(
            "lag",
            "Arrival to ticket",
            "healthy" if lag < 60 else "degraded" if lag < 600 else "down",
            f"{lag} s",
        )

    if mb.last_sweep_at is None:
        add("sweep", "Last catch-up", "unknown", "not yet")
    else:
        since = now - mb.last_sweep_at
        level = (
            "healthy"
            if since < timedelta(minutes=15)
            else "degraded"
            if since < timedelta(hours=1)
            else "down"
        )
        add("sweep", "Last catch-up", level, _ago(now, mb.last_sweep_at))

    if mb.connection == "reauth_required":
        add("credential", "Sign-in", "down", "reconnect required")
    elif mb.token_expires_at is None:
        add("credential", "Sign-in", "unknown", "not connected")
    else:
        add("credential", "Sign-in", "healthy", "valid (refreshed automatically)")

    total = sends_ok + sends_failed
    if total == 0:
        add("send", "Replies sent (24 h)", "unknown", "none yet")
    else:
        rate = sends_ok / total
        add(
            "send",
            "Replies sent (24 h)",
            "healthy" if rate >= 0.99 else "degraded" if rate >= 0.95 else "down",
            f"{sends_ok} of {total}",
        )

    if calls == 0:
        add("throttling", "Provider throttling (24 h)", "healthy", "none")
    else:
        rate = throttled / calls
        add(
            "throttling",
            "Provider throttling (24 h)",
            "healthy" if rate < 0.01 else "degraded" if rate < 0.05 else "down",
            f"{throttled} of {calls} syncs",
        )
    return out


def overall(mb: Mailbox, sig: list[dto.MailboxHealthSignalDTO]) -> str:
    if mb.connection in ("not_connected", "disconnected"):
        return "unknown"
    if mb.connection == "reauth_required":
        return "down"
    worst = max((ORDER[s.level] for s in sig if s.level != "unknown"), default=0)
    return next(k for k, v in ORDER.items() if v == worst)
