"""Keyword signals ported from the TypeScript heuristic provider (`providers/heuristic.ts`).

The deterministic decision engine and the heuristic System 2 provider both read these, so offline triage
of the seeded mail lands in the same lanes as the previous service. Order matters: signatures are listed
strongest first and ties keep this order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Signature:
    query_type: str
    any: tuple[str, ...]
    intent: str
    strong: re.Pattern[str] | None = None
    informational: bool = False


SIGNATURES: tuple[Signature, ...] = (
    Signature(
        "Stop payment instruction",
        ("stop payment", "stop on cheque", "place a stop", "immediate stop"),
        "stop a cheque",
        strong=re.compile(r"cheque\s*(no\.?|number)?\s*\d{6}"),
    ),
    Signature(
        "Disputed transactions",
        (
            "unauthorised",
            "unauthorized",
            "never transacted",
            "dispute",
            "fraudulent",
            "duplicate neft",
            "sent twice",
        ),
        "dispute a debit",
    ),
    Signature(
        "Chargeback status",
        ("chargeback status", "status of my chargeback", "chargeback dsp"),
        "chargeback status",
    ),
    Signature(
        "Statement re-issue",
        ("statement for", "re-issue the account statement", "send me the statement", "account statement"),
        "statement copy",
    ),
    Signature(
        "Certificate requests",
        ("interest certificate", "balance confirmation", "certificate for"),
        "certificate",
    ),
    Signature("Foreclosure quotes", ("foreclosure", "foreclose"), "loan foreclosure"),
    Signature(
        "EMI reschedule requests",
        ("emi reschedule", "reschedule my emi", "emis be rescheduled", "moratorium"),
        "EMI reschedule",
    ),
    Signature(
        "Credit limit explanations",
        ("credit limit", "limit was reduced", "limit dropped"),
        "credit limit",
    ),
    Signature(
        "Trade finance advisory",
        ("forward contract", "forward cover", "receivables", "letter of credit", " lc "),
        "trade finance advice",
        informational=True,
    ),
    Signature(
        "SWIFT trace requests",
        ("swift", "trace the payment", "has not arrived", "uetr"),
        "trace a remittance",
        informational=True,
    ),
    Signature("Locker rent waiver", ("locker",), "locker"),
    Signature(
        "Balance & charge queries",
        ("annual fee", "charges", "fee schedule", "debited as", "why was", "maintenance charges"),
        "fee question",
        informational=True,
    ),
    Signature(
        "Account maintenance",
        (
            "joint holder",
            "nomination",
            "registered mobile",
            "change my address",
            "standing instruction",
            "cheque book",
        ),
        "account change",
        informational=True,
    ),
)
SIGNATURE_BY_NAME = {s.query_type: s for s in SIGNATURES}

REPEAT_CONTACT_RE = re.compile(
    r"third (time|email)|3rd (time|email)|written three times|chasing|again and again"
)
UPSET_RE = re.compile(r"urgent|disappointed|unacceptable|angry")
MULTI_JOIN_RE = re.compile(r"\b(two things|separately|also|and also)\b")

# Hard stops the platform has always screened for, as (key, reason, keywords). The keyword lists spell out
# the previous regular expressions exactly (substring match on lower-cased text).
BASELINE_HARD_STOPS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "regulator",
        "regulator named",
        ("ombudsman", "rbi complaint", "regulator", "consumer forum"),
        "Does the customer name a regulator, the banking ombudsman or a consumer forum?",
    ),
    (
        "legal",
        "legal notice",
        ("legal notice", "my lawyer", "advocate", "court"),
        "Does the email threaten or announce legal action?",
    ),
    (
        "fraud",
        "suspected fraud",
        ("fraud", "scam", "phishing", "hacked"),
        "Does the customer report fraud, a scam or a compromised account?",
    ),
    (
        "vulnerable",
        "vulnerable-customer signal",
        (
            "passed away",
            "deceased",
            "bereave",
            "died",
            "terminal",
            "serious illness",
            "hospitalised",
            "hospitalized",
            "cannot afford",
            "can't afford",
            "financial distress",
        ),
        "Does the email show the customer may be vulnerable (bereavement, serious illness, hardship)?",
    ),
)


@dataclass(slots=True)
class Hit:
    name: str
    phrases: list[str]
    score: int
    signature: Signature | None = None
    group: str | None = None
    order: int = 0
    extra: dict[str, object] = field(default_factory=dict)


def phrase_hits(body: str, name: str, phrases: tuple[str, ...], strong: re.Pattern[str] | None) -> Hit:
    """Score one label on a padded, lower-cased body: one point per phrase, two for the strong pattern."""
    found = [w for w in phrases if w in body]
    bonus = 2 if strong is not None and strong.search(body) else 0
    return Hit(name=name, phrases=found, score=len(found) + bonus)
