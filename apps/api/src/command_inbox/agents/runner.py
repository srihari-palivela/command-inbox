"""The triage job: load a ticket and its deployment version, run the compiled flow, commit the outcome.

Model calls run outside any database transaction; the outcome (ticket fields, gate artefacts, explanation
rows, routing, audit and live-update events) commits in one tenant transaction, exactly as the previous
service wrote it. Re-running a job is harmless: only a ticket still in `triaging` is touched, checked again
under a row lock at commit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.budget import monthly_remaining, record_spend
from command_inbox.agents.config import DeploymentConfig
from command_inbox.agents.decision import ResilientEngine, engine_from_settings
from command_inbox.agents.flow import CategoryMeta, DocRow, RunDeps, TemplateRow, compile_flow, run_flow
from command_inbox.agents.flow.nodes import LANE_NAME
from command_inbox.agents.providers import ProviderRouter
from command_inbox.agents.specs import agent_specs, catalogue_agent
from command_inbox.agents.synthesis import FALLBACK_NAME, synthesise_config
from command_inbox.agents.tracing import flush, run_span, set_output
from command_inbox.core import jobs, outbox
from command_inbox.core.audit import Feed
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import AI_ACTOR
from command_inbox.core.crypto import canonical_json, sha256
from command_inbox.core.jobs import JobHandler, JobRow, Worker
from command_inbox.core.telemetry import lane_decisions
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import (
    ActionInstance,
    ActionTemplate,
    Agent,
    AgentBoard,
    AutonomyDial,
    Brief,
    BucketRule,
    Customer,
    Department,
    Deployment,
    DeploymentVersion,
    Draft,
    GapTicket,
    KnowledgeDoc,
    Mailbox,
    Message,
    Org,
    PredictionOutcome,
    PriorityRule,
    QueryType,
    Subtask,
    Ticket,
    TicketLink,
    TraceSpan,
    TriageRun,
)
from command_inbox.domain.sla import sla_budget
from command_inbox.modules.people.routing import pick_assignee
from command_inbox.modules.tickets.ops import (
    lock_ticket,
    next_number,
    record_ticket_event,
    system_note,
    update_ticket,
)

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class Loaded:
    ticket: Ticket
    deps: RunDeps
    version_id: str | None
    deployment_id: str | None
    config_hash: str
    bucket_agent: str


# ── loading ──────────────────────────────────────────────────────────────────────────────────────────────
async def _deployment_config(
    tx: AsyncSession, t: Ticket
) -> tuple[DeploymentConfig, str | None, str | None, str] | None:
    """The ticket's own version, else its mailbox deployment's active version. None when there is none."""
    version: DeploymentVersion | None = None
    if t.deployment_version_id:
        version = await tx.get(DeploymentVersion, t.deployment_version_id)
    if version is None:
        deployment_id = t.deployment_id
        if deployment_id is None and t.mailbox_id:
            mb = await tx.get(Mailbox, t.mailbox_id)
            deployment_id = mb.deployment_id if mb else None
        if deployment_id:
            dep = await tx.get(Deployment, deployment_id)
            if dep is not None and dep.active_version_id:
                version = await tx.get(DeploymentVersion, dep.active_version_id)
    if version is None:
        return None
    dep = await tx.get(Deployment, version.deployment_id)
    config = DeploymentConfig.model_validate(version.config)
    label = f"{dep.key if dep else 'deployment'}@v{version.version}"
    return config, str(version.id), str(version.deployment_id), label


async def load(org_id: str, ticket_id: str, payload: dict[str, Any]) -> Loaded | None:
    async with tenant_tx(org_id) as tx:
        t = (
            await tx.execute(select(Ticket).where(Ticket.org_id == org_id, Ticket.id == ticket_id))
        ).scalar_one_or_none()
        if t is None or t.status != "triaging":
            return None
        msgs = (
            (
                await tx.execute(
                    select(Message)
                    .where(
                        Message.org_id == org_id,
                        Message.ticket_id == ticket_id,
                        Message.direction == "inbound",
                    )
                    .order_by(Message.sent_at)
                )
            )
            .scalars()
            .all()
        )
        org = (await tx.execute(select(Org).where(Org.id == org_id))).scalar_one()
        allowed_providers = list(org.allowed_providers or [])
        month_left = await monthly_remaining(tx, org)
        depts = {
            str(d.id): d.name
            for d in (await tx.execute(select(Department).where(Department.org_id == org_id))).scalars()
        }
        qts = list(
            (
                await tx.execute(select(QueryType).where(QueryType.org_id == org_id).order_by(QueryType.sort))
            ).scalars()
        )
        agent_rows = list((await tx.execute(select(Agent).where(Agent.org_id == org_id))).scalars())
        on_board: set[str] = set()
        if t.board_id:
            on_board = {
                str(a)
                for a in (
                    await tx.execute(
                        select(AgentBoard.agent_id).where(
                            AgentBoard.org_id == org_id, AgentBoard.board_id == t.board_id
                        )
                    )
                ).scalars()
            }
        templates = {
            r.code: TemplateRow(str(r.id), r.code, r.name, r.reversible, r.money_moves, r.approval)
            for r in (
                await tx.execute(select(ActionTemplate).where(ActionTemplate.org_id == org_id))
            ).scalars()
        }
        dial = {
            r.cell: r.level
            for r in (await tx.execute(select(AutonomyDial).where(AutonomyDial.org_id == org_id))).scalars()
        }
        prior: list[Any] = []
        customer = None
        if t.customer_id:
            prior = list(
                (
                    await tx.execute(
                        select(Ticket.query_type_id).where(
                            Ticket.org_id == org_id, Ticket.customer_id == t.customer_id, Ticket.id != t.id
                        )
                    )
                ).scalars()
            )
            customer = await tx.get(Customer, t.customer_id)
        docs = [
            DocRow(
                str(d.id),
                d.title,
                d.section,
                d.body,
                d.owner,
                str(d.department_id) if d.department_id else None,
                d.verified_at,
            )
            for d in (
                await tx.execute(
                    select(KnowledgeDoc).where(
                        KnowledgeDoc.org_id == org_id, KnowledgeDoc.status.in_(("approved", "stale"))
                    )
                )
            ).scalars()
        ]
        found = await _deployment_config(tx, t)
        qt_by_key: dict[str, str] = {}
        if found is None:
            bucket_rules = list(
                (await tx.execute(select(BucketRule).where(BucketRule.org_id == org_id))).scalars()
            )
            priority_rules = list(
                (await tx.execute(select(PriorityRule).where(PriorityRule.org_id == org_id))).scalars()
            )
            syn = synthesise_config(
                confidence_bar=org.confidence_bar,
                departments=depts,
                query_types=qts,
                bucket_rules=bucket_rules,
                priority_rules=priority_rules,
            )
            config, version_id, deployment_id, label = syn.config, None, None, "setup"
            qt_by_key = syn.query_type_by_key
        else:
            config, version_id, deployment_id, label = found

    dept_by_name = {v: k for k, v in depts.items()}
    qt_by_name = {q.name: q for q in qts}
    categories: dict[str, CategoryMeta] = {}
    for c in config.taxonomy.categories:
        if c.key == config.taxonomy.fallback:
            categories[c.key] = CategoryMeta(c, None, None, None, False, FALLBACK_NAME, is_fallback=True)
            continue
        qt = next((q for q in qts if str(q.id) == qt_by_key.get(c.key)), None) or qt_by_name.get(c.name)
        dept_id = str(qt.department_id) if qt and qt.department_id else None
        if qt is None:
            dept_id = dept_by_name.get(c.department)
        categories[c.key] = CategoryMeta(
            category=c,
            query_type_id=str(qt.id) if qt else None,
            department_id=dept_id,
            department_name=depts.get(dept_id or ""),
            owned=dept_id is not None,
            bucket=qt.name if qt else c.name,
        )

    catalogue = {
        role: spec
        for role in ("guard", "bucketer", "adjudicator", "extractor", "drafter", "summariser", "ranker")
        if (spec := catalogue_agent(agent_rows, on_board, role)) is not None
    }
    agents = agent_specs(config, catalogue)
    prior_same = sum(1 for q in prior if q and q == t.query_type_id)
    # Intake's DKIM/SPF/DMARC verdict: the job payload, else what the ticket recorded (re-runs).
    if "senderVerified" in payload:
        sender_verified = payload.get("senderVerified") is True
    else:
        sender_verified = bool(getattr(t, "sender_verified", False))
    deps = RunDeps(
        config=config,
        deployment=label,
        engine=ResilientEngine(engine_from_settings()),
        providers=ProviderRouter(
            budget_minor=config.models.max_cost_minor_per_mail,
            allowed=allowed_providers,
            monthly_remaining_minor=month_left,
        ),
        ticket_id=str(t.id),
        org_id=org_id,
        subject=t.subject,
        messages=[(m.from_name, m.body) for m in msgs],
        sender_email=t.from_email,
        customer_name=t.from_name,
        customer_facts=(
            f"{customer.name} · {customer.cif or 'unmatched sender, no customer record'}"
            if customer
            else None
        ),
        segment=t.segment,
        ticket_priority=t.priority,
        received_at=t.received_at,
        now=clock.now(),
        prior_contacts=len(prior),
        prior_same_topic=prior_same,
        categories=categories,
        templates=templates,
        dial=dial,
        docs=docs,
        agents=agents,
        sender_verified=sender_verified,
        force_lane=payload.get("forceLane") if payload.get("forceLane") in LANE_NAME else None,
        retrieve=_retriever(org_id),
    )
    bucketer = agents.get("bucketer")
    return Loaded(
        t, deps, version_id, deployment_id, config.config_hash(), bucketer.name if bucketer else "bucketer"
    )


# ── the job ──────────────────────────────────────────────────────────────────────────────────────────────
async def run_triage_job(job: JobRow) -> None:
    ticket_id = str(job.payload["ticketId"])
    loaded = await load(job.org_id, ticket_id, job.payload)
    if loaded is None:
        return  # gone, or already triaged: idempotent
    deps = loaded.deps
    started = time.perf_counter()
    graph = compile_flow(deps.config, loaded.version_id)
    with run_span(
        "triage",
        ticket_id=ticket_id,
        org_id=job.org_id,
        deployment=deps.deployment,
        input_text=f"QRY-{loaded.ticket.number}",
    ) as span:
        state = await run_flow(graph, deps)
        set_output(
            span,
            {
                "lane": state.get("lane"),
                "category": state.get("category"),
                "confidence": state.get("confidence"),
            },
        )
    flush()
    await record_spend(job.org_id, deps.providers)
    lane = await commit(job, loaded, dict(state), int((time.perf_counter() - started) * 1000))
    if lane:
        lane_decisions.labels(deps.deployment, lane).inc()
        log.info(
            "triage complete", ticket_id=ticket_id, lane=lane, ms=int((time.perf_counter() - started) * 1000)
        )


def _reasoning(
    state: dict[str, Any], bar: float, owned: bool, degraded: bool, template: TemplateRow | None
) -> str:
    if degraded:
        return (
            "The model provider was unavailable for part of this run, so nothing customer-facing was generated. "
            "The thread is with a person, unaltered."
        )
    guard = state.get("guard") or {}
    choice = state.get("choice") or {}
    conf = float(state.get("confidence", 0.0))
    intents = state.get("intents") or []
    phrases = choice.get("evidence") or []
    parts: list[str] = []
    if guard.get("stop"):
        parts.append(
            f"A hard stop rule fired ({guard['stop']}), so the agent will not draft customer-facing content and "
            "has assembled context for you instead."
        )
    if state.get("multi_intent"):
        parts.append(
            f"The email carries {len(intents)} intents ({' + '.join(intents)}), owned by different teams — the "
            "agent proposes a split rather than guessing a primary intent."
        )
    if not owned:
        parts.append(
            "No team owns this query type yet, so it cannot be automated and goes to a person every time."
        )
    level = "high" if conf >= 0.9 else "moderate" if conf >= bar else "low"
    driven = (", driven by " + ", ".join(f"“{p}”" for p in phrases)) if phrases else ""
    parts.append(
        f"Classified with {level} confidence ({conf:.2f}), {'above' if conf >= bar else 'below'} the "
        f"{bar:.2f} bar{driven}."
    )
    adj = (state.get("outputs") or {}).get("adjudicate")
    if adj:
        parts.append(
            "The fast classifier was unsure, so a reviewing model "
            + (
                f"chose from {len(adj['candidates'])} candidates."
                if adj.get("picked")
                else "looked at it and could not decide — a person makes the call."
            )
        )
    lane, chain, draft = state.get("lane"), state.get("chain"), state.get("draft")
    if lane == "auto" and template:
        parts.append(
            "The action can be undone and no money moves — the one risk group where the AI is allowed to act "
            "alone. It executes now and is reviewed afterwards."
            if chain == "auto"
            else f"{'The action can be undone' if template.reversible else 'This one cannot be undone'} and "
            f"{'money moves' if template.money_moves else 'no money moves'}, so it stops here for "
            f"{'your approval and a second approver' if chain == 'dual' else 'your approval'} rather than acting "
            "on its own."
        )
    if lane == "draft" and draft is not None:
        parts.append(
            "The draft is grounded only in knowledge-manager-approved material, with every policy statement cited."
            if draft.coverage == "full"
            else "Only part of the question has approved coverage: the answerable part is drafted, the gap is "
            "marked in writing rather than filled, and a gap ticket was raised. Check the flagged paragraph "
            "before sending."
        )
    return " ".join(parts)


def _retriever(org_id: str) -> Any:
    async def retrieve_rows(query: str, department_id: str | None) -> list[DocRow]:
        from command_inbox.knowledge.retrieve import retrieve

        async with tenant_tx(org_id) as tx:
            hits = await retrieve(tx, org_id, query, department_id=department_id, k=6)
        return [
            DocRow(
                h.doc_id,
                h.title,
                h.section,
                h.text,
                h.owner,
                h.department_id,
                h.verified_at,
                chunk_id=h.chunk_id,
            )
            for h in hits
        ]

    return retrieve_rows


async def commit(job: JobRow, loaded: Loaded, state: dict[str, Any], total_ms: int) -> str | None:
    deps = loaded.deps
    org_id = job.org_id
    ticket_id = str(loaded.ticket.id)
    lane: str = state.get("lane") or "manual"
    lane_note: str = state.get("lane_note") or ""
    chain: str | None = state.get("chain")
    draft = state.get("draft")
    brief = state.get("brief")
    extraction = state.get("extraction")
    guard = state.get("guard") or {}
    choice = state.get("choice") or {}
    meta = deps.meta(state.get("category"))
    owned = bool(meta and meta.owned)
    department_id = meta.department_id if meta else None
    department_name = meta.department_name if meta else None
    confidence = float(state.get("confidence", 0.0))
    bar = deps.config.thresholds.auto_min_confidence
    multi = bool(state.get("multi_intent"))
    intents = state.get("intents") or []
    code = state.get("template_code")
    template = deps.templates.get(code) if code else None
    degraded = bool(state.get("degraded"))
    prio = state.get("priority") or {}
    priority = prio.get("priority") or loaded.ticket.priority
    escalation = bool(guard.get("regulator_named") or guard.get("repeat_contact"))
    bucket = meta.bucket if meta else FALLBACK_NAME
    spans = list(state.get("spans") or [])
    # Exactly the passages the draft was written from (citation numbers index into this list).
    grounding = list(state.get("grounding") or [])

    async with tenant_tx(org_id) as tx:
        locked = await lock_ticket(tx, org_id, ticket_id)
        if locked.status != "triaging":
            return None

        assignee = (
            None
            if chain == "auto"
            else await pick_assignee(tx, org_id, department_id, choice_label(meta) or "this query", None)
        )
        spans.append(
            deps.span(
                "Router",
                "Placed the ticket with the right person at the right position",
                f"Assigned to {assignee['name']} · clearance-checked · position by {priority}"
                if assignee
                else "Owned by the AI · auto-execution cell"
                if chain == "auto"
                else "No cleared person available — left unassigned for the team lead",
                "ok" if assignee or chain == "auto" else "flag",
                model="rules",
            )
        )
        runs = (
            await tx.execute(
                select(func.count())
                .select_from(TriageRun)
                .where(TriageRun.org_id == org_id, TriageRun.ticket_id == ticket_id)
            )
        ).scalar_one()
        trace_id = f"TRC-{locked.number}-{runs + 1:02d}"
        cost = sum(s["cost_minor"] or 0 for s in spans)
        phrases = choice.get("evidence") or []
        evidence: list[dict[str, str]] = [
            {
                "tag": "INTENT",
                "quote": f'"{phrases[0]}"' if phrases else (" + ".join(intents) or "no clear intent"),
                "why": "more than one intent" if multi else "drove the classification",
            }
        ]
        if guard.get("stop"):
            evidence.append({"tag": "POLICY", "quote": guard["stop"], "why": "hard stop rule"})
        if extraction is not None:
            evidence.append(
                {
                    "tag": "ENTITY",
                    "quote": " · ".join(f.value for f in extraction.fields[:3]),
                    "why": "all mandatory fields present" if extraction.complete else "some fields missing",
                }
            )
        if draft is not None:
            n = len(draft.citations)
            evidence.append(
                {
                    "tag": "MATCH" if draft.coverage == "full" else "GAP",
                    "quote": f"{n} approved source{'' if n == 1 else 's'} cover the answer"
                    if draft.coverage == "full"
                    else "part of the question has no approved source",
                    "why": "approved source" if draft.coverage == "full" else "gap ticket raised",
                }
            )
        provider = "degraded" if degraded else deps.providers.name
        run = TriageRun(
            org_id=org_id,
            ticket_id=ticket_id,
            trace_id=trace_id,
            reasoning=_reasoning(state, bar, owned, degraded, template),
            evidence=evidence,
            confidence=confidence,
            lane=lane,
            latency_ms=total_ms,
            cost_minor=cost,
            provider=provider,
        )
        tx.add(run)
        await tx.flush()
        tx.add_all([TraceSpan(org_id=org_id, run_id=run.id, seq=i + 1, **s) for i, s in enumerate(spans)])

        links: list[dict[str, Any]] = []
        status = "with_human" if lane == "manual" else "awaiting_approval"

        if lane == "auto" and template and extraction is not None and chain:
            fields = [f.as_json() for f in extraction.fields]
            key = sha256(
                canonical_json(
                    {
                        "orgId": org_id,
                        "code": template.code,
                        "fields": [[f["label"], f["value"]] for f in fields],
                    }
                )
            )
            existing = (
                await tx.execute(
                    select(ActionInstance.id).where(
                        ActionInstance.org_id == org_id, ActionInstance.idempotency_key == key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                # Same instruction already exists: never create a second executable copy.
                status, lane, lane_note = (
                    "with_human",
                    "manual",
                    "Duplicate of an existing instruction — held for a person",
                )
            else:
                ai = ActionInstance(
                    org_id=org_id,
                    ticket_id=ticket_id,
                    template_id=template.id,
                    account_ref=next((f["value"] for f in fields if _is_account(f["label"])), ""),
                    fields=fields,
                    validation="All mandatory fields extracted. Verified against the customer record where available."
                    if extraction.complete
                    else "Some fields are missing.",
                    state="scheduled" if chain == "auto" else "drafted",
                    chain=chain,
                    idempotency_key=key,
                    execute_after=clock.now() if chain == "auto" else None,
                )
                tx.add(ai)
                await tx.flush()
                if chain == "auto":
                    status = "executing"
                    await jobs.enqueue(
                        tx,
                        org_id,
                        "execute_action",
                        {"actionId": str(ai.id)},
                        dedupe_key=f"exec:{ai.id}:auto",
                    )
                links.append(
                    {"kind": "ACTION", "label": f"{template.code} · {template.name}", "ref": template.code}
                )

        if lane == "draft" and draft is not None:
            by_n = {i + 1: d for i, d in enumerate(grounding)}
            cites = []
            body = draft.body
            for i, n in enumerate(draft.citations):
                g = by_n[n]
                cites.append(
                    {
                        "n": i + 1,
                        "docId": g.id,
                        "chunkId": g.chunk_id,
                        "doc": g.title,
                        "section": g.section,
                        "verifiedAt": iso_ms(g.verified_at or clock.now()),
                        "owner": g.owner,
                    }
                )
                body = body.replace(f"[{n}]", f"[§{i + 1}]")
            body = body.replace("[§", "[")
            await tx.execute(
                insert(Draft)
                .values(
                    org_id=org_id,
                    ticket_id=ticket_id,
                    subject=f"Re: {locked.subject}",
                    to_addr=locked.from_email,
                    original_body=body,
                    current_body=body,
                    citations=cites,
                    flagged=draft.flagged,
                    state="draft",
                )
                .on_conflict_do_update(
                    index_elements=[Draft.ticket_id],
                    set_={
                        "original_body": body,
                        "current_body": body,
                        "citations": cites,
                        "flagged": draft.flagged,
                        "state": "draft",
                        "updated_at": clock.now(),
                    },
                )
            )
            links += [
                {"kind": "SOURCE", "label": f"{c['doc']} {c['section']}", "ref": c["docId"]} for c in cites
            ]
            if draft.coverage != "full" and draft.gap_question:
                n = await next_number(tx, org_id, "gap")
                tx.add(
                    GapTicket(
                        org_id=org_id,
                        number=n,
                        severity="blocking",
                        question=draft.gap_question,
                        detail=f"Raised from QRY-{locked.number}: the drafter could not ground this part of the "
                        "answer in approved content.",
                        hits=1,
                        owner=department_name or "Unassigned",
                        state="Needs content",
                        cta="Write content",
                    )
                )
                gap = f"GAP-{n:04d}"
                links.append({"kind": "GAP", "label": f"{gap} · raised from this ticket", "ref": gap})
                await record_ticket_event(
                    tx,
                    locked,
                    actor=AI_ACTOR,
                    action="gap.raised",
                    summary=f"Raised knowledge gap {gap} · {draft.gap_question[:80]}",
                    feed=Feed("flag", f"QRY-{locked.number}"),
                )

        if lane == "manual" and brief is not None:
            why = f"Hard stop · {guard['stop']}" if guard.get("stop") else lane_note
            await tx.execute(
                insert(Brief)
                .values(
                    org_id=org_id,
                    ticket_id=ticket_id,
                    why=why,
                    summary=brief.summary,
                    context=brief.context,
                    suggestions=brief.suggestions,
                )
                .on_conflict_do_update(
                    index_elements=[Brief.ticket_id],
                    set_={
                        "summary": brief.summary,
                        "context": brief.context,
                        "suggestions": brief.suggestions,
                    },
                )
            )
            links.append({"kind": "POLICY", "label": why, "ref": None})
        links.append({"kind": "AUDIT", "label": f"AUD-{locked.number}", "ref": None})
        await tx.execute(
            delete(TicketLink).where(
                TicketLink.org_id == org_id,
                TicketLink.ticket_id == ticket_id,
                TicketLink.kind.in_(("ACTION", "SOURCE", "POLICY", "AUDIT")),
            )
        )
        tx.add_all(
            [TicketLink(org_id=org_id, ticket_id=ticket_id, sort=i, **link) for i, link in enumerate(links)]
        )

        await tx.execute(delete(Subtask).where(Subtask.org_id == org_id, Subtask.ticket_id == ticket_id))
        tx.add_all(
            [
                Subtask(
                    org_id=org_id, ticket_id=ticket_id, key=k, label=label, owner=owner, done=done, sort=i
                )
                for i, (k, label, owner, done) in enumerate(
                    _subtasks(lane, chain, draft, multi, bool(guard.get("regulator_named")))
                )
            ]
        )

        level = "high" if confidence >= 0.9 else "moderate" if confidence >= bar else "low"
        await system_note(
            tx,
            org_id,
            ticket_id,
            f"Read the email and classified it as {bucket.lower()} with {level} confidence ({confidence:.2f}).",
        )
        await system_note(
            tx,
            org_id,
            ticket_id,
            "Stood down — no customer-facing text generated. Assembled the context instead."
            if lane == "manual"
            else "Drafted a reply grounded in approved sources and attached the citations."
            if lane == "draft"
            else "Filled the action template and ran the validation checks.",
        )
        await system_note(
            tx,
            org_id,
            ticket_id,
            f"Routed to {department_name or 'no owning team'} and placed in the queue by urgency.",
        )
        if assignee:
            await system_note(
                tx, org_id, ticket_id, f"Assigned to {assignee['name']} — {assignee['reason']}."
            )

        sla_minutes = sla_budget(priority, locked.segment, escalation)
        patch: dict[str, Any] = {
            "lane": lane,
            "original_lane": locked.original_lane if deps.force_lane else lane,
            "lane_note": lane_note,
            "status": status,
            "priority": priority,
            "department_id": department_id,
            "query_type_id": meta.query_type_id if meta else None,
            "bucket": bucket,
            "confidence": confidence,
            "assignee_id": assignee["id"] if assignee else None,
            "owner_kind": "user" if assignee else "ai" if chain == "auto" else "unassigned",
            "sla_minutes": sla_minutes,
            "due_at": locked.received_at + timedelta(minutes=sla_minutes),
            "regulatory_flag": "Regulator named" if guard.get("regulator_named") else None,
            "sentiment": guard.get("sentiment", "neutral"),
            "split_proposed": multi,
            "next_move": _next_move(status, lane, chain, draft, multi),
            "category": ("Complaints" if guard.get("stop") else "Servicing")
            if lane == "manual"
            else "Servicing"
            if lane == "draft"
            else "Transactions",
            "subcategory": bucket,
        }
        if loaded.version_id:
            patch["deployment_version_id"] = loaded.version_id
            patch["deployment_id"] = loaded.deployment_id
        locked = await update_ticket(tx, locked, patch)
        tx.add(
            PredictionOutcome(
                org_id=org_id,
                agent_name=loaded.bucket_agent,
                ticket_id=ticket_id,
                confidence=confidence,
                correct=None,
            )
        )
        await record_ticket_event(
            tx,
            locked,
            actor=AI_ACTOR,
            action="triage.completed",
            summary=f"Classified QRY-{locked.number} as {bucket} ({confidence:.2f}) → {lane}",
            data={
                "lane": lane,
                "confidence": confidence,
                "traceId": trace_id,
                "degraded": degraded,
                "provider": provider,
                "engine": deps.engine.name,
                "deploymentVersionId": loaded.version_id,
                "configHash": loaded.config_hash,
                "predictionSet": choice.get("prediction_set") or [],
                "escalated": bool(choice.get("escalate")),
            },
            feed=Feed("stop", f"QRY-{locked.number} · hard stop") if guard.get("stop") else None,
        )
        await outbox.publish(tx, org_id, "ticket.created", {"ticketId": ticket_id})
    return lane


def choice_label(meta: CategoryMeta | None) -> str | None:
    return None if meta is None or meta.is_fallback else meta.bucket


def _is_account(label: str) -> bool:
    low = label.lower()
    return "account" in low or "card" in low


def _next_move(status: str, lane: str, chain: str | None, draft: Any, multi: bool) -> str:
    if status == "executing":
        return "Executing in the approved cell"
    if lane == "auto":
        return "Your approval + checker" if chain == "dual" else "Your approval"
    if lane == "draft":
        return "Check flagged paragraph" if draft is not None and draft.flagged else "Review & send draft"
    return "Split into two tickets" if multi else "Human commitment due"


def _subtasks(
    lane: str, chain: str | None, draft: Any, multi: bool, regulator: bool
) -> list[tuple[str, str, str, bool]]:
    if lane == "auto":
        head = [
            ("a1", "Verify the request against the customer record", "AI", True),
            ("a2", "Extract and validate the action fields", "AI", True),
        ]
        if chain == "dual":
            return [
                *head,
                ("a3", "Approve as maker", "You", False),
                ("a4", "Counter-approve as checker", "Team lead", False),
            ]
        return [
            *head,
            (
                "a3",
                "Sampled post-hoc review" if chain == "auto" else "Approve",
                "Team lead" if chain == "auto" else "You",
                False,
            ),
        ]
    if lane == "draft":
        return [
            ("b1", "Find approved sources for the answer", "AI", True),
            ("b2", "Draft the reply with citations", "AI", True),
            (
                "b3",
                "Read the flagged paragraph" if draft is not None and draft.flagged else "Read the draft",
                "You",
                False,
            ),
            ("b4", "Send and close", "You", False),
        ]
    return [
        ("c1", "Pull the history and records for the brief", "AI", True),
        ("c2", "Decide whether to split" if multi else "Decide the next step", "You", False),
        ("c3", "Give the customer a dated commitment", "You", False),
        ("c4", "Notify Compliance" if regulator else "Close the loop with the customer", "You", False),
    ]


# ── final failure ────────────────────────────────────────────────────────────────────────────────────────
async def handle_final_failure(job: JobRow) -> None:
    """Triage used up its retries: hand the ticket, unaltered, to a person in the manual lane."""
    ticket_id = str(job.payload.get("ticketId") or "")
    if not ticket_id:
        return
    async with tenant_tx(job.org_id) as tx:
        t = (
            await tx.execute(select(Ticket).where(Ticket.org_id == job.org_id, Ticket.id == ticket_id))
        ).scalar_one_or_none()
        if t is None or t.status != "triaging":
            return
        t = await lock_ticket(tx, job.org_id, ticket_id)
        note = "AI unavailable — handed to a person with the raw thread"
        sla_minutes = sla_budget(t.priority, t.segment)
        t = await update_ticket(
            tx,
            t,
            {
                "lane": "manual",
                "original_lane": "manual",
                "lane_note": note,
                "status": "with_human",
                "owner_kind": "unassigned",
                "next_move": "Human commitment due",
                "sla_minutes": sla_minutes,
                "due_at": t.received_at + timedelta(minutes=sla_minutes),
            },
        )
        await system_note(
            tx, job.org_id, ticket_id, "Triage could not complete. The thread is with a person, unaltered."
        )
        await record_ticket_event(
            tx,
            t,
            actor=AI_ACTOR,
            action="triage.failed",
            summary=f"Triage of QRY-{t.number} failed after {job.attempts or job.max_attempts} attempts → manual",
            data={"attempts": job.attempts},
            feed=Feed("flag", f"QRY-{t.number} · triage failed"),
        )
    lane_decisions.labels("unavailable", "manual").inc()


def install(worker: Worker) -> Worker:
    """Register the triage final-failure handler, chaining whatever was installed before it."""
    previous: JobHandler | None = getattr(worker, "_on_final_failure", None)

    async def dispatch(job: JobRow) -> None:
        if job.kind == "triage":
            await handle_final_failure(job)
        elif previous is not None:
            await previous(job)

    return worker.on_final_failure(dispatch)
