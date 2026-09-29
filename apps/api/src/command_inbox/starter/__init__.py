"""The starter pack: versioned, neutral content a new tenant begins from. Never seeded as live data.

Provisioning builds the tenant's first deployment from it. The bank then replaces or extends every part in
the studio (its own categories, examples, rules and a labelled evaluation set) before shadow mode.

- A neutral banking taxonomy (cards, payments, accounts, loans, statements, complaints, fraud and security,
  anything else), each with a description and short illustrative phrases, not real mail.
- The mandatory hard stops: regulator or ombudsman named, fraud or scam, legal action, vulnerable customer,
  formal complaint escalation. They send mail straight to a person.
- Conservative thresholds, and no automatic lane at all (decision D5: no actions in the first version; the
  most the AI does is draft a reply for a person to approve).
"""

from __future__ import annotations

from typing import Any

from command_inbox.agents.config import SYSTEM2_NODES, DeploymentConfig, standard_flow
from command_inbox.agents.specs import DEFAULT_AGENTS

STARTER_PACK_VERSION = "2026.09"

_CATEGORIES: list[dict[str, Any]] = [
    {
        "key": "cards",
        "name": "Cards",
        "description": "Debit or credit card questions: a card not arriving, blocked or declined, limits, PIN, renewal.",
        "examples": [
            "my card was declined",
            "new card has not arrived",
            "increase my credit limit",
            "reset my PIN",
        ],
        "department": "Cards",
    },
    {
        "key": "payments",
        "name": "Payments and transfers",
        "description": "A payment or transfer: status, a missing incoming payment, a failed transfer, standing orders.",
        "examples": [
            "transfer has not reached",
            "payment failed",
            "cancel my standing order",
            "where is my payment",
        ],
        "department": "Payments",
    },
    {
        "key": "accounts",
        "name": "Accounts",
        "description": "Account servicing: opening, closing, details, contact details, access to online banking.",
        "examples": [
            "close my account",
            "update my address",
            "cannot log in to online banking",
            "open a joint account",
        ],
        "department": "Accounts",
    },
    {
        "key": "loans",
        "name": "Loans and mortgages",
        "description": "A loan or mortgage: repayments, balance, early settlement, a new application.",
        "examples": [
            "early repayment of my loan",
            "mortgage statement",
            "loan application status",
            "change my EMI date",
        ],
        "department": "Lending",
    },
    {
        "key": "statements",
        "name": "Statements and certificates",
        "description": "A copy of a statement, an interest or balance certificate, or a tax document.",
        "examples": [
            "copy of my statement",
            "interest certificate",
            "balance confirmation letter",
            "tax statement",
        ],
        "department": "Accounts",
    },
    {
        "key": "complaints",
        "name": "Complaints",
        "description": "The customer is dissatisfied with a service or outcome and says so, or asks for redress.",
        "examples": [
            "I want to make a complaint",
            "this is unacceptable",
            "third time I am writing",
            "I want compensation",
        ],
        "department": "Customer relations",
        "default_lane": "manual",
        "sensitivity": "restricted",
    },
    {
        "key": "fraud_security",
        "name": "Fraud and security",
        "description": "Suspected fraud, an unrecognised transaction, a scam, a lost or stolen card, account takeover.",
        "examples": [
            "I did not make this transaction",
            "I think I was scammed",
            "my card was stolen",
            "someone accessed my account",
        ],
        "department": "Fraud operations",
        "default_lane": "manual",
        "sensitivity": "restricted",
    },
    {
        "key": "other",
        "name": "Anything else",
        "description": "Mail that fits no other category. A person reads it.",
        "examples": ["general question", "branch opening hours", "who do I contact about"],
        "department": "Customer service",
        "default_lane": "manual",
    },
]

_HARD_STOPS: list[dict[str, Any]] = [
    {
        "key": "regulator_named",
        "label": "Regulator or ombudsman named",
        "keywords": [
            "ombudsman",
            "regulator",
            "central bank",
            "financial conduct authority",
            "banking ombudsman",
        ],
        "question": "Does the customer mention a regulator, an ombudsman or escalating to one?",
    },
    {
        "key": "fraud_or_scam",
        "label": "Fraud or scam",
        "keywords": [
            "fraud",
            "scam",
            "unauthorised",
            "unauthorized",
            "did not make this",
            "phishing",
            "stolen",
        ],
        "question": "Does the customer report fraud, a scam or a transaction they did not make?",
    },
    {
        "key": "legal_action",
        "label": "Legal action threatened",
        "keywords": ["lawyer", "solicitor", "advocate", "legal notice", "court", "sue"],
        "question": "Does the customer threaten legal action or mention a lawyer or court?",
    },
    {
        "key": "vulnerable_customer",
        "label": "Vulnerable customer",
        "keywords": ["bereavement", "passed away", "terminal", "hospital", "cannot cope", "suicide", "carer"],
        "question": "Does the mail suggest the customer is vulnerable (bereavement, illness, distress, financial hardship)?",
    },
    {
        "key": "complaint_escalation",
        "label": "Formal complaint escalation",
        "keywords": ["formal complaint", "escalate", "final response", "nodal officer", "grievance"],
        "question": "Does the customer ask to escalate a complaint or for a final response?",
    },
]


def _starter_flow() -> dict[str, object]:
    """The standard flow with each model node's agent written out, so the studio shows what runs."""
    flow = standard_flow().model_dump()
    for node in flow["nodes"]:
        role = SYSTEM2_NODES.get(node["type"])
        if role:
            name, prompt = DEFAULT_AGENTS[role]
            node["agent"] = {"name": name, "prompt": prompt}
    return flow


def starter_config() -> DeploymentConfig:
    """The starter deployment config. Validated like any other: required safety nodes, known categories."""
    return DeploymentConfig.model_validate(
        {
            "taxonomy": {
                "categories": [{"default_lane": "draft", **c} for c in _CATEGORIES],
                "fallback": "other",
            },
            "rules": {"hard_stops": _HARD_STOPS},
            "flow": _starter_flow(),
            # No automatic lane (D5): confidence can never reach 1.0 after calibration.
            "thresholds": {"auto_min_confidence": 1.0, "draft_min_confidence": 0.8, "escalate_below": 0.75},
        }
    )
