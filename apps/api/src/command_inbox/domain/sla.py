"""Deadline pressure. Late if past due; almost late inside the last hour or 10% of budget; due soon inside
half the budget. The clock is paused while waiting on the customer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from command_inbox.core.clock import iso_ms
from command_inbox.schemas.dto import SlaDTO


@dataclass(frozen=True, slots=True)
class SlaInput:
    status: str
    due_at: datetime | None
    sla_minutes: int
    paused_at: datetime | None


def compute_sla(t: SlaInput, now: datetime) -> SlaDTO:
    budget = t.sla_minutes
    if t.status in ("resolved", "closed"):
        return SlaDTO(tone="closed", minutes_left=None, budget_minutes=budget, due_at=None)
    if t.due_at is None:
        return SlaDTO(tone="on_track", minutes_left=None, budget_minutes=budget, due_at=None)
    ref = t.paused_at or now
    # Math.round semantics (half up), matching the previous service.
    left = (
        int((t.due_at - ref).total_seconds() / 60 + 0.5)
        if t.due_at >= ref
        else -int((ref - t.due_at).total_seconds() / 60 + 0.5)
    )
    due = iso_ms(t.due_at)
    if t.paused_at:
        tone = "paused"
    elif left < 0:
        tone = "late"
    elif left <= 60 or left <= budget * 0.1:
        tone = "almost_late"
    elif left <= budget * 0.5:
        tone = "due_soon"
    else:
        tone = "on_track"
    return SlaDTO(tone=tone, minutes_left=left, budget_minutes=budget, due_at=due)


def at_risk(tone: str) -> bool:
    return tone in ("due_soon", "almost_late", "late")


@dataclass(frozen=True, slots=True)
class SlaRule:
    """A workspace's reply-time target. None matches any priority or segment."""

    minutes: int
    priority: str | None = None
    segment: str | None = None
    escalation: bool = False
    name: str = ""


# What a workspace gets until its admin sets its own (the starter pack writes these as its policies).
DEFAULT_SLA_RULES: tuple[SlaRule, ...] = (
    SlaRule(8 * 60, escalation=True, name="Escalations and regulator mentions"),
    SlaRule(4 * 60, priority="P1", segment="Corporate", name="Urgent corporate"),
    SlaRule(8 * 60, priority="P1", name="Urgent"),
    SlaRule(24 * 60, name="Everything else"),
)


def sla_budget(
    priority: str, segment: str, escalation: bool = False, rules: tuple[SlaRule, ...] | list[SlaRule] = ()
) -> int:
    """Minutes to reply: the most specific matching rule (priority and segment, then priority, then segment,
    then neither); an escalation uses the escalation rules first. Built-in defaults when none match."""
    for table in (rules, DEFAULT_SLA_RULES) if rules else (DEFAULT_SLA_RULES,):
        passes = (True, False) if escalation else (False,)
        for esc in passes:
            matches = [
                r
                for r in table
                if r.escalation == esc
                and r.priority in (None, priority)
                and (r.segment is None or r.segment.lower() == segment.lower())
            ]
            if matches:
                best = max(matches, key=lambda r: (r.priority is not None) * 2 + (r.segment is not None))
                return best.minutes
    return 24 * 60
