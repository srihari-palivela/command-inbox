"""The fixed questions the flow asks System 1. The deterministic engine recognises them by identity."""

from __future__ import annotations

CATEGORY_Q = "Which query type best describes what the customer wants?"
MULTI_INTENT_Q = "Does the email ask for two or more separate things that different teams would handle?"
INFORMATIONAL_Q = "Is the customer only asking for information, rather than asking the bank to do something?"
PRIORITY_Q = "How urgent is this email for the bank to handle?"
PRIORITY_LEVELS = ("P1 — critical, today", "P2 — high", "P3 — normal", "P4 — low, informational")
