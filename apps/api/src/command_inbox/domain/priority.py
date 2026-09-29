"""Deterministic priority rules. Hard rules always fire (policy, not preference); weighted ones can be off."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

Priority = Literal["P1", "P2", "P3", "P4"]
_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


@dataclass(frozen=True, slots=True)
class PrioritySignals:
    regulator_named: bool
    vulnerable: bool
    minutes_left: float | None
    amount_inr: float | None
    contact_count: int
    informational: bool


@dataclass(frozen=True, slots=True)
class PriorityRuleRow:
    key: str
    hard: bool
    enabled: bool


def _at_least(cur: Priority, floor: Priority) -> Priority:
    return floor if _RANK[cur] > _RANK[floor] else cur


def rank_priority(s: PrioritySignals, rules: Sequence[PriorityRuleRow]) -> tuple[Priority, list[str]]:
    def on(key: str) -> bool:
        r = next((x for x in rules if x.key == key), None)
        return r is None or r.hard or r.enabled

    fired: list[str] = []
    p: list[Priority] = ["P3"]

    def fire(key: str, cond: bool, apply: Callable[[Priority], Priority]) -> None:
        if cond and on(key):
            fired.append(key)
            p[0] = apply(p[0])

    fire("p7", s.informational and not s.regulator_named and not s.vulnerable, lambda _: "P4")
    fire("p5", s.minutes_left is not None and s.minutes_left <= 8 * 60, lambda c: _at_least(c, "P2"))
    fire("p4", s.contact_count >= 3, lambda c: _at_least(c, "P2"))
    fire("p3", (s.amount_inr or 0) >= 10_00_000, lambda c: _at_least(c, "P2"))
    fire("p2", s.minutes_left is not None and s.minutes_left <= 120, lambda _: "P1")
    fire("p1", s.regulator_named, lambda _: "P1")
    fire("p6", s.vulnerable, lambda _: "P1")
    return p[0], fired
