"""The risk matrix: can the action be undone × does money move. Policy as code, not configuration."""

from __future__ import annotations

from typing import Literal

RiskCell = Literal["0-0", "0-1", "1-0", "1-1"]
ApprovalChain = Literal["auto", "single", "single_undo", "dual"]
ApprovalMode = Literal["auto", "single", "dual"]


def cell_of(reversible: bool, money_moves: bool) -> RiskCell:
    """Row = money moves, column = cannot be undone."""
    return f"{1 if money_moves else 0}-{0 if reversible else 1}"  # type: ignore[return-value]


CELL_TITLE: dict[str, str] = {
    "0-0": "Can be undone · no money moves",
    "0-1": "Cannot be undone · no money moves",
    "1-0": "Can be undone · money moves",
    "1-1": "Cannot be undone · money moves",
}

LOCKED_CELL: RiskCell = "1-1"
"""The irreversible + money cell is locked to suggest-only by policy, not configuration."""

MAX_DIAL: dict[str, int] = {"0-0": 2, "0-1": 1, "1-0": 1, "1-1": 0}
"""Highest dial level each cell may ever reach. Only 0-0 may auto-execute."""


def chain_for(cell: str, template: str, dial_level: int) -> ApprovalChain:
    """A template may raise the bar above the cell minimum, never lower it."""
    if cell in ("0-1", "1-1"):
        return "dual"
    if cell == "1-0":
        return "dual" if template == "dual" else "single_undo"
    if template == "dual":
        return "dual"
    if template == "auto" and dial_level >= 2:
        return "auto"
    return "single_undo"


APPROVAL_CYCLES: list[dict[str, object]] = [
    {
        "cell": CELL_TITLE["0-0"],
        "chain": ["AI fills & validates", "AI executes (approved cell)", "Sampled review weekly"],
        "note": "The only chain with no human before execution — post-hoc review, 5% sample.",
    },
    {
        "cell": CELL_TITLE["1-0"],
        "chain": ["AI fills & validates", "Staff approves", "Executes with 30s undo", "Audit written"],
        "note": "Single approver plus an undo window. Reversal after the window needs its own approval.",
    },
    {
        "cell": CELL_TITLE["0-1"],
        "chain": [
            "AI fills & validates",
            "Staff approves (maker)",
            "Team lead approves (checker)",
            "Executes · audit",
        ],
        "note": "Two approvers always, because permanence.",
    },
    {
        "cell": CELL_TITLE["1-1"],
        "chain": [
            "AI fills & validates",
            "Staff approves (maker)",
            "Team lead approves (checker)",
            "Executes · audit",
        ],
        "note": "Locked to this chain by policy. Escalates to the lead if the checker is silent for 2 hours.",
    },
]

UNDO_WINDOW_SEC = 30
RECALL_WINDOW_SEC = 60
CHECKER_ESCALATION_MIN = 120
