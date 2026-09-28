"""The node registry: one async function per `NodeType`.

Each node reads state + `RunDeps`, returns a partial state update, appends its explanation span, and records
its decision under `outputs[<node>]`. System 1 answers through `deps.engine`, System 2 through
`deps.providers`; the lane is decided only by `domain.lane.decide_lane` here, never by a model.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig

from command_inbox.agents.config import NodeType
from command_inbox.agents.decision import Calibration, DecisionState, Option
from command_inbox.agents.decision.questions import (
    CATEGORY_Q,
    INFORMATIONAL_Q,
    MULTI_INTENT_Q,
    PRIORITY_LEVELS,
)
from command_inbox.agents.flow.state import FlowState, RunDeps, deps_of
from command_inbox.agents.lexicon import REPEAT_CONTACT_RE, UPSET_RE
from command_inbox.agents.providers import (
    TEMPLATE_FIELDS,
    CustomerContext,
    GroundingDoc,
    TemplateSpec,
    ThreadInput,
    ThreadMessage,
)
from command_inbox.agents.templates import template_for
from command_inbox.domain.lane import LaneInputs, decide_lane
from command_inbox.domain.pii import TOKEN_RE, mask_pii
from command_inbox.domain.priority import PriorityRuleRow, PrioritySignals, rank_priority
from command_inbox.domain.risk import cell_of, chain_for
from command_inbox.domain.sla import sla_budget

Update = dict[str, Any]
NodeFn = Callable[[FlowState, RunnableConfig, dict[str, Any]], Awaitable[Update]]

LANE_NAME = {
    "auto": "Auto — the AI does it",
    "draft": "Draft — you send it",
    "manual": "You — the AI steps back",
}
STANDARD_PRIORITY_KEYS = frozenset({"p1", "p2", "p3", "p4", "p5", "p6", "p7"})
_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
_SEP = "\n␞\n"  # message separator while masking a thread in one pass
DRAFT_MIN_CONFIDENCE = 0.6  # below this an informational query is not worth a draft attempt


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _full_text(deps: RunDeps) -> str:
    """Raw subject + bodies, lower-cased: deterministic rules only (never sent to a model)."""
    return (deps.subject + "\n" + "\n".join(b for _, b in deps.messages)).lower()


def _heuristic(deps: RunDeps) -> bool:
    return deps.engine.name == "heuristic"


def _s1_model(deps: RunDeps) -> str:
    return (
        "rules"
        if _heuristic(deps)
        else f"{deps.engine.name} · {deps.config.models.decision_model or 'default'}"
    )


# ── mask_pii ─────────────────────────────────────────────────────────────────────────────────────────────
async def mask_pii_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    # One pass over the whole thread so a value gets the same token in every message.
    joined = mask_pii(_SEP.join(b for _, b in deps.messages))
    bodies = joined.text.split(_SEP) if deps.messages else []
    subject = mask_pii(deps.subject)
    deps.vault.update(joined.vault)
    for token, value in subject.vault.items():
        deps.vault.setdefault(token, value)
    thread = ThreadInput(
        subject=subject.text,
        messages=tuple(ThreadMessage(s, b) for (s, _), b in zip(deps.messages, bodies, strict=True)),
        customer=CustomerContext(deps.segment, deps.prior_contacts, deps.prior_same_topic),
    )
    matched = (
        f"matched to {deps.customer_facts.split(' · ')[-1]}"
        if deps.customer_facts
        else "not matched to a customer"
    )
    span = deps.span(
        "Mail intake",
        "Assembled the thread and masked PII before any model call",
        f"{_plural(len(deps.messages), 'message')} · {len(deps.vault)} values masked · sender {matched}",
        "ok",
        model="—",
    )
    return {
        "thread": thread,
        "text": thread.text(),
        "sender_verified": deps.sender_verified,
        "spans": [span],
        "outputs": {"mask_pii": {"masked": len(deps.vault)}},
    }


# ── sender_trust ─────────────────────────────────────────────────────────────────────────────────────────
async def sender_trust_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    ok = deps.sender_verified
    span = deps.span(
        "Sender check",
        "Checked the sender's DKIM / SPF / DMARC verdict from intake",
        "Sender authenticated" if ok else "Sender not authenticated — no action will be filled automatically",
        "ok" if ok else "flag",
        model="rules",
    )
    return {"sender_verified": ok, "spans": [span], "outputs": {"sender_trust": {"verified": ok}}}


# ── hard_stop_guard ──────────────────────────────────────────────────────────────────────────────────────
async def hard_stop_guard_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    body = state["text"].lower()
    reasons: list[str] = []
    fired: list[str] = []
    model_used = False
    for stop in deps.config.rules.hard_stops:
        hit = any(k.lower() in body for k in stop.keywords)
        if not hit and stop.question and not _heuristic(deps):
            model_used = True
            p = await deps.engine.boolean(DecisionState(text=state["text"]), stop.question)
            hit = p >= stop.threshold
        if hit:
            fired.append(stop.key)
            reasons.append(stop.label)
    # Third contact is counted from the thread and history, never by a model.
    repeat = bool(REPEAT_CONTACT_RE.search(body)) or deps.prior_same_topic >= 2
    if repeat:
        reasons.append("third contact")
    regulator = any("regulator" in k for k in fired)
    vulnerable = any("vulnerab" in k for k in fired)
    stop_text = " + ".join(reasons) if reasons else None
    sentiment = (
        "vulnerable"
        if vulnerable
        else "escalating"
        if regulator or repeat
        else "upset"
        if UPSET_RE.search(body)
        else "neutral"
    )
    guard = {
        "stop": stop_text,
        "fired": fired,
        "regulator_named": regulator,
        "vulnerable": vulnerable,
        "legal_or_fraud": any(k.startswith(("legal", "fraud", "suspected_fraud")) for k in fired),
        "repeat_contact": repeat,
        "sentiment": sentiment,
    }
    agent = deps.agent("guard")
    span = deps.span(
        agent.name,
        "Screened for hard stop rules before anything else ran",
        f"STOP · {stop_text} — customer-facing generation suspended"
        if stop_text
        else "Clear · no hard stop fired",
        "stop" if stop_text else "ok",
        model=_s1_model(deps) if model_used else "rules",
    )
    span["latency_ms"] = 40 if not model_used else span["latency_ms"]
    return {"guard": guard, "spans": [span], "outputs": {"hard_stop_guard": guard}}


# ── categorise ───────────────────────────────────────────────────────────────────────────────────────────
def _options(deps: RunDeps) -> list[Option]:
    return [
        Option(c.key, c.name, c.description, tuple(c.examples), group=c.department)
        for c in deps.config.taxonomy.categories
    ]


def _override(deps: RunDeps) -> tuple[int, str] | None:
    """First bucket override that matches (deterministic, on the raw text inside the bank's boundary)."""
    text = _full_text(deps)
    sender = deps.sender_email.lower()
    for i, o in enumerate(deps.config.rules.bucket_overrides):
        if not all(w.lower() in text for w in o.requires):
            continue
        v = o.value.lower()
        if (
            (o.match == "sender" and sender == v)
            or (o.match == "domain" and sender.endswith("@" + v.lstrip("@")))
            or (o.match == "phrase" and v in text)
            or (o.match == "regex" and re.search(o.value, text, re.I))
        ):
            return i, o.category
    return None


def _template(deps: RunDeps, key: str | None) -> str | None:
    meta = deps.meta(key)
    if meta is None or meta.is_fallback:
        return None
    code = template_for(meta.category.name, meta.category.action_template, _full_text(deps))
    return code if code in deps.templates else None


async def categorise_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    cfg = deps.config
    fallback = cfg.taxonomy.fallback
    options = _options(deps)
    cal = Calibration(
        temperature=cfg.models.temperature,
        qhat=cfg.models.conformal_qhat,
        coverage=cfg.thresholds.conformal_coverage,
        model=cfg.models.decision_model,
    )
    ds = DecisionState(text=state["text"], hints={"fallback": fallback})
    r = await deps.engine.choice(ds, CATEGORY_Q, options, calibration=cal)
    label = r.label
    names = {o.key: o.label for o in options}
    info_p = await deps.engine.boolean(
        DecisionState(
            text=state["text"],
            hints={"category": names[label], "no_match": "1" if label == fallback else "0"},
        ),
        INFORMATIONAL_Q,
    )
    confidence = r.confidence
    # System 1's choice leads the set, so an adjudicator that cannot tell them apart keeps it.
    pset = [label, *(k for k in r.prediction_set if k != label)]
    rule = _override(deps)
    if rule is not None:
        label = rule[1]
        confidence = max(confidence, 0.94)
        pset = [label]
    confidence = round(confidence, 2)
    escalate = rule is None and (confidence < cfg.thresholds.escalate_below or len(pset) > 1)
    top = sorted(r.distribution.items(), key=lambda kv: -kv[1])[:5]
    choice = {
        "label": label,
        "engine_label": r.label,
        "confidence": confidence,
        "distribution": {k: round(v, 4) for k, v in top},
        "prediction_set": pset,
        "set_probs": {k: r.distribution.get(k, 0.0) for k in pset},
        "evidence": list(r.evidence),
        "intents": list(r.intents),
        "rule": rule[0] + 1 if rule else None,
        "escalate": escalate,
        "escalation_reason": (
            None
            if not escalate
            else f"confidence {confidence:.2f} below {cfg.thresholds.escalate_below:.2f}"
            if confidence < cfg.thresholds.escalate_below
            else f"{len(pset)} labels in the conformal set"
        ),
        "engine": r.engine,
        "degraded": r.degraded,
        "temperature": cal.temperature,
        "qhat": cal.qhat,
    }
    bar = cfg.thresholds.auto_min_confidence
    agent = deps.agent("bucketer")
    shown = "No matching query type" if label == fallback else names[label]
    span = deps.span(
        agent.name,
        f"Bucketing rule {rule[0] + 1} fired, then classified against the taxonomy"
        if rule
        else "Classified the query against the taxonomy",
        f"{shown} · confidence {confidence:.2f} · "
        + ("above the bar" if confidence >= bar else "below the bar, human required"),
        "ok" if confidence >= bar else "flag",
        model=_s1_model(deps),
    )
    span["latency_ms"] = 80 if _heuristic(deps) else max(1, int(r.latency_ms))
    return {
        "choice": choice,
        "category": label,
        "confidence": confidence,
        "escalate": escalate,
        "escalated_unresolved": False,
        "informational": info_p >= 0.5,
        "intents": list(r.intents) or [names[label].lower()],
        "template_code": _template(deps, label),
        "degraded": r.degraded,
        "spans": [span],
        "outputs": {"categorise": choice},
    }


# ── adjudicate ───────────────────────────────────────────────────────────────────────────────────────────
async def adjudicate_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    choice = state["choice"]
    by_name = {c.name: c.key for c in deps.config.taxonomy.categories}
    names = {c.key: c.name for c in deps.config.taxonomy.categories}
    candidates = [names[k] for k in choice["prediction_set"] if k in names]
    agent = deps.agent("adjudicator") if "adjudicator" in deps.agents else deps.agent("bucketer")
    staged = await deps.providers.call(
        "adjudicate", agent, lambda p: p.adjudicate(state["text"], candidates, agent)
    )
    picked = by_name.get(staged.result.label or "")
    update: Update = {}
    if picked is None:
        update["escalated_unresolved"] = True
        output = "Unsure between " + ", ".join(candidates) + " — a person decides"
        status = "flag"
    else:
        conf = state["confidence"]
        if picked != state["category"]:
            # A different label from the set carries System 1's own (lower) probability for it.
            conf = round(choice["set_probs"].get(picked, 0.0), 2)
        update |= {"category": picked, "confidence": conf, "template_code": _template(deps, picked)}
        output = f"Chose {names[picked]} from {_plural(len(candidates), 'candidate')}"
        status = "ok"
    span = deps.span(
        "Adjudicator",
        f"Reviewed an uncertain categorisation ({choice['escalation_reason']})",
        output,
        status,
        usage=staged.usage,
    )
    out = {"candidates": candidates, "picked": picked, "reason": staged.result.reason}
    return {**update, "spans": [span], "outputs": {"adjudicate": out}}


# ── priority ─────────────────────────────────────────────────────────────────────────────────────────────
def _more_urgent(a: str, b: str) -> str:
    return a if _RANK[a] <= _RANK[b] else b


def compute_priority(state: FlowState, deps: RunDeps, model_hint: str | None = None) -> dict[str, Any]:
    guard = state.get("guard") or {}
    extraction = state.get("extraction")
    escalation = bool(guard.get("regulator_named") or guard.get("repeat_contact"))
    provisional = sla_budget(deps.ticket_priority, deps.segment, escalation)
    due = deps.received_at + timedelta(minutes=provisional)
    minutes_left = math.floor((due - deps.now).total_seconds() / 60 + 0.5)  # Math.round semantics
    contacts = max(3, deps.prior_contacts + 1) if guard.get("repeat_contact") else deps.prior_contacts + 1
    signals = PrioritySignals(
        regulator_named=bool(guard.get("regulator_named")),
        vulnerable=bool(guard.get("vulnerable")),
        minutes_left=minutes_left,
        amount_inr=extraction.amount_inr if extraction is not None else None,
        contact_count=contacts,
        informational=bool(state.get("informational")) and not state.get("template_code"),
    )
    rules = deps.config.rules.priority
    rows = [PriorityRuleRow(r.key, r.hard, r.enabled) for r in rules if r.key in STANDARD_PRIORITY_KEYS]
    priority, fired = rank_priority(signals, rows)
    body = (state.get("text") or "").lower()
    hard_fired = any(r.hard for r in rules if r.key in fired)
    for r in rules:
        if r.key in STANDARD_PRIORITY_KEYS or not r.keywords or not (r.hard or r.enabled):
            continue
        if any(k.lower() in body for k in r.keywords):
            fired.append(r.key)
            priority = _more_urgent(priority, r.target.upper())  # type: ignore[assignment]
            hard_fired = hard_fired or r.hard
    if model_hint and not hard_fired:
        priority = _more_urgent(priority, model_hint)  # type: ignore[assignment]
    return {"priority": priority, "fired": fired, "minutes_left": minutes_left, "model_hint": model_hint}


async def priority_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    hint: str | None = None
    if not _heuristic(deps) and params.get("useModel", True):
        sc = await deps.engine.score(DecisionState(text=state["text"]), PRIORITY_LEVELS)
        # The model may raise urgency to P2 at most; P1 is reserved for the hard rules.
        hint = f"P{min(4, max(2, round(sc.expectation)))}"
    result = compute_priority(state, deps, hint)
    fired = result["fired"]
    agent = deps.agent("ranker")
    span = deps.span(
        agent.name if "ranker" in deps.agents else "Priority Ranker",
        "Scored urgency from deadline, sentiment, amount and repeat contacts",
        f"{result['priority']} · rules fired: {', '.join(fired) if fired else 'none (default P3)'}",
        "flag" if result["priority"] == "P1" else "ok",
        model=agent.model if "ranker" in deps.agents else "rules",
    )
    return {"priority": result, "spans": [span], "outputs": {"priority": result}}


# ── multi_intent ─────────────────────────────────────────────────────────────────────────────────────────
async def multi_intent_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    threshold = float(params.get("threshold", 0.5))
    p = await deps.engine.boolean(
        DecisionState(text=state["text"], options=tuple(_options(deps))), MULTI_INTENT_Q
    )
    multi = p >= threshold
    intents = state.get("intents") or []
    if multi and len(intents) < 2:
        intents = [*intents, "a second request"]
    return {
        "multi_intent": multi,
        "intents": intents,
        "outputs": {"multi_intent": {"p": round(p, 3), "multi": multi}},
    }


# ── extract_fields ───────────────────────────────────────────────────────────────────────────────────────
async def extract_fields_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    code = state.get("template_code")
    guard = state.get("guard") or {}
    force = deps.force_lane
    want = (
        bool(code)
        and not state.get("informational")
        and not guard.get("stop")
        and not state.get("multi_intent")
    )
    if not code or not ((want and force not in ("draft", "manual")) or force == "auto"):
        return {"extraction": None, "outputs": {"extract_fields": {"skipped": True}}}
    row = deps.templates[code]
    fields = TEMPLATE_FIELDS.get(code, ("Account number",))
    agent = deps.agent("extractor")
    spec = TemplateSpec(row.code, row.name, fields)
    staged = await deps.providers.call(
        "extract_fields", agent, lambda p: p.extract_fields(state["thread"], spec, agent)
    )
    extraction = staged.result
    # Re-bind masked values inside the bank's boundary, now that no model will see them.
    for f in extraction.fields:
        f.value = TOKEN_RE.sub(lambda m: deps.vault.get(m.group(0), m.group(0)), f.value)
    inferred = sum(1 for f in extraction.fields if f.inferred)
    span = deps.span(
        agent.name,
        f"Filled {code} from the thread and system records",
        f"{len(extraction.fields)} of {len(fields)} fields · {inferred} inferred"
        + ("" if extraction.complete else " · missing fields, a person must complete them"),
        "ok" if extraction.complete else "flag",
        usage=staged.usage,
    )
    update: Update = {
        "extraction": extraction,
        "spans": [span],
        "outputs": {"extract_fields": {"template": code, "complete": extraction.complete}},
    }
    if extraction.amount_inr is not None and state.get("priority"):
        # The amount at stake is a priority signal; re-rank with it (same rules, same order).
        prior = state["priority"]
        update["priority"] = compute_priority(
            {**state, "extraction": extraction}, deps, prior.get("model_hint")
        )
    return update


# ── lane_policy ──────────────────────────────────────────────────────────────────────────────────────────
async def lane_policy_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    th = deps.config.thresholds
    guard = state.get("guard") or {}
    stop = guard.get("stop")
    meta = deps.meta(state.get("category"))
    owned = bool(meta and meta.owned)
    extraction = state.get("extraction")
    code = state.get("template_code")
    has_template = bool(code) and extraction is not None
    confidence = float(state.get("confidence", 0.0))
    multi = bool(state.get("multi_intent"))
    verified = bool(state.get("sender_verified", deps.sender_verified))
    unresolved = bool(state.get("escalated_unresolved"))
    d = decide_lane(
        LaneInputs(
            hard_stop=stop,
            confidence=confidence,
            bar=th.auto_min_confidence if has_template else th.draft_min_confidence,
            query_type_owned=owned,
            multi_intent=multi,
            has_template=has_template,
            fields_complete=bool(extraction and extraction.complete),
            coverage="none",
            informational=bool(state.get("informational")) or not code,
            sender_verified=verified,
            escalated_unresolved=unresolved,
        )
    )
    lane, note = d.lane, d.note
    force = deps.force_lane
    if force and not stop:
        if force == "auto" and extraction is not None and extraction.complete and verified:
            lane = "auto"
        elif force == "draft":
            lane = "draft"
        elif force == "manual":
            lane = "manual"
        note = (
            f"Re-run as {LANE_NAME[lane]} after an override"
            if lane == force
            else f"Override to {force} not possible — {d.note}"
        )
    category_manual = bool(meta and meta.category.default_lane == "manual")
    if category_manual and lane != "manual" and force is None:
        lane, note = "manual", "This query type is always handled by a person"
    want_draft = (
        lane == "draft"
        or (
            lane == "manual"
            and not stop
            and owned
            and not multi
            and not unresolved
            and not category_manual
            and confidence >= DRAFT_MIN_CONFIDENCE
            and not code
        )
    ) and not state.get("degraded")
    decision = {
        "lane": lane,
        "note": note,
        "want_draft": want_draft,
        "owned": owned,
        "sender_verified": verified,
    }
    span = deps.span(
        "Lane policy",
        "Applied the deterministic lane policy",
        f"{LANE_NAME[lane]} · {note}",
        "ok",
        model="rules",
    )
    return {
        "lane": lane,
        "lane_note": note,
        "want_draft": want_draft,
        "spans": [span],
        "outputs": {"lane_policy": decision},
    }


# ── draft_reply ──────────────────────────────────────────────────────────────────────────────────────────
def _grounding(deps: RunDeps, department_id: str | None) -> list[tuple[GroundingDoc, Any]]:
    docs = [d for d in deps.docs if not department_id or d.department_id == department_id][:6]
    return [(GroundingDoc(i + 1, d.title, d.section, d.body), d) for i, d in enumerate(docs)]


async def draft_reply_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    th = deps.config.thresholds
    meta = deps.meta(state.get("category"))
    grounding = _grounding(deps, meta.department_id if meta else None)
    agent = deps.agent("drafter")
    staged = await deps.providers.call(
        "draft_reply",
        agent,
        lambda p: p.draft_reply(state["thread"], [g for g, _ in grounding], agent, deps.customer_name),
    )
    draft = staged.result
    n = len(draft.citations)
    span = deps.span(
        agent.name,
        "Drafted the reply from approved sources only",
        f"{_plural(n, 'citation')} · "
        + {
            "full": "full coverage",
            "partial": "1 gap flagged",
            "none": "no approved source — nothing quotable",
        }[draft.coverage],
        "ok" if draft.coverage == "full" else "flag",
        usage=staged.usage,
    )
    d2 = decide_lane(
        LaneInputs(
            hard_stop=None,
            confidence=float(state.get("confidence", 0.0)),
            bar=th.draft_min_confidence,
            query_type_owned=bool(meta and meta.owned),
            multi_intent=False,
            has_template=False,
            fields_complete=False,
            coverage=draft.coverage,
            informational=True,
        )
    )
    lane, note = state["lane"], state["lane_note"]
    if deps.force_lane == "draft" or d2.lane == "draft":
        lane = "draft"
        note = note if deps.force_lane == "draft" else d2.note
    out = {"coverage": draft.coverage, "citations": draft.citations, "lane": lane}
    return {
        "lane": lane,
        "lane_note": note,
        "draft": draft if lane == "draft" else None,
        "spans": [span],
        "outputs": {"draft_reply": out},
    }


# ── brief ────────────────────────────────────────────────────────────────────────────────────────────────
async def brief_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    meta = deps.meta(state.get("category"))
    multi = bool(state.get("multi_intent"))
    facts = "\n".join(
        x
        for x in (
            f"Customer: {deps.customer_facts}" if deps.customer_facts else None,
            f"Prior contacts: {deps.prior_contacts} ({deps.prior_same_topic} on this topic)",
            f"Owning team: {meta.department_name}"
            if meta and meta.department_name
            else "Owning team: none — query type unowned",
            f"Intents: {' + '.join(state.get('intents') or [])}" if multi else None,
        )
        if x
    )
    agent = deps.agent("summariser")
    staged = await deps.providers.call(
        "brief", agent, lambda p: p.brief(state["thread"], mask_pii(facts).text, agent)
    )
    brief = staged.result
    if multi:
        brief.suggestions.insert(0, {"label": "Split into two child tickets", "meta": "recommended"})
    span = deps.span(
        "Split proposer" if multi else agent.name,
        "Detected two intents owned by different teams"
        if multi
        else "Assembled context for the person taking over",
        "Proposed split into two child tickets · held for your decision"
        if multi
        else f"Brief attached · {_plural(len(brief.context), 'context item')} · no customer-facing text generated",
        "flag" if multi else "ok",
        usage=staged.usage,
    )
    return {"brief": brief, "spans": [span], "outputs": {"brief": {"context": len(brief.context)}}}


# ── route ────────────────────────────────────────────────────────────────────────────────────────────────
async def route_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    """Which team owns the ticket. The person is picked when the outcome commits (live load, clearance)."""
    deps = deps_of(config)
    meta = deps.meta(state.get("category"))
    route = {
        "department_id": meta.department_id if meta else None,
        "department": meta.department_name if meta else None,
    }
    return {"route": route, "outputs": {"route": route}}


# ── approval_gate ────────────────────────────────────────────────────────────────────────────────────────
async def approval_gate_node(state: FlowState, config: RunnableConfig, params: dict[str, Any]) -> Update:
    deps = deps_of(config)
    lane, note = state.get("lane", "manual"), state.get("lane_note", "")
    draft = state.get("draft")
    guard = state.get("guard") or {}
    code = state.get("template_code")
    chain: str | None = None
    spans = []
    if lane == "auto" and code:
        t = deps.templates[code]
        cell = cell_of(t.reversible, t.money_moves)
        chain = chain_for(cell, t.approval, deps.dial.get(cell, 1))
        who = (
            "the AI may act alone"
            if chain == "auto"
            else "maker + checker"
            if chain == "dual"
            else "one approver"
        )
        spans.append(
            deps.span(
                "Policy engine",
                "Looked up the action’s risk cell and approval route",
                f"{'Can be undone' if t.reversible else 'Cannot be undone'} · "
                f"{'Money moves' if t.money_moves else 'No money moves'} → {who}",
                "flag" if chain == "dual" else "ok",
                model="rules",
            )
        )
        note = {
            "auto": "Can be undone — the AI already did it",
            "dual": "Filled in, waiting on two approvers",
        }.get(chain, "Filled in, waiting on one approver")
    degraded = bool(state.get("degraded")) or deps.engine.degraded or deps.providers.degraded
    if degraded:
        lane, note, chain, draft = (
            "manual",
            "AI partly unavailable — handed to a person with the raw thread",
            None,
            None,
        )
    # Invariants the approval gateway relies on, whatever the flow did before.
    if lane != "manual" and guard.get("stop"):
        lane, note, chain, draft = "manual", f"Held back — {guard['stop']}", None, None
    if lane == "auto" and not state.get("sender_verified", deps.sender_verified):
        lane, note, chain = "manual", "Sender could not be verified — a person checks before any action", None
    if lane != "draft":
        draft = None
    out = {"lane": lane, "note": note, "chain": chain, "degraded": degraded}
    return {
        "lane": lane,
        "lane_note": note,
        "chain": chain,
        "draft": draft,
        "degraded": degraded,
        "spans": spans,
        "outputs": {"approval_gate": out},
    }


NODE_REGISTRY: dict[NodeType, NodeFn] = {
    "mask_pii": mask_pii_node,
    "sender_trust": sender_trust_node,
    "hard_stop_guard": hard_stop_guard_node,
    "categorise": categorise_node,
    "adjudicate": adjudicate_node,
    "priority": priority_node,
    "multi_intent": multi_intent_node,
    "extract_fields": extract_fields_node,
    "lane_policy": lane_policy_node,
    "draft_reply": draft_reply_node,
    "brief": brief_node,
    "route": route_node,
    "approval_gate": approval_gate_node,
}
