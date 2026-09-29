"""The pilot's stages, gates and sign-off.

    onboarding ─▶ shadow ─▶ assisted ─▶ live

- **Shadow**: the AI triages and drafts every real mail, but nothing is sent from Command Inbox; the team
  works as today and labels a sample so the AI's answers can be compared with theirs.
- **Assisted**: drafts go out after a person approves them (the only way anything is sent in v1).
- **Live**: the pilot's KPIs held; the whole team works this way. The automatic lane stays off in v1 (D5).

Moving forward is a request by one admin with the gates as evidence, signed off by a second person (a named
Risk approver when the workspace has any), with the gates checked again at sign-off. Stepping back is open to
any admin at once and needs only a reason: less autonomy is always allowed. Everything is audited.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import Feed, audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import bad_request, conflict, forbidden, not_found
from command_inbox.db.models import (
    EvalRun,
    Mailbox,
    Membership,
    Org,
    PilotIncident,
    PilotStageRequest,
    Ticket,
    User,
)
from command_inbox.modules.pilot import kpis as k
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import (
    PilotBaselineBody,
    PilotDecisionBody,
    PilotIncidentBody,
    PilotResolveBody,
    PilotSettingsBody,
    PilotStageBody,
)

STAGES = ("onboarding", "shadow", "assisted", "live")
NEXT = {"onboarding": "shadow", "shadow": "assisted", "assisted": "live"}
LABEL = {"onboarding": "Onboarding", "shadow": "Shadow", "assisted": "Assisted", "live": "Live"}
# Nothing is sent from Command Inbox before the bank has signed off assisted mode.
NO_SENDS = ("provisioned", "onboarding", "shadow")
DEFAULT_TARGETS: dict[str, Any] = {
    "acceptance": 0.7,
    "lightEditMax": 0.2,
    "agreement": 0.85,
    "shadowDays": 14,
    "assistedDays": 28,
    "minLabelled": 100,
    "minDrafts": 200,
}


def targets_of(org: Org) -> dict[str, Any]:
    return {**DEFAULT_TARGETS, **((org.pilot_settings or {}).get("targets") or {})}


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _gate(key: str, label: str, state: str, value: str, target: str) -> dto.PilotGateDTO:
    return dto.PilotGateDTO(key=key, label=label, state=state, value=value, target=target)  # type: ignore[arg-type]


async def require_sends_allowed(tx: AsyncSession, org_id: str) -> None:
    """Refuse any send from Command Inbox before assisted mode (the pilot's shadow stage)."""
    status = (await tx.execute(select(Org.status).where(Org.id == org_id))).scalar_one()
    if status in NO_SENDS:
        raise conflict(
            "pilot_shadow",
            "Nothing is sent from Command Inbox during shadow mode.",
            "Reply from your usual mail client for now; replies start here once assisted mode is signed off.",
        )


async def _org(tx: AsyncSession, org_id: str, lock: bool = False) -> Org:
    q = select(Org).where(Org.id == org_id)
    return (await tx.execute(q.with_for_update() if lock else q)).scalar_one()


def _since(org: Org) -> datetime:
    return org.status_changed_at or org.created_at


async def gates(tx: AsyncSession, org: Org, kp: k.Kpis) -> list[dto.PilotGateDTO]:
    """What must hold to move to the next stage."""
    t = targets_of(org)
    nxt = NEXT.get(org.status)
    out: list[dto.PilotGateDTO] = []
    baseline = (org.pilot_settings or {}).get("baseline") or {}
    days = max(0, (clock.now() - _since(org)).days)

    def no_p1() -> dto.PilotGateDTO:
        return _gate(
            "p1",
            "No P1 incidents in this stage",
            "pass" if not kp.p1_incidents else "fail",
            str(kp.p1_incidents),
            "0",
        )

    def no_misses() -> dto.PilotGateDTO:
        return _gate(
            "hard_stops",
            "No hard-stop misses",
            "pass" if not kp.hard_stop_misses else "fail",
            str(kp.hard_stop_misses),
            "0",
        )

    if nxt == "shadow":
        passed = (
            await tx.execute(
                select(EvalRun.id).where(EvalRun.org_id == org.id, EvalRun.state == "passed").limit(1)
            )
        ).first()
        mailbox = (
            await tx.execute(
                select(Mailbox.id)
                .where(Mailbox.org_id == org.id, Mailbox.state.in_(("streaming", "triage_only", "observe")))
                .limit(1)
            )
        ).first()
        out.append(
            _gate(
                "eval",
                "A deployment passed its evaluation",
                "pass" if passed else "fail",
                "yes" if passed else "no",
                "passing run",
            )
        )
        out.append(
            _gate(
                "mailbox",
                "A mailbox is connected",
                "pass" if mailbox else "fail",
                "yes" if mailbox else "no",
                "connected",
            )
        )
        has = baseline.get("onTimeRate") is not None
        out.append(
            _gate(
                "baseline",
                "Reply-time baseline recorded before the pilot",
                "pass" if has else "fail",
                _pct(baseline.get("onTimeRate")) + " on time" if has else "not recorded",
                "recorded",
            )
        )
    elif nxt == "assisted":
        out.append(
            _gate(
                "days",
                "Days in shadow",
                "pass" if days >= t["shadowDays"] else "pending",
                str(days),
                f"≥ {t['shadowDays']}",
            )
        )
        enough = kp.labelled >= t["minLabelled"]
        out.append(
            _gate(
                "labelled",
                "Mail labelled by people",
                "pass" if enough else "pending",
                str(kp.labelled),
                f"≥ {t['minLabelled']}",
            )
        )
        ca = kp.category_agreement
        out.append(
            _gate(
                "category",
                "AI and people agree on the category",
                "pending" if not enough else "pass" if (ca or 0) >= t["agreement"] else "fail",
                _pct(ca),
                f"≥ {_pct(t['agreement'])}",
            )
        )
        la = kp.lane_agreement
        lane_enough = kp.lane_compared >= t["minLabelled"]
        out.append(
            _gate(
                "lane",
                "AI and people agree on the lane",
                "pending" if not lane_enough else "pass" if (la or 0) >= t["agreement"] else "fail",
                _pct(la),
                f"≥ {_pct(t['agreement'])}",
            )
        )
        out.append(no_misses())
        out.append(no_p1())
    elif nxt == "live":
        out.append(
            _gate(
                "days",
                "Days in assisted mode",
                "pass" if days >= t["assistedDays"] else "pending",
                str(days),
                f"≥ {t['assistedDays']}",
            )
        )
        enough = kp.drafts_decided >= t["minDrafts"]
        out.append(
            _gate(
                "drafts",
                "Drafts decided",
                "pass" if enough else "pending",
                str(kp.drafts_decided),
                f"≥ {t['minDrafts']}",
            )
        )
        ar = kp.acceptance_rate
        out.append(
            _gate(
                "acceptance",
                f"Drafts sent unedited or lightly edited (≤ {_pct(t['lightEditMax'])} changed)",
                "pending" if not enough else "pass" if (ar or 0) >= t["acceptance"] else "fail",
                _pct(ar),
                f"≥ {_pct(t['acceptance'])}",
            )
        )
        out.append(no_misses())
        base = baseline.get("onTimeRate")
        if base is None:
            out.append(
                _gate(
                    "sla",
                    "Replies on time vs the baseline",
                    "fail",
                    _pct(kp.on_time_rate),
                    "baseline not recorded",
                )
            )
        else:
            ok = kp.on_time_rate is not None and kp.on_time_rate >= base
            out.append(
                _gate(
                    "sla",
                    "Replies on time vs the baseline",
                    "pending" if kp.on_time_rate is None else "pass" if ok else "fail",
                    _pct(kp.on_time_rate),
                    f"≥ {_pct(base)}",
                )
            )
        fr = baseline.get("firstReplyMinutes")
        if fr is not None and kp.median_first_reply_minutes is not None:
            ok = kp.median_first_reply_minutes <= fr
            out.append(
                _gate(
                    "first_reply",
                    "Median first reply vs the baseline",
                    "pass" if ok else "fail",
                    f"{kp.median_first_reply_minutes:.0f} min",
                    f"≤ {fr:.0f} min",
                )
            )
        out.append(no_p1())
    return out


async def _kpis(tx: AsyncSession, org: Org) -> k.Kpis:
    return await k.compute(tx, org.id, _since(org), clock.now(), float(targets_of(org)["lightEditMax"]))


async def _refs(tx: AsyncSession, ids: set[str | None]) -> dict[str, dto.UserRef]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    found = await tx.execute(select(User.id, User.name, User.initials).where(User.id.in_(wanted)))
    return {str(u.id): dto.UserRef(id=str(u.id), name=u.name, initials=u.initials) for u in found}


def _may_sign_off(ctx: Ctx, org: Org, r: PilotStageRequest) -> bool:
    approvers = (org.pilot_settings or {}).get("riskApprovers") or []
    return (
        r.state == "pending"
        and ctx.can("autonomy.change")
        and r.requested_by != ctx.user.id
        and (not approvers or ctx.user.id in approvers)
    )


def _request_dto(
    ctx: Ctx, org: Org, r: PilotStageRequest, refs: dict[str, dto.UserRef]
) -> dto.PilotRequestDTO:
    return dto.PilotRequestDTO(
        id=r.id,
        from_stage=r.from_stage,
        to_stage=r.to_stage,
        reason=r.reason,
        state=r.state,  # type: ignore[arg-type]
        requested_by=refs.get(r.requested_by),
        requested_at=iso_ms(r.requested_at),
        decided_by=refs.get(r.decided_by) if r.decided_by else None,
        decided_at=iso_ms(r.decided_at) if r.decided_at else None,
        decision_note=r.decision_note,
        evidence=[dto.PilotGateDTO.model_validate(g) for g in (r.evidence or {}).get("gates", [])],
        can_decide=_may_sign_off(ctx, org, r),
        can_withdraw=r.state == "pending" and ctx.can("autonomy.change"),
    )


async def get_pilot(tx: AsyncSession, ctx: Ctx) -> dto.PilotDTO:
    require(ctx, "insights.view", "view the pilot")
    org = await _org(tx, ctx.org_id)
    kp = await _kpis(tx, org)
    gs = await gates(tx, org, kp)
    requests = list(
        (
            await tx.execute(
                select(PilotStageRequest)
                .where(PilotStageRequest.org_id == org.id)
                .order_by(PilotStageRequest.requested_at.desc())
                .limit(30)
            )
        ).scalars()
    )
    settings = org.pilot_settings or {}
    approver_ids = settings.get("riskApprovers") or []
    refs = await _refs(
        tx, {*approver_ids, *(r.requested_by for r in requests), *(r.decided_by for r in requests)}
    )
    pending = next((r for r in requests if r.state == "pending"), None)
    t = targets_of(org)
    b = settings.get("baseline") or {}
    since = _since(org)
    return dto.PilotDTO(
        stage=org.status,  # type: ignore[arg-type]
        stage_since=iso_ms(since),
        days_in_stage=max(0, (clock.now() - since).days),
        next=NEXT.get(org.status),  # type: ignore[arg-type]
        gates=gs,
        ready=bool(gs) and all(g.state == "pass" for g in gs),
        pending=_request_dto(ctx, org, pending, refs) if pending else None,
        history=[_request_dto(ctx, org, r, refs) for r in requests if r.state != "pending"],
        kpis=dto.PilotKpisDTO(
            window_start=iso_ms(kp.since),
            days=kp.days,
            drafts_decided=kp.drafts_decided,
            drafts_accepted=kp.drafts_accepted,
            acceptance_rate=kp.acceptance_rate,
            labelled=kp.labelled,
            category_agreement=kp.category_agreement,
            lane_compared=kp.lane_compared,
            lane_agreement=kp.lane_agreement,
            hard_stop_misses=kp.hard_stop_misses,
            on_time_rate=kp.on_time_rate,
            median_first_reply_minutes=kp.median_first_reply_minutes,
            p1_incidents=kp.p1_incidents,
            open_incidents=kp.open_incidents,
        ),
        targets=dto.PilotTargetsDTO(
            acceptance=t["acceptance"],
            light_edit_max=t["lightEditMax"],
            agreement=t["agreement"],
            shadow_days=t["shadowDays"],
            assisted_days=t["assistedDays"],
            min_labelled=t["minLabelled"],
            min_drafts=t["minDrafts"],
        ),
        baseline=dto.PilotBaselineDTO(
            on_time_rate=b.get("onTimeRate"),
            first_reply_minutes=b.get("firstReplyMinutes"),
            captured_at=b.get("capturedAt"),
            source=b.get("source"),
            days=b.get("days"),
        ),
        risk_approvers=[refs[i] for i in approver_ids if i in refs],
        sends_allowed=org.status not in NO_SENDS,
        can_request=ctx.can("autonomy.change") and org.status in NEXT and pending is None,
        can_step_back=ctx.can("autonomy.change") and org.status in ("shadow", "assisted", "live"),
        can_edit_settings=ctx.can("autonomy.change"),
    )


async def _set_stage(tx: AsyncSession, org: Org, to: str) -> None:
    org.status = to
    org.status_changed_at = clock.now()
    await tx.flush()


async def request_stage(tx: AsyncSession, ctx: Ctx, body: PilotStageBody) -> dto.PilotDTO:
    """Ask to move one stage forward. The gates must hold now; a second person signs off."""
    require(ctx, "autonomy.change", "move the pilot to the next stage")
    org = await _org(tx, ctx.org_id, lock=True)
    if NEXT.get(org.status) != body.to_stage:
        raise conflict(
            "bad_stage",
            f"From {LABEL.get(org.status, org.status)} the next stage is "
            f"{LABEL.get(NEXT.get(org.status, ''), 'none')}.",
        )
    gs = await gates(tx, org, await _kpis(tx, org))
    failing = [g.label for g in gs if g.state != "pass"]
    if failing:
        raise conflict(
            "gates_not_met", "The gates for the next stage do not all hold yet.", "; ".join(failing)
        )
    r = PilotStageRequest(
        org_id=org.id,
        from_stage=org.status,
        to_stage=body.to_stage,
        reason=body.reason,
        evidence={"gates": [g.model_dump(by_alias=True) for g in gs], "at": iso_ms(clock.now())},
        requested_by=ctx.user.id,
    )
    try:
        async with tx.begin_nested():
            tx.add(r)
            await tx.flush()
    except IntegrityError:
        raise conflict("pending_exists", "A stage change is already waiting for sign-off.") from None
    await audit(
        tx,
        org.id,
        actor=actor_of(ctx),
        action="pilot.stage_requested",
        entity="pilot",
        entity_id=r.id,
        summary=f"{ctx.user.name} asked to move the pilot from {LABEL[r.from_stage]} to {LABEL[r.to_stage]}",
        data={"from": r.from_stage, "to": r.to_stage, "reason": body.reason, "gates": r.evidence["gates"]},
        feed=Feed("flag", "pilot"),
    )
    return await get_pilot(tx, ctx)


async def _pending(tx: AsyncSession, org_id: str, request_id: str) -> PilotStageRequest:
    r = (
        await tx.execute(
            select(PilotStageRequest)
            .where(PilotStageRequest.org_id == org_id, PilotStageRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if r is None:
        raise not_found("Stage change")
    if r.state != "pending":
        raise conflict("not_pending", "This stage change has already been decided.")
    return r


async def decide(tx: AsyncSession, ctx: Ctx, request_id: str, body: PilotDecisionBody) -> dto.PilotDTO:
    """The second person's sign-off (four eyes). Approval re-checks the gates against today's records."""
    require(ctx, "autonomy.change", "sign off a pilot stage change")
    org = await _org(tx, ctx.org_id, lock=True)
    r = await _pending(tx, org.id, request_id)
    if r.requested_by == ctx.user.id:
        raise forbidden("You asked for this change; another person has to sign it off.", "four_eyes")
    approvers = (org.pilot_settings or {}).get("riskApprovers") or []
    if approvers and ctx.user.id not in approvers:
        raise forbidden("Only a named Risk approver can sign off a pilot stage change.", "not_risk_approver")
    now = clock.now()
    if not body.approve:
        if not body.note:
            raise bad_request("note_required", "Say why the change is refused.")
        r.state, r.decided_by, r.decided_at, r.decision_note = "rejected", ctx.user.id, now, body.note
        await tx.flush()
        await audit(
            tx,
            org.id,
            actor=actor_of(ctx),
            action="pilot.stage_rejected",
            entity="pilot",
            entity_id=r.id,
            summary=f"{ctx.user.name} refused moving the pilot to {LABEL[r.to_stage]}: {body.note[:120]}",
            data={"from": r.from_stage, "to": r.to_stage, "note": body.note},
        )
        return await get_pilot(tx, ctx)
    if org.status != r.from_stage:
        raise conflict("stage_moved", "The pilot has changed stage since this was asked; ask again.")
    gs = await gates(tx, org, await _kpis(tx, org))
    failing = [g.label for g in gs if g.state != "pass"]
    if failing:
        raise conflict("gates_not_met", "The gates no longer all hold.", "; ".join(failing))
    r.state, r.decided_by, r.decided_at, r.decision_note = "approved", ctx.user.id, now, body.note
    await _set_stage(tx, org, r.to_stage)
    await audit(
        tx,
        org.id,
        actor=actor_of(ctx),
        action="pilot.stage_changed",
        entity="pilot",
        entity_id=r.id,
        summary=f"{ctx.user.name} signed off the pilot moving from {LABEL[r.from_stage]} to {LABEL[r.to_stage]}",
        data={
            "from": r.from_stage,
            "to": r.to_stage,
            "requestedBy": r.requested_by,
            "note": body.note,
            "gates": [g.model_dump(by_alias=True) for g in gs],
        },
        feed=Feed("flag", "pilot"),
    )
    return await get_pilot(tx, ctx)


async def withdraw(tx: AsyncSession, ctx: Ctx, request_id: str) -> dto.PilotDTO:
    require(ctx, "autonomy.change", "withdraw a pilot stage change")
    r = await _pending(tx, ctx.org_id, request_id)
    r.state, r.decided_by, r.decided_at = "withdrawn", ctx.user.id, clock.now()
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="pilot.stage_withdrawn",
        entity="pilot",
        entity_id=r.id,
        summary=f"{ctx.user.name} withdrew the request to move the pilot to {LABEL[r.to_stage]}",
    )
    return await get_pilot(tx, ctx)


async def step_back(tx: AsyncSession, ctx: Ctx, body: PilotStageBody) -> dto.PilotDTO:
    """Less autonomy, at once: no sign-off, only a reason. Any waiting request is withdrawn."""
    require(ctx, "autonomy.change", "step the pilot back")
    org = await _org(tx, ctx.org_id, lock=True)
    if org.status not in STAGES or STAGES.index(body.to_stage) >= STAGES.index(org.status):
        raise conflict("bad_stage", f"{LABEL[body.to_stage]} is not a step back from {org.status}.")
    now = clock.now()
    await tx.execute(
        update(PilotStageRequest)
        .where(PilotStageRequest.org_id == org.id, PilotStageRequest.state == "pending")
        .values(
            state="withdrawn", decided_by=ctx.user.id, decided_at=now, decision_note="The pilot stepped back."
        )
    )
    before = org.status
    await _set_stage(tx, org, body.to_stage)
    await audit(
        tx,
        org.id,
        actor=actor_of(ctx),
        action="pilot.stepped_back",
        entity="pilot",
        entity_id=org.id,
        summary=f"{ctx.user.name} stepped the pilot back from {LABEL[before]} to {LABEL[body.to_stage]}: "
        f"{body.reason[:120]}",
        data={"from": before, "to": body.to_stage, "reason": body.reason},
        feed=Feed("stop", "pilot"),
    )
    return await get_pilot(tx, ctx)


async def save_settings(tx: AsyncSession, ctx: Ctx, body: PilotSettingsBody) -> dto.PilotDTO:
    require(ctx, "autonomy.change", "change the pilot's targets")
    org = await _org(tx, ctx.org_id, lock=True)
    approvers = list(dict.fromkeys(body.risk_approvers))
    if approvers:
        admins = set(
            (
                await tx.execute(
                    select(Membership.user_id).where(
                        Membership.org_id == org.id,
                        Membership.user_id.in_(approvers),
                        Membership.role == "admin",
                    )
                )
            ).scalars()
        )
        if missing := [a for a in approvers if a not in {str(x) for x in admins}]:
            raise bad_request(
                "not_admin", "Risk approvers must be admins of this workspace.", ", ".join(missing)
            )
    before = dict(org.pilot_settings or {})
    targets = body.targets.model_dump(by_alias=True)
    org.pilot_settings = {**before, "targets": targets, "riskApprovers": approvers}
    await tx.flush()
    await audit(
        tx,
        org.id,
        actor=actor_of(ctx),
        action="pilot.settings_changed",
        entity="pilot",
        entity_id=org.id,
        summary=f"{ctx.user.name} set the pilot's targets and {len(approvers)} Risk approver"
        f"{'s' if len(approvers) != 1 else ''}",
        data={
            "before": {"targets": before.get("targets"), "riskApprovers": before.get("riskApprovers")},
            "after": {"targets": targets, "riskApprovers": approvers},
        },
    )
    return await get_pilot(tx, ctx)


async def record_baseline(tx: AsyncSession, ctx: Ctx, body: PilotBaselineBody) -> dto.PilotDTO:
    """From the workspace's own records over the last `days`, or figures the bank measured itself."""
    require(ctx, "autonomy.change", "record the pilot's baseline")
    org = await _org(tx, ctx.org_id, lock=True)
    now = clock.now()
    baseline: dict[str, Any]
    if body.days is not None:
        rate, fr = await k.reply_times(tx, org.id, now - timedelta(days=body.days), now)
        if rate is None:
            raise conflict("no_records", f"No mail with a deadline in the last {body.days} days to measure.")
        baseline = {
            "onTimeRate": round(rate, 4),
            "firstReplyMinutes": fr,
            "source": "records",
            "days": body.days,
        }
    elif body.on_time_rate is not None:
        baseline = {
            "onTimeRate": body.on_time_rate,
            "firstReplyMinutes": body.first_reply_minutes,
            "source": "manual",
            "days": None,
        }
    else:
        raise bad_request("baseline_missing", "Give a number of days to measure, or the on-time rate.")
    baseline["capturedAt"] = iso_ms(now)
    org.pilot_settings = {**(org.pilot_settings or {}), "baseline": baseline}
    await tx.flush()
    await audit(
        tx,
        org.id,
        actor=actor_of(ctx),
        action="pilot.baseline_recorded",
        entity="pilot",
        entity_id=org.id,
        summary=f"{ctx.user.name} recorded the pilot baseline: {_pct(float(baseline['onTimeRate']))} of mail answered on "
        f"time ({baseline['source']})",
        data=baseline,
    )
    return await get_pilot(tx, ctx)


# ── Shadow comparison ─────────────────────────────────────────────────────────────


async def shadow_report(tx: AsyncSession, ctx: Ctx, days: int) -> dto.ShadowReportDTO:
    require(ctx, "insights.view", "view the shadow comparison")
    now = clock.now()
    rows = await k.compared(tx, ctx.org_id, now - timedelta(days=days), now)
    lanes: dict[tuple[str, str], int] = {}
    cats: dict[str, list[int]] = {}
    out: list[dto.ShadowReportDTODisagreements] = []
    for c in rows:
        lanes[(c.ai_lane, c.final_lane)] = lanes.get((c.ai_lane, c.final_lane), 0) + 1
        kinds = []
        if c.labelled and c.human_hard_stop and not c.ai_hard_stop:
            kinds.append("hard_stop_miss")
        if c.labelled and c.human_category:
            agg = cats.setdefault(c.human_category, [0, 0])
            agg[0] += 1
            if c.ai_category == c.human_category:
                agg[1] += 1
            else:
                kinds.append("category")
        if c.ai_lane != c.final_lane:
            kinds.append("lane")
        if kinds and len(out) < 100:
            out.append(
                dto.ShadowReportDTODisagreements(
                    ticket_id=c.id,
                    number=c.number,
                    subject=c.subject[:200],
                    kind=kinds[0],  # type: ignore[arg-type]
                    ai_category=c.ai_category,
                    human_category=c.human_category,
                    ai_lane=c.ai_lane,
                    final_lane=c.final_lane,
                    ai_hard_stop=c.ai_hard_stop,
                )
            )
    return dto.ShadowReportDTO(
        days=days,
        compared=len(rows),
        labelled=sum(1 for c in rows if c.labelled),
        lanes=[dto.ShadowReportDTOLanes(ai=a, final=f, count=n) for (a, f), n in sorted(lanes.items())],
        categories=[
            dto.ShadowReportDTOCategories(key=key, labelled=v[0], agreed=v[1])
            for key, v in sorted(cats.items(), key=lambda kv: -kv[1][0])
        ],
        disagreements=out,
    )


# ── Incidents ─────────────────────────────────────────────────────────────────────


async def incidents(tx: AsyncSession, ctx: Ctx) -> list[dto.PilotIncidentDTO]:
    require(ctx, "ticket.work", "view the incident log")
    found = list(
        (
            await tx.execute(
                select(PilotIncident, Ticket.number)
                .outerjoin(
                    Ticket, (Ticket.id == PilotIncident.ticket_id) & (Ticket.org_id == PilotIncident.org_id)
                )
                .where(PilotIncident.org_id == ctx.org_id)
                .order_by(PilotIncident.resolved_at.is_(None).desc(), PilotIncident.opened_at.desc())
                .limit(200)
            )
        ).all()
    )
    refs = await _refs(tx, {*(i.opened_by for i, _ in found), *(i.resolved_by for i, _ in found)})
    return [
        dto.PilotIncidentDTO(
            id=i.id,
            severity=i.severity,  # type: ignore[arg-type]
            kind=i.kind,  # type: ignore[arg-type]
            title=i.title,
            detail=i.detail,
            ticket_id=i.ticket_id,
            ticket_number=number,
            opened_by=refs.get(i.opened_by),
            opened_at=iso_ms(i.opened_at),
            resolved_by=refs.get(i.resolved_by) if i.resolved_by else None,
            resolved_at=iso_ms(i.resolved_at) if i.resolved_at else None,
            resolution=i.resolution,
        )
        for i, number in found
    ]


async def log_incident(tx: AsyncSession, ctx: Ctx, body: PilotIncidentBody) -> list[dto.PilotIncidentDTO]:
    """Anyone who works mail can log an incident; a P1 raises an alert for the admins."""
    require(ctx, "ticket.work", "log a pilot incident")
    ticket_id = None
    if body.ticket_number is not None:
        ticket_id = (
            await tx.execute(
                select(Ticket.id).where(Ticket.org_id == ctx.org_id, Ticket.number == body.ticket_number)
            )
        ).scalar_one_or_none()
        if ticket_id is None:
            raise not_found(f"QRY-{body.ticket_number}")
    i = PilotIncident(
        org_id=ctx.org_id,
        severity=body.severity,
        kind=body.kind,
        title=body.title,
        detail=body.detail,
        ticket_id=ticket_id,
        opened_by=ctx.user.id,
    )
    tx.add(i)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="pilot.incident_logged",
        entity="pilot_incident",
        entity_id=i.id,
        ticket_id=ticket_id,
        summary=f"{ctx.user.name} logged a {body.severity} incident: {body.title[:120]}",
        data={"severity": body.severity, "kind": body.kind, "ticketNumber": body.ticket_number},
        feed=Feed("stop", "incident") if body.severity == "P1" else None,
    )
    if body.severity == "P1":
        from command_inbox.modules.insights.alerts import apply, conditions

        await apply(tx, ctx.org_id, await conditions(tx, ctx.org_id))
    return await incidents(tx, ctx)


async def resolve_incident(
    tx: AsyncSession, ctx: Ctx, incident_id: str, body: PilotResolveBody
) -> list[dto.PilotIncidentDTO]:
    require(ctx, "autonomy.change", "close a pilot incident")
    i = (
        await tx.execute(
            select(PilotIncident)
            .where(PilotIncident.org_id == ctx.org_id, PilotIncident.id == incident_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if i is None:
        raise not_found("Incident")
    if i.resolved_at is not None:
        raise conflict("already_resolved", "This incident is already closed.")
    i.resolved_at, i.resolved_by, i.resolution = clock.now(), ctx.user.id, body.resolution
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="pilot.incident_resolved",
        entity="pilot_incident",
        entity_id=i.id,
        ticket_id=i.ticket_id,
        summary=f"{ctx.user.name} closed the {i.severity} incident “{i.title[:80]}”: {body.resolution[:120]}",
    )
    if i.severity == "P1":
        from command_inbox.modules.insights.alerts import apply, conditions

        await apply(tx, ctx.org_id, await conditions(tx, ctx.org_id))
    return await incidents(tx, ctx)
