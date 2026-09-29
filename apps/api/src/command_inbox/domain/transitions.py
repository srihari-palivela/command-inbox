"""Status moves a person may make directly. Approval-driven moves happen through the gateway."""

from __future__ import annotations

MANUAL: dict[str, list[str]] = {
    "triaging": ["with_human"],
    "awaiting_approval": ["with_human", "waiting_customer"],
    "executing": [],
    "with_human": ["waiting_customer", "resolved"],
    "waiting_customer": ["with_human", "resolved"],
    "resolved": ["closed", "with_human"],
    "closed": ["with_human"],
}


def allowed_transitions(frm: str) -> list[str]:
    return list(MANUAL.get(frm, []))


def can_transition(frm: str, to: str) -> bool:
    return to in MANUAL.get(frm, [])


def is_open(status: str) -> bool:
    return status not in ("resolved", "closed")


STATUS_GROUP: dict[str, str] = {
    "triaging": "triage",
    "awaiting_approval": "approval",
    "executing": "executing",
    "with_human": "human",
    "waiting_customer": "customer",
    "resolved": "resolved",
    "closed": "resolved",
}
