"""Which action template (if any) a category maps to, refined by the thread's wording.

The previous service hard-coded this per query type; it is kept for those names so seeded tenants behave
the same. Any other category uses its configured `actionTemplate`.
"""

from __future__ import annotations

import re

LEGACY_NAMES = frozenset(
    {
        "Stop payment instruction",
        "Statement re-issue",
        "Certificate requests",
        "Interest certificate",
        "Foreclosure quotes",
        "Account maintenance",
        "Disputed transactions",
        "Balance & charge queries",
    }
)


def legacy_template(name: str, text: str) -> str | None:
    t = text.lower()
    if name == "Stop payment instruction":
        return "ACT-STP-014"
    if name == "Statement re-issue":
        return "ACT-CHQ-001" if "cheque book" in t else "ACT-STM-002"
    if name in ("Certificate requests", "Interest certificate"):
        return "ACT-LTR-011" if "balance confirmation" in t else "ACT-CRT-004"
    if name == "Foreclosure quotes":
        return "ACT-LON-007"
    if name == "Account maintenance":
        if re.search(r"registered mobile|mobile number", t) and "otp" in t:
            return "ACT-KYC-021"
        if "cheque book" in t:
            return "ACT-CHQ-001"
        if "standing instruction" in t:
            return "ACT-SI-009"
        return None
    if name == "Disputed transactions":
        if re.search(r"sent twice|duplicate neft", t):
            return "ACT-PAY-025"
        if re.search(r"temporary hold|blocked while travelling", t):
            return "ACT-CRD-008"
        return None
    if name == "Balance & charge queries":
        return "ACT-FEE-017" if "late payment fee" in t and "waive" in t else None
    return None


def template_for(name: str | None, action_template: str | None, text: str) -> str | None:
    if name is None:
        return None
    if name in LEGACY_NAMES:
        return legacy_template(name, text) or action_template
    return action_template
