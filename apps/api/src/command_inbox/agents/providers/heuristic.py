"""Deterministic System 2, ported from the TypeScript heuristic provider.

Used in tests, offline development, and as the degraded path when the model provider is unavailable. It is
intentionally conservative: it never invents facts, cites only supplied sources, and when unsure it says so.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from command_inbox.agents.lexicon import SIGNATURE_BY_NAME, phrase_hits
from command_inbox.agents.providers.types import (
    Adjudication,
    AgentSpec,
    BriefResult,
    DraftResult,
    ExtractedField,
    ExtractResult,
    GroundingDoc,
    Staged,
    TemplateSpec,
    ThreadInput,
    Usage,
)

_AMOUNT = re.compile(r"₹\s?([\d,]+(?:\.\d{2})?)")
_ACCOUNT = re.compile(r"(?:account|a/c)[^\d]{0,30}(\d{4})\b", re.I)
_ENDING = re.compile(r"ending\s+(\d{4})", re.I)
_CHEQUE = re.compile(r"cheque\s*(?:no\.?|number)?\s*(\d{6})", re.I)
_DATE = re.compile(r"(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)", re.I)
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_PERIOD_FROM = re.compile(rf"({_MONTHS})\s+to", re.I)
_PERIOD_TO = re.compile(rf"to\s+({_MONTHS})", re.I)
_WORDS = re.compile(r"[a-z]{4,}")

GAP_LINE = (
    "On the remaining part of your question, I do not yet have an approved position I can commit to in "
    "writing; I have raised it with the owning team and will revert."
)
NO_SOURCE = "I do not yet have approved guidance I can quote on this"


def _staged(result: Any, latency_ms: int) -> Staged[Any]:
    return Staged(result, Usage(model="rules", tokens=None, cost_minor=None, latency_ms=latency_ms))


def _inr(amount: float) -> str:
    """`₹{toLocaleString('en-IN')}.00`, exactly as the previous service rendered amounts (12,34,567)."""
    whole = str(int(amount))
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    decimals = f"{amount:.3f}".split(".")[1].rstrip("0")
    return "₹" + ",".join([*groups, tail]) + (f".{decimals}" if decimals else "") + ".00"


class HeuristicProvider:
    name: Literal["heuristic"] = "heuristic"

    async def extract_fields(
        self, thread: ThreadInput, template: TemplateSpec, agent: AgentSpec
    ) -> Staged[ExtractResult]:
        raw = thread.subject + "\n" + "\n".join(m.body for m in thread.messages)
        amount_m = _AMOUNT.search(raw)
        amount_inr = float(amount_m.group(1).replace(",", "")) if amount_m else None
        acct = _ACCOUNT.search(raw) or _ENDING.search(raw)
        cheque = _CHEQUE.search(raw)
        date = _DATE.search(raw)
        fields: list[ExtractedField] = []
        for label in template.fields:
            value: str | None = None
            source, inferred = "email body", False
            low = label.lower()
            if re.search(r"account|loan account|card number", low):
                value = f"••••{acct.group(1)}" if acct else None
                source = "customer record"
            elif "cheque number" in low:
                value = cheque.group(1) if cheque else None
            elif "amount" in low:
                value = _inr(amount_inr) if amount_inr is not None else None
            elif "date" in low:
                value = date.group(1) if date else None
            elif "reason code" in low:
                value = (
                    "CONTRACT_TERMINATED"
                    if re.search(r"terminat|cancel", raw, re.I)
                    else "INSTRUMENT_LOST"
                    if re.search(r"lost|stolen", raw, re.I)
                    else None
                )
                source, inferred = "inferred · 0.80", True
            elif "requested by" in low:
                value = thread.messages[0].sender if thread.messages else None
                source = "sender"
            elif "delivery" in low:
                value, source = "Registered email", "policy default"
            elif "charge" in low:
                value, source = "Waived · first request", "fee schedule"
            elif "format" in low:
                value = "PDF, unsecured" if re.search(r"password|unlocked|unsecured", raw, re.I) else "PDF"
            elif "period from" in low:
                m = _PERIOD_FROM.search(raw)
                value = f"01-{m.group(1)[:3]}-2026" if m else None
            elif "period to" in low:
                m = _PERIOD_TO.search(raw)
                value = f"end of {m.group(1)}" if m else None
            elif "leaves" in low:
                m = re.search(r"(\d+)\s+leaves", raw, re.I)
                value = m.group(1) if m else None
            elif "financial year" in low:
                m = re.search(r"fy\s*(\d{4}-\d{2})", raw, re.I)
                value = m.group(1) if m else None
            if value is not None:
                fields.append(ExtractedField(label, value, source, inferred))
        return _staged(ExtractResult(fields, len(fields) == len(template.fields), amount_inr), 70)

    async def draft_reply(
        self, thread: ThreadInput, docs: list[GroundingDoc], agent: AgentSpec, customer_name: str
    ) -> Staged[DraftResult]:
        body = thread.text().lower()
        q = set(_WORDS.findall(body))
        scored = []
        for d in docs:
            w = set(_WORDS.findall((d.title + " " + d.section + " " + d.body).lower()))
            overlap = len(q & w)
            if overlap >= 3:
                scored.append((d, overlap))
        scored.sort(key=lambda x: -x[1])
        scored = scored[:2]
        salutation = f"Dear {customer_name},"
        if not scored:
            return _staged(
                DraftResult(
                    body=f"{salutation}\n\nThank you for writing in. {NO_SOURCE}, so I have asked the owning "
                    "team and will come back to you with a definitive answer.\n\nWarm regards,",
                    citations=[],
                    flagged=[NO_SOURCE],
                    coverage="none",
                    gap_question=thread.subject,
                ),
                120,
            )
        paragraphs = [" ".join(re.split(r"(?<=\.)\s+", d.body)[:2]) + f" [{d.n}]" for d, _ in scored]
        partial = body.count("?") > len(scored)
        parts = [salutation, "Thank you for writing in.", *paragraphs, *([GAP_LINE] if partial else [])]
        return _staged(
            DraftResult(
                body="\n\n".join([*parts, "Warm regards,"]),
                citations=[d.n for d, _ in scored],
                flagged=[GAP_LINE] if partial else [],
                coverage="partial" if partial else "full",
                gap_question=thread.subject if partial else None,
            ),
            140,
        )

    async def brief(self, thread: ThreadInput, facts: str, agent: AgentSpec) -> Staged[BriefResult]:
        first = thread.messages[0].body if thread.messages else ""
        summary = first if len(first) <= 280 else first[:277] + "…"
        context = []
        for line in facts.split("\n"):
            parts = line.split(": ")
            if len(parts) == 2:
                context.append({"label": parts[0], "value": parts[1]})
        prior = thread.customer.prior_contacts
        return _staged(
            BriefResult(
                summary=(f"Contact {prior + 1} from this customer. " if prior else "") + summary,
                context=context,
                suggestions=[
                    {"label": "Call the customer and agree a dated next step", "meta": "recommended"},
                    {"label": "Reply with an acknowledgement in your own words", "meta": "you write it"},
                ],
            ),
            90,
        )

    async def adjudicate(
        self, text: str, candidate_labels: list[str], agent: AgentSpec
    ) -> Staged[Adjudication]:
        """Re-score only the candidates on the keyword signals; ties keep the order given (most likely first)."""
        if not candidate_labels:
            return _staged(Adjudication(None, "no candidates"), 20)
        body = " " + text.lower() + " "
        best, best_score = candidate_labels[0], 0
        for label in candidate_labels:
            sig = SIGNATURE_BY_NAME.get(label)
            if sig is None:
                continue
            score = phrase_hits(body, label, sig.any, sig.strong).score
            if score > best_score:
                best, best_score = label, score
        return _staged(Adjudication(best, "keyword signals" if best_score else "most likely candidate"), 20)

    async def copilot_answer(self, question: str, facts: str, model: str) -> dict[str, Any] | None:
        return None
