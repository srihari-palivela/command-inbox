"""Action library and autonomy dial, rules and policies, knowledge sources and gaps, the taxonomy map."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core import jobs
from command_inbox.core.audit import Feed, audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import conflict, forbidden, not_found, unprocessable
from command_inbox.core.outbox import publish
from command_inbox.db.engine import rows
from command_inbox.db.models import (
    ActionTemplate,
    AutonomyDial,
    BucketRule,
    Clearance,
    Department,
    GapTicket,
    KnowledgeSource,
    PriorityRule,
    ProposedRule,
    QueryType,
    User,
)
from command_inbox.domain.risk import APPROVAL_CYCLES, CELL_TITLE, LOCKED_CELL, MAX_DIAL, cell_of
from command_inbox.modules.insights.jsfmt import js_round
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import ActionTemplateBody, KnowledgeSourceBody

# ── Action library & autonomy dial ────────────────────────────────────────────
CELLS = ("0-0", "0-1", "1-0", "1-1")
CELL_META = {
    "0-0": (
        "Phase 3 candidate",
        "This is the only cell approved for autonomous execution, and only for the actions marked auto below.",
    ),
    "0-1": (
        "Two approvers, always",
        "Irreversible even without money moving — a closed account or a withdrawn nomination cannot be "
        "quietly undone.",
    ),
    "1-0": (
        "Approval plus an undo window",
        "Money moves but can be reversed. A 30-second undo window applies, after which reversal needs its "
        "own approval.",
    ),
    "1-1": (
        "A human signs, always",
        "This cell will not be automated. The dial is locked to suggest-only by policy, not by configuration.",
    ),
}
GATE_NOTE = {
    "0-0": "Risk sign-off held 12 Jun. Sampled review of 5% of executions runs weekly.",
    "0-1": "Raising this dial requires Risk & Compliance sign-off plus a 30-day sampled review.",
    "1-0": "Auto-execute is available only after 60 days at dual approval with reversal rate under 0.5%.",
    "1-1": "Locked by policy. Irreversible and financially material actions always carry a named human approver.",
}
DIAL_LABEL = ("suggest only", "suggest + approve", "auto-execute")

# Display form of the capability matrix (Rules & policies → Who may do what).
POLICY_MATRIX = dto.PoliciesDTOMatrix(
    cols=["AI agents", "Staff", "Team lead", "Admin · Risk"],
    rows=[
        dto.PoliciesDTOMatrixRows(capability=c, values=v)
        for c, v in (
            ("Suggest a draft or a filled action", ["yes", "yes", "yes", "yes"]),
            ("Send a reply to a customer", ["no", "yes", "yes", "yes"]),
            ("Execute · can be undone, no money", ["cell", "yes", "yes", "yes"]),
            ("Execute · money moves", ["no", "appr", "appr", "appr"]),
            ("Approve as checker (second pair of eyes)", ["no", "no", "yes", "yes"]),
            ("Reassign tickets & clearance", ["auto", "no", "yes", "yes"]),
            ("Change the autonomy dial", ["no", "no", "no", "yes"]),
            ("Edit agent prompts & rules", ["no", "no", "no", "yes"]),
        )
    ],
)


async def _advisory_lock(tx: AsyncSession, org_id: str, name: str) -> None:
    await tx.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"{org_id}:{name}"})


def _template_dto(t: ActionTemplate) -> dto.ActionTemplateDTO:
    return dto.ActionTemplateDTO(
        id=t.id,
        code=t.code,
        name=t.name,
        system=t.system,
        owner=t.owner,
        approval=t.approval,
        stp_pct=t.stp_pct,
        volume=t.monthly_volume,
        cell=cell_of(t.reversible, t.money_moves),
        state=t.state,
    )


async def actions_overview(tx: AsyncSession, ctx: Ctx) -> dto.ActionsDTO:
    require(ctx, "setup.view", "view the action library")
    templates = [
        _template_dto(t)
        for t in (
            await tx.execute(
                select(ActionTemplate)
                .where(ActionTemplate.org_id == ctx.org_id)
                .order_by(ActionTemplate.sort)
            )
        ).scalars()
    ]
    dial = {
        d.cell: d
        for d in (await tx.execute(select(AutonomyDial).where(AutonomyDial.org_id == ctx.org_id))).scalars()
    }
    cells = []
    for cell in CELLS:
        d = dial.get(cell)
        in_cell = sum(1 for t in templates if t.cell == cell)
        phase, audit_note = CELL_META[cell]
        cells.append(
            dto.RiskCellDTO(
                cell=cell,
                title=CELL_TITLE[cell],
                count=max((d.count_override or 0) if d else 0, in_cell),
                dial=d.level if d else 0,
                locked=cell == LOCKED_CELL or bool(d and d.locked),
                phase=phase,
                audit=audit_note,
                gate_note=GATE_NOTE[cell],
            )
        )
    return dto.ActionsDTO(
        cells=cells, templates=templates, autonomous_cells=sum(1 for c in cells if c.dial >= 2)
    )


async def set_dial(tx: AsyncSession, ctx: Ctx, cell: str, level: int) -> None:
    require(ctx, "autonomy.change", "change the autonomy dial")
    if cell == LOCKED_CELL and level > 0:
        raise forbidden("This cell is locked to suggest-only by policy.", "cell_locked")
    if level > MAX_DIAL[cell]:
        raise unprocessable(
            "dial_cap", "Auto-execute is only possible for actions that can be undone and move no money."
        )
    before = (
        await tx.execute(
            select(AutonomyDial.level)
            .where(AutonomyDial.org_id == ctx.org_id, AutonomyDial.cell == cell)
            .with_for_update()
        )
    ).scalar_one_or_none()
    await tx.execute(
        insert(AutonomyDial)
        .values(
            org_id=ctx.org_id,
            cell=cell,
            level=level,
            locked=cell == LOCKED_CELL,
            updated_by=ctx.user.id,
            updated_at=clock.now(),
        )
        .on_conflict_do_update(
            index_elements=[AutonomyDial.org_id, AutonomyDial.cell],
            set_={"level": level, "updated_by": ctx.user.id, "updated_at": clock.now()},
        )
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="autonomy.changed",
        entity="autonomy_dial",
        entity_id=cell,
        summary=f'Autonomy for "{CELL_TITLE[cell]}" set to {DIAL_LABEL[level]}',
        data={"from": before, "to": level},
        feed=Feed("flag", "autonomy dial"),
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "actions"})


async def create_action_template(
    tx: AsyncSession, ctx: Ctx, body: ActionTemplateBody
) -> dto.ActionTemplateDTO:
    require(ctx, "setup.edit", "create an action template")
    # Serialise code allocation per tenant so two concurrent creates cannot pick the same number.
    await _advisory_lock(tx, ctx.org_id, "action_template_code")
    [top] = await rows(
        tx,
        r"""select coalesce(max(nullif(substring(code from 'ACT-NEW-(\d+)'), '')::int), 0)::int as n
              from action_templates where org_id = :org""",
        {"org": ctx.org_id},
    )
    code = f"ACT-NEW-{top['n'] + 1:03d}"
    money = body.cell.startswith("1")
    reversible = body.cell.endswith("0")
    t = ActionTemplate(
        org_id=ctx.org_id,
        code=code,
        name=body.name,
        system=body.system,
        endpoint="PENDING",
        owner="Risk review",
        reversible=reversible,
        money_moves=money,
        approval="single" if reversible and not money else "dual",
        monthly_volume=0,
        state="pending_review",
        sort=1000,
    )
    tx.add(t)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="action_template.created",
        entity="action_template",
        entity_id=t.id,
        summary=f"{body.name} created as suggest-only in “{CELL_TITLE[body.cell]}” — pending Risk review",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "actions"})
    return dto.ActionTemplateDTO(
        id=t.id,
        code=code,
        name=t.name,
        system=t.system,
        owner=t.owner,
        approval=t.approval,
        stp_pct=None,
        volume=0,
        cell=body.cell,
        state="pending_review",
    )


# ── Rules & policies ─────────────────────────────────────────────────────────
async def policies(tx: AsyncSession, ctx: Ctx) -> dto.PoliciesDTO:
    require(ctx, "setup.view", "view rules and policies")
    bucket = (
        (
            await tx.execute(
                select(BucketRule).where(BucketRule.org_id == ctx.org_id).order_by(BucketRule.sort)
            )
        )
        .scalars()
        .all()
    )
    pri = (
        (
            await tx.execute(
                select(PriorityRule).where(PriorityRule.org_id == ctx.org_id).order_by(PriorityRule.sort)
            )
        )
        .scalars()
        .all()
    )
    proposed = (
        (
            await tx.execute(
                select(ProposedRule)
                .where(ProposedRule.org_id == ctx.org_id)
                .order_by(ProposedRule.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return dto.PoliciesDTO(
        bucket_rules=[
            dto.PoliciesDTOBucketRules(
                id=r.id, description=r.description, target=r.target, kind=r.kind, hits=r.hits
            )
            for r in bucket
        ],
        priority_rules=[
            dto.PoliciesDTOPriorityRules(
                id=r.id,
                key=r.key,
                description=r.description,
                target=r.target,
                hard=r.hard,
                enabled=r.enabled or r.hard,
                hits=r.hits,
            )
            for r in pri
        ],
        matrix=POLICY_MATRIX,
        cycles=[dto.PoliciesDTOCycles.model_validate(c) for c in APPROVAL_CYCLES],
        proposed_rules=[
            dto.PoliciesDTOProposedRules(
                id=p.id,
                text=p.text_,
                ticket_number=p.ticket_number,
                proposed_by=p.proposed_by_name,
                at=iso_ms(p.created_at),
                status=p.status,
            )
            for p in proposed
        ],
    )


async def toggle_priority_rule(tx: AsyncSession, ctx: Ctx, rule_id: str, enabled: bool) -> None:
    require(ctx, "rules.edit", "change priority rules")
    r = (
        await tx.execute(
            select(PriorityRule).where(PriorityRule.org_id == ctx.org_id, PriorityRule.id == rule_id)
        )
    ).scalar_one_or_none()
    if r is None:
        raise not_found("Rule")
    if r.hard:
        raise forbidden("Hard rules cannot be switched off — they are policy, not preference.", "hard_rule")
    await tx.execute(
        update(PriorityRule)
        .where(PriorityRule.org_id == ctx.org_id, PriorityRule.id == rule_id)
        .values(enabled=enabled)
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="rule.toggled",
        entity="priority_rule",
        entity_id=rule_id,
        summary=f'Priority rule "{r.description}" {"enabled" if enabled else "disabled"}',
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "policies"})


async def decide_proposed_rule(tx: AsyncSession, ctx: Ctx, rule_id: str, approve: bool) -> None:
    """Staff can only *propose* a hard stop; an admin decides (the matrix says only Admin edits rules)."""
    require(ctx, "rules.edit", "approve rule changes")
    p = (
        await tx.execute(
            select(ProposedRule)
            .where(ProposedRule.org_id == ctx.org_id, ProposedRule.id == rule_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if p is None:
        raise not_found("Proposed rule")
    if p.status != "pending":
        raise conflict("already_decided", "This proposal was already decided.")
    await tx.execute(
        update(ProposedRule)
        .where(ProposedRule.org_id == ctx.org_id, ProposedRule.id == rule_id)
        .values(status="approved" if approve else "rejected", decided_by=ctx.user.id)
    )
    if approve:
        [top] = await rows(
            tx,
            "select coalesce(max(sort), 0)::int as n from bucket_rules where org_id = :org",
            {"org": ctx.org_id},
        )
        tx.add(
            BucketRule(
                org_id=ctx.org_id,
                sort=top["n"] + 1,
                description=p.text_,
                target="Always a person — hard stop",
                kind="Hard stop",
                hits="new",
                pattern=None,
            )
        )
        await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="rule.approved" if approve else "rule.rejected",
        entity="proposed_rule",
        entity_id=rule_id,
        summary=f"{'Approved' if approve else 'Rejected'} proposed rule from {p.proposed_by_name}",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "policies"})


# ── Knowledge ────────────────────────────────────────────────────────────────
KIND_ABBR = {"SharePoint": "SP", "Confluence": "CF", "Google Drive": "GD", "S3": "S3", "Upload": "UP"}
GAP_ORDER = {"blocking": 0, "stale": 1, "unowned": 2, "resolved": 3}


def ago_label(d: datetime | None) -> str:
    if d is None:
        return "never"
    m = js_round((clock.now() - d).total_seconds() / 60)
    if m < 60:
        return f"{max(1, m)}m ago"
    if m < 1440:
        return f"{js_round(m / 60)}h ago"
    return f"{js_round(m / 1440)}d ago"


def gap_number(n: int) -> str:
    return f"GAP-{n:04d}"


async def knowledge(tx: AsyncSession, ctx: Ctx) -> dto.KnowledgeDTO:
    require(ctx, "setup.view", "view knowledge")
    sources = (
        (
            await tx.execute(
                select(KnowledgeSource)
                .where(KnowledgeSource.org_id == ctx.org_id)
                .order_by(KnowledgeSource.sort, KnowledgeSource.created_at)
            )
        )
        .scalars()
        .all()
    )
    depts = (
        (
            await tx.execute(
                select(Department).where(Department.org_id == ctx.org_id).order_by(Department.sort)
            )
        )
        .scalars()
        .all()
    )
    gaps = (
        (
            await tx.execute(
                select(GapTicket)
                .where(GapTicket.org_id == ctx.org_id)
                .order_by(GapTicket.closed_at.asc().nulls_last(), GapTicket.hits.desc())
            )
        )
        .scalars()
        .all()
    )
    qts = (
        (
            await tx.execute(
                select(QueryType).where(QueryType.org_id == ctx.org_id, QueryType.show_on_map.is_(True))
            )
        )
        .scalars()
        .all()
    )
    open_gaps = [g for g in gaps if g.closed_at is None]
    quotable = js_round(sum(1 for q in qts if q.live and q.department_id) / len(qts) * 100) if qts else 0
    # Invariant check: sent replies citing anything that was not approved content.
    [ungrounded] = await rows(
        tx,
        """select count(*)::int as n from drafts d
            where d.org_id = :org and d.state = 'sent'
              and exists (select 1 from jsonb_array_elements(d.citations) c
                            join knowledge_docs k on k.id::text = c->>'docId'
                           where k.status = 'pending')""",
        {"org": ctx.org_id},
    )
    ranked = sorted(gaps, key=lambda g: (GAP_ORDER.get(g.severity, 9), -g.hits))
    shown = [g for i, g in enumerate(ranked) if i < 5 or g.closed_at is None][:8]
    now = clock.now()
    return dto.KnowledgeDTO(
        sources=[
            dto.KnowledgeSourceDTO(
                id=k.id,
                name=k.name,
                kind=k.kind,
                abbr=k.abbr,
                docs=f"{k.doc_count} {k.doc_unit}",
                approved=f"{k.approved_count} approved",
                sync=k.sync_note
                or (f"Synced {ago_label(k.last_sync_at)}" if k.last_sync_at else "Connecting…"),
                health=k.health,
                note=k.note,
            )
            for k in sources
        ],
        readiness=[
            dto.KnowledgeDTOReadiness(department=d.name, pct=d.readiness_pct, note=d.readiness_note)
            for d in depts
        ],
        honest=dto.KnowledgeDTOHonest(
            quotable_pct=quotable, ungrounded_sentences=ungrounded["n"], open_gaps=len(open_gaps)
        ),
        gaps=[
            dto.GapDTO(
                id=g.id,
                number=gap_number(g.number),
                severity=g.severity,
                question=g.question,
                detail=g.detail,
                hits=g.hits,
                age="closed"
                if g.closed_at
                else f"open {max(1, js_round((now - g.opened_at) / timedelta(days=1)))} days",
                owner=g.owner,
                state=g.state,
                cta=g.cta,
            )
            for g in shown
        ],
    )


async def connect_source(tx: AsyncSession, ctx: Ctx, body: KnowledgeSourceBody) -> tuple[str, str]:
    require(ctx, "setup.edit", "connect a knowledge source")
    src = KnowledgeSource(
        org_id=ctx.org_id,
        name=body.name or f"New {body.kind} source",
        kind=body.kind,
        abbr=KIND_ABBR.get(body.kind, "KS"),
        doc_count=0,
        doc_unit="files" if body.kind == "Upload" else "documents",
        approved_count=0,
        health="warn",
        sync_note="Connecting… first sync queued",
        note="Read-only. Content stays pending until a knowledge manager approves it.",
        sort=100,
    )
    tx.add(src)
    await tx.flush()
    await jobs.enqueue(
        tx,
        ctx.org_id,
        "knowledge_sync",
        {"sourceId": src.id},
        run_at=clock.now() + timedelta(seconds=3),
        dedupe_key=f"ksync:{src.id}:first",
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="knowledge.source_connected",
        entity="knowledge_source",
        entity_id=src.id,
        summary=f"{body.kind} connected — first sync queued; nothing is citable until approved",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "knowledge"})
    return src.id, src.name


async def sync_source(tx: AsyncSession, ctx: Ctx, source_id: str) -> str:
    require(ctx, "setup.edit", "sync a knowledge source")
    src = (
        await tx.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.org_id == ctx.org_id, KnowledgeSource.id == source_id
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise not_found("Source")
    if src.health == "bad":
        await tx.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.org_id == ctx.org_id, KnowledgeSource.id == source_id)
            .values(sync_note="Re-consent requested from the owner")
        )
        await audit(
            tx,
            ctx.org_id,
            actor=actor_of(ctx),
            action="knowledge.reconsent_requested",
            entity="knowledge_source",
            entity_id=source_id,
            summary=f"Re-consent request sent to the {src.kind} owner",
        )
        await publish(tx, ctx.org_id, "setup.updated", {"area": "knowledge"})
        return f"Re-consent request sent to the {src.kind} owner."
    await jobs.enqueue(
        tx,
        ctx.org_id,
        "knowledge_sync",
        {"sourceId": source_id},
        dedupe_key=f"ksync:{source_id}:{int(clock.now().timestamp() * 1000)}",
    )
    return f"Sync queued for {src.name}."


async def act_on_gap(tx: AsyncSession, ctx: Ctx, gap_id: str) -> str:
    require(ctx, "setup.edit", "work gap tickets")
    g = (
        await tx.execute(select(GapTicket).where(GapTicket.org_id == ctx.org_id, GapTicket.id == gap_id))
    ).scalar_one_or_none()
    if g is None:
        raise not_found("Gap")
    number = gap_number(g.number)
    if g.closed_at is not None:
        return f"{number} is resolved."
    if g.owner == "Unassigned":
        state, owner = "Owner requested", "Branch Ops"
    else:
        state, owner = f"{g.cta} — sent to {g.owner}", g.owner
    await tx.execute(
        update(GapTicket)
        .where(GapTicket.org_id == ctx.org_id, GapTicket.id == gap_id)
        .values(state=state, owner=owner)
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="gap.actioned",
        entity="gap",
        entity_id=gap_id,
        summary=f"{g.cta} · {number} — sent to {owner}",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "knowledge"})
    return f"{g.cta} · {number} — sent to {owner}."


# ── Taxonomy ("Who owns what") ───────────────────────────────────────────────
CONTRACT = [
    (
        "The AI addresses the query",
        "It reads, classifies, extracts, drafts and fills the action. On the volume marked Auto it can carry "
        "the work end to end.",
    ),
    (
        "You stay accountable",
        "Every customer-facing sentence, and every action that cannot be undone, carries a named human "
        "approver. Approval is never implicit.",
    ),
    (
        "What it will not do",
        "It will not answer from model recall, will not touch a query type that has no owner, and will not "
        "proceed past a hard stop rule.",
    ),
    (
        "What it admits",
        "Below the bar it says so, hands over with context, and raises a gap ticket rather than guessing.",
    ),
]


async def taxonomy(tx: AsyncSession, ctx: Ctx) -> dto.TaxonomyDTO:
    require(ctx, "setup.view", "view the responsibility map")
    depts = (
        (
            await tx.execute(
                select(Department).where(Department.org_id == ctx.org_id).order_by(Department.sort)
            )
        )
        .scalars()
        .all()
    )
    qts = (
        (
            await tx.execute(
                select(QueryType)
                .where(QueryType.org_id == ctx.org_id, QueryType.show_on_map.is_(True))
                .order_by(QueryType.sort)
            )
        )
        .scalars()
        .all()
    )
    # Only the owners' names are needed (users is a platform table; never list other tenants' people).
    owner_ids = {d.owner_id for d in depts if d.owner_id}
    owners = {
        u.id: u
        for u in (
            (await tx.execute(select(User).where(User.id.in_(owner_ids)))).scalars().all()
            if owner_ids
            else []
        )
    }
    # Unowned query types are shown under the team they arrive at, flagged, so the gap is visible.
    retail = next((d for d in depts if d.name == "Retail Service Desk"), None)
    out = []
    for d in depts:
        items = [
            q
            for q in qts
            if q.department_id == d.id or (not q.department_id and retail is not None and d.id == retail.id)
        ]
        if not items:
            continue
        owner = owners.get(d.owner_id) if d.owner_id else None
        out.append(
            dto.TaxonomyDTODepartments(
                id=d.id,
                name=d.name,
                owner=dto.UserRef(id=owner.id, name=owner.name, initials=owner.initials) if owner else None,
                gap_note=d.gap_note,
                tone="risk" if d.risk else "normal",
                query_types=[
                    dto.TaxonomyDTODepartmentsQueryTypes(
                        id=q.id,
                        name=q.map_name or q.name,
                        lane=q.default_lane,
                        volume=q.monthly_volume,
                        live=q.live and bool(q.department_id),
                    )
                    for q in items
                ],
            )
        )
    return dto.TaxonomyDTO(
        departments=out,
        owned=sum(1 for q in qts if q.department_id),
        total=len(qts),
        contract=[dto.TaxonomyDTOContract(title=t, text=x) for t, x in CONTRACT],
    )


async def set_department_owner(tx: AsyncSession, ctx: Ctx, department_id: str, user_id: str | None) -> str:
    require(ctx, "setup.edit", "change who owns a query type")
    d = (
        await tx.execute(
            select(Department).where(Department.org_id == ctx.org_id, Department.id == department_id)
        )
    ).scalar_one_or_none()
    if d is None:
        raise not_found("Team")
    pick = user_id
    if not pick:
        pick = (
            await tx.execute(
                select(Clearance.user_id)
                .join(User, User.id == Clearance.user_id)
                .where(
                    Clearance.org_id == ctx.org_id,
                    Clearance.department_id == department_id,
                    Clearance.level >= 3,
                    *([Clearance.user_id != d.owner_id] if d.owner_id else []),
                )
                .order_by(User.name)
                .limit(1)
            )
        ).scalar_one_or_none()
    if not pick:
        raise conflict("no_candidate", "Nobody else holds approve clearance for this team.")
    level = (
        await tx.execute(
            select(Clearance.level).where(
                Clearance.org_id == ctx.org_id,
                Clearance.user_id == pick,
                Clearance.department_id == department_id,
            )
        )
    ).scalar_one_or_none()
    if level is None or level < 3:
        raise forbidden("The owner must hold approve clearance for this team.", "clearance_required")
    name = (await tx.execute(select(User.name).where(User.id == pick))).scalar_one()
    await tx.execute(
        update(Department)
        .where(Department.org_id == ctx.org_id, Department.id == department_id)
        .values(owner_id=pick)
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="taxonomy.owner_changed",
        entity="department",
        entity_id=department_id,
        summary=f"{d.name} now owned by {name}",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "map"})
    return name


async def open_gap_count(tx: AsyncSession, org_id: str) -> int:
    [r] = await rows(
        tx,
        "select count(*)::int as n from gap_tickets where org_id = :org and closed_at is null",
        {"org": org_id},
    )
    return int(r["n"])
