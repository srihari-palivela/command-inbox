"""The onboarding checklist: the bank admin's landing page until go-live.

Every step's state is computed from the workspace's real data, never ticked by hand, so the checklist cannot
claim something that is not true. Steps that belong to later releases say so ("later").
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.db.models import (
    Deployment,
    DeploymentVersion,
    EvalRun,
    KnowledgeSource,
    Mailbox,
    Membership,
    Org,
    User,
)
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto

MANDATORY_HARD_STOPS = {
    "regulator_named",
    "fraud_or_scam",
    "legal_action",
    "vulnerable_customer",
    "complaint_escalation",
}
MIN_EXAMPLES = 5


def _step(
    key: str, title: str, description: str, state: str, detail: str, to: str | None
) -> dto.OnboardingStepDTO:
    return dto.OnboardingStepDTO(
        key=key, title=title, description=description, state=state, detail=detail, to=to
    )  # type: ignore[arg-type]


async def _count(tx: AsyncSession, q) -> int:
    return int((await tx.execute(q)).scalar_one())


async def checklist(tx: AsyncSession, ctx: Ctx) -> dto.OnboardingDTO:
    require(ctx, "workspace.manage", "see the onboarding checklist")
    org = (await tx.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one()
    steps: list[dto.OnboardingStepDTO] = []

    profile_fields = [org.legal_name, org.support_email, org.locale, org.currency, org.time_zone]
    filled = sum(1 for f in profile_fields if f)
    steps.append(
        _step(
            "profile",
            "Organisation profile",
            "Legal name, support email, locale, currency and time zone.",
            "done" if filled == len(profile_fields) else "in_progress" if filled else "not_started",
            f"{filled} of {len(profile_fields)} set",
            "/admin/organisation",
        )
    )

    sso_members = await _count(
        tx,
        select(func.count())
        .select_from(Membership)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org.id, User.idp_subject.is_not(None)),
    )
    sso_state = (org.sso_config or {}).get("state", "not_connected")
    steps.append(
        _step(
            "sso",
            "Single sign-on",
            "Connect Microsoft Entra ID or Google Workspace, then have a colleague sign in through it.",
            "done"
            if org.sso_idp_alias and sso_members >= 1
            else "in_progress"
            if sso_state != "not_connected"
            else "not_started",
            f"{sso_state.replace('_', ' ')}; {sso_members} signed in with SSO",
            "/admin/organisation",
        )
    )

    by_role = dict(
        (
            await tx.execute(
                select(Membership.role, func.count())
                .where(Membership.org_id == org.id)
                .group_by(Membership.role)
            )
        ).all()
    )
    approvers = by_role.get("admin", 0) + by_role.get("lead", 0)
    members = sum(by_role.values())
    steps.append(
        _step(
            "people",
            "People",
            "Invite your team with one role each. You need at least two approvers, so maker and checker differ.",
            "done"
            if by_role.get("admin", 0) >= 1 and approvers >= 2
            else "in_progress"
            if members > 1
            else "not_started",
            f"{members} member{'s' if members != 1 else ''}, {approvers} approver{'s' if approvers != 1 else ''}",
            "/admin/members",
        )
    )

    live_boxes = await _count(
        tx,
        select(func.count())
        .select_from(Mailbox)
        .where(Mailbox.org_id == org.id, Mailbox.state.in_(("streaming", "triage_only", "observe"))),
    )
    any_boxes = await _count(tx, select(func.count()).select_from(Mailbox).where(Mailbox.org_id == org.id))
    steps.append(
        _step(
            "mailbox",
            "Mailbox",
            "Connect one shared mailbox through Microsoft 365 or Google Workspace and pass the test-mail round trip.",
            "done" if live_boxes else "in_progress" if any_boxes else "not_started",
            f"{live_boxes} connected" if any_boxes else "No mailbox yet",
            "/setup/mailboxes",
        )
    )

    config = (
        await tx.execute(
            select(DeploymentVersion.config)
            .join(Deployment, Deployment.active_version_id == DeploymentVersion.id)
            .where(Deployment.org_id == org.id)
            .order_by(Deployment.created_at)
            .limit(1)
        )
    ).scalar_one_or_none() or {}
    cats = (config.get("taxonomy") or {}).get("categories") or []
    ready = [c for c in cats if len(c.get("examples") or []) >= MIN_EXAMPLES and c.get("department")]
    steps.append(
        _step(
            "categories",
            "Departments and categories",
            f"Every category has an owning department and at least {MIN_EXAMPLES} examples from your own mail.",
            "done" if cats and len(ready) == len(cats) else "in_progress" if cats else "not_started",
            f"{len(ready)} of {len(cats)} categories ready",
            "/admin/deployments",
        )
    )

    approved = await _count(
        tx,
        select(func.coalesce(func.sum(KnowledgeSource.approved_count), 0)).where(
            KnowledgeSource.org_id == org.id
        ),
    )
    sources = await _count(
        tx, select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.org_id == org.id)
    )
    steps.append(
        _step(
            "knowledge",
            "Knowledge",
            "Upload or connect your policies, product sheets and templates, and approve them for citation.",
            "done" if approved else "in_progress" if sources else "not_started",
            f"{approved} approved document{'s' if approved != 1 else ''}",
            "/setup/knowledge",
        )
    )

    stops = {h.get("key") for h in ((config.get("rules") or {}).get("hardStops") or [])}
    missing = MANDATORY_HARD_STOPS - stops
    passed_runs = await _count(
        tx,
        select(func.count()).select_from(EvalRun).where(EvalRun.org_id == org.id, EvalRun.state == "passed"),
    )
    steps.append(
        _step(
            "rules",
            "Rules and hard stops",
            "Review the mandatory hard stops and add your own; the hard-stop test set must reach 100% recall.",
            "done" if not missing and passed_runs else "in_progress" if not missing else "not_started",
            "All mandatory hard stops present" if not missing else f"Missing: {', '.join(sorted(missing))}",
            "/admin/deployments",
        )
    )

    steps.append(
        _step(
            "actions",
            "Actions and systems",
            "Connecting core banking for lookups and actions comes after the first release.",
            "later",
            "Not in the first release: the AI drafts replies for people to approve.",
            None,
        )
    )

    steps.append(
        _step(
            "agents",
            "Agents and workflow",
            "Label a set of your own mail and run the evaluation; the deployment must pass every gate.",
            "done" if passed_runs else "not_started",
            f"{passed_runs} passing eval run{'s' if passed_runs != 1 else ''}",
            "/admin/evals",
        )
    )

    steps.append(
        _step(
            "shadow",
            "Shadow mode",
            "The AI triages every real mail but only records its decisions, while your team works as today.",
            "done"
            if org.status in ("assisted", "live")
            else "in_progress"
            if org.status == "shadow"
            else "not_started",
            org.status,
            "/admin/pilot",
        )
    )
    steps.append(
        _step(
            "go_live",
            "Go-live",
            "Risk signs off assisted mode, then live once the pilot's KPIs hold. Replies are always drafts "
            "for your people to approve.",
            "done" if org.status == "live" else "in_progress" if org.status == "assisted" else "not_started",
            org.status if org.status in ("assisted", "live") else "",
            "/admin/pilot",
        )
    )
    done = sum(1 for s in steps if s.state == "done")
    total = sum(1 for s in steps if s.state != "later")
    return dto.OnboardingDTO(status=org.status, steps=steps, done=done, total=total)  # type: ignore[arg-type]
