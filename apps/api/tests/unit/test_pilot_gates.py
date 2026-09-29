"""The pilot's gates for shadow → assisted and assisted → live, judged from the KPIs alone."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from command_inbox.core.clock import clock
from command_inbox.modules.pilot.kpis import Kpis
from command_inbox.modules.pilot.service import gates

TARGETS = {"minLabelled": 10, "minDrafts": 10, "shadowDays": 14, "assistedDays": 28}


def org(status: str, days: int, baseline: dict[str, Any] | None = None) -> Any:
    now = clock.now()
    return SimpleNamespace(
        id="o",
        status=status,
        status_changed_at=now - timedelta(days=days),
        created_at=now - timedelta(days=90),
        pilot_settings={"targets": TARGETS, "baseline": baseline or {}},
    )


def kpis(**kw: Any) -> Kpis:
    return Kpis(since=clock.now(), days=0, **kw)


def states(gs: list[Any]) -> dict[str, str]:
    return {g.key: g.state for g in gs}


async def test_shadow_waits_for_days_and_labels_then_judges_agreement():
    s = states(await gates(None, org("shadow", 3), kpis()))  # type: ignore[arg-type]
    assert s == {
        "days": "pending",
        "labelled": "pending",
        "category": "pending",
        "lane": "pending",
        "hard_stops": "pass",
        "p1": "pass",
    }
    good = kpis(labelled=20, category_agreed=19, lane_compared=40, lane_agreed=36)
    assert set(states(await gates(None, org("shadow", 15), good)).values()) == {"pass"}  # type: ignore[arg-type]
    weak = kpis(
        labelled=20, category_agreed=10, lane_compared=40, lane_agreed=36, hard_stop_misses=1, p1_incidents=1
    )
    s = states(await gates(None, org("shadow", 15), weak))  # type: ignore[arg-type]
    assert s["category"] == "fail" and s["lane"] == "pass" and s["hard_stops"] == "fail" and s["p1"] == "fail"


async def test_live_needs_acceptance_and_reply_times_at_least_the_baseline():
    base = {"onTimeRate": 0.8, "firstReplyMinutes": 120}
    good = kpis(drafts_decided=20, drafts_accepted=15, on_time_rate=0.85, median_first_reply_minutes=90)
    s = states(await gates(None, org("assisted", 30, base), good))  # type: ignore[arg-type]
    assert s == {
        "days": "pass",
        "drafts": "pass",
        "acceptance": "pass",
        "hard_stops": "pass",
        "sla": "pass",
        "first_reply": "pass",
        "p1": "pass",
    }
    slow = kpis(drafts_decided=20, drafts_accepted=13, on_time_rate=0.75, median_first_reply_minutes=150)
    s = states(await gates(None, org("assisted", 30, base), slow))  # type: ignore[arg-type]
    assert s["acceptance"] == "fail" and s["sla"] == "fail" and s["first_reply"] == "fail"
    # Without a baseline there is nothing to show improvement against.
    s = states(await gates(None, org("assisted", 30), good))  # type: ignore[arg-type]
    assert s["sla"] == "fail" and "first_reply" not in s


async def test_live_has_no_further_stage():
    assert await gates(None, org("live", 30), kpis()) == []  # type: ignore[arg-type]
