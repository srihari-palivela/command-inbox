"""Deterministic natural-language → ticket filters: the offline/degraded path for the query box and the
reference behaviour a model path is evaluated against."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chip:
    key: str
    value: str
    text: str


@dataclass
class ParsedFilters:
    filters: dict[str, Any] = field(default_factory=dict)
    chips: list[Chip] = field(default_factory=list)


def parse_natural_filters(raw: str, departments: list[dict[str, str]]) -> ParsedFilters:
    q = " " + raw.lower().strip() + " "
    out = ParsedFilters()

    def has(*words: str) -> bool:
        return any(w in q for w in words)

    def add(key: str, value: str, text: str) -> None:
        out.filters[key] = value
        out.chips.append(Chip(key, str(value), text))

    if has("triaging", "being sorted"):
        add("status", "triage", "Agent triaging")
    elif has("approval", "approve", "sign off", "waiting on me"):
        add("status", "approval", "Awaiting approval")
    elif has("executing", "in progress"):
        add("status", "executing", "Executing")
    elif has("with a human", "with a person", "handed over"):
        add("status", "human", "With a human")
    elif has("waiting on customer", "waiting for the customer"):
        add("status", "customer", "Waiting on customer")
    elif has("closed", "resolved", " done "):
        add("status", "resolved", "Resolved")

    if has(" auto ", "automated", "automatic"):
        add("lane", "auto", "Handled: Auto")
    elif has("draft"):
        add("lane", "draft", "Handled: Draft")
    elif has("manual", "human-only", "needs me", "needs a person"):
        add("lane", "manual", "Handled: You")

    def dept(needle: str) -> dict[str, str] | None:
        return next((d for d in departments if needle in d["name"].lower()), None)

    team = (
        (has("dispute", "chargeback") and dept("dispute"))
        or (has("trade", "payment", "remittance", "swift") and dept("trade"))
        or (has("lending", "loan", "foreclos", " emi") and dept("lending"))
        or (has("card") and dept("card"))
        or (has("retail", "service desk") and dept("retail"))
        or None
    )
    if team:
        add("team", team["id"], f"Team: {team['name']}")

    if has("assigned to me", "my tickets", " mine ", " me "):
        add("owner", "mine", "Owner: me")
    elif has("unowned", "unassigned", "nobody", "no owner"):
        add("owner", "unassigned", "Owner: nobody")
    elif has("agent-owned", "the ai owns", "ai owned", "ai-owned", "owned by the ai"):
        add("owner", "ai", "Owner: the AI")

    if has(" late", "overdue", "breach", "at risk", "urgent", "running out"):
        add("due", "risk", "Running late")
    elif has(" open "):
        add("due", "open", "Open only")

    if has("below the bar", "low confidence", "unsure", "not confident"):
        add("conf", "low", "Below the bar")
    elif has("high confidence", "confident"):
        add("conf", "high", "High confidence")

    m = re.search(r"\bp([1-4])\b", q)
    if m:
        add("pri", f"P{m.group(1)}", f"Priority: P{m.group(1)}")
    return out
