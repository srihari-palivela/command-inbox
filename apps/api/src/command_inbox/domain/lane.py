"""The lane decision: deterministic and ordered — safety, then ownership, then confidence.

Models supply inputs; they never pick the lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Lane = Literal["auto", "draft", "manual"]


@dataclass(frozen=True, slots=True)
class LaneInputs:
    hard_stop: str | None
    confidence: float
    bar: float
    query_type_owned: bool
    multi_intent: bool
    has_template: bool
    fields_complete: bool
    coverage: Literal["full", "partial", "none"]
    informational: bool
    sender_verified: bool = True
    escalated_unresolved: bool = False


@dataclass(frozen=True, slots=True)
class LaneDecision:
    lane: Lane
    note: str


def decide_lane(i: LaneInputs) -> LaneDecision:
    if i.hard_stop:
        return LaneDecision("manual", f"Held back — {i.hard_stop}")
    if i.multi_intent:
        return LaneDecision("manual", "Two separate questions in one email")
    if not i.query_type_owned:
        return LaneDecision("manual", "No team owns this query type yet")
    if i.escalated_unresolved:
        return LaneDecision("manual", "The categorisation stayed uncertain after review — handed to a person")
    if i.has_template and not i.informational:
        if not i.sender_verified:
            # Mail that failed sender authentication never fills an action (spoofing defence).
            return LaneDecision("manual", "Sender could not be verified — a person checks before any action")
        if i.fields_complete and i.confidence >= i.bar:
            return LaneDecision("auto", "Filled in, waiting at the approval gate")
        return LaneDecision("manual", "Action matched but fields or confidence fall short")
    if i.coverage == "full" and i.confidence >= i.bar:
        return LaneDecision("draft", "Cited draft ready to send")
    if i.coverage != "none":
        return LaneDecision("draft", "Not confident enough — read it before sending")
    return LaneDecision("manual", "No approved content covers this — handed to a person")
