"""Tenant lifecycle, as the platform console drives it.

    draft ─provision─▶ provisioning ─steps done─▶ provisioned ─first admin accepts─▶ onboarding
    onboarding → shadow → assisted → live            (moved by the bank's pilot sign-off: modules/pilot)
    any open status ─suspend─▶ suspended ─resume─▶ the status it had
    draft / provisioned / onboarding / suspended ─archive─▶ archived

Suspending or archiving revokes every session of the tenant at once (and sessions of a suspended tenant stop
resolving), so the bank's people are out immediately. Every action is written to the platform audit chain,
and to the tenant's own chain so the bank can see what the vendor did.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.errors import conflict, not_found
from command_inbox.core.jobs import enqueue
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import (
    Invitation,
    Membership,
    Org,
    PlatformAuditEvent,
    PlatformOperator,
    Session,
    TenantKey,
    TenantProvisioning,
)
from command_inbox.platform.audit import platform_audit
from command_inbox.platform.provisioning import STEP_LABEL, STEPS, operator_actor
from command_inbox.platform.rbac import OperatorCtx, require_platform
from command_inbox.platform.schemas import CreateTenantBody, ReinviteBody, TenantReasonBody
from command_inbox.schemas import platform_dto as pdto

OPEN = ("provisioned", "onboarding", "shadow", "assisted", "live")
TINTS = [("#3D4451", "#ECEEF1"), ("#1B6B49", "#E9F2EE"), ("#4B4EC8", "#EDECF9"), ("#8A6413", "#F6EEDF")]


def _initials(name: str) -> str:
    return "".join(w[0] for w in name.split()[:2]).upper() or "T"


def allowed_actions(status: str, steps_failed: bool, admins: int) -> list[str]:
    actions: list[str] = []
    if status == "draft" or (status == "provisioning" and steps_failed):
        actions.append("provision")
    if status in ("provisioned", "onboarding") or (status in OPEN and admins == 0):
        actions.append("reinvite")
    if status in OPEN:
        actions.append("suspend")
    if status == "suspended":
        actions.append("resume")
    if status in ("draft", "provisioned", "onboarding", "suspended"):
        actions.append("archive")
    return actions


def _actions_for(op: OperatorCtx, status: str, failed: bool, admins: int) -> list[str]:
    acts = allowed_actions(status, failed, admins)
    if not op.can("tenants.lifecycle"):
        acts = [a for a in acts if a == "reinvite"]
    if not op.can("tenants.invite"):
        acts = [a for a in acts if a != "reinvite"]
    return acts


async def _step_rollup(g: AsyncSession) -> dict[str, str | None]:
    rows = (await g.execute(select(TenantProvisioning.tenant_id, TenantProvisioning.state))).all()
    by: dict[str, list[str]] = {}
    for tid, state in rows:
        by.setdefault(str(tid), []).append(state)
    out: dict[str, str | None] = {}
    for tid, states in by.items():
        if "failed" in states:
            out[tid] = "failed"
        elif "running" in states:
            out[tid] = "running"
        elif "pending" in states:
            out[tid] = "pending"
        else:
            out[tid] = None
    return out


async def _people(g: AsyncSession) -> dict[str, tuple[int, int]]:
    rows = (
        await g.execute(
            select(
                Membership.org_id,
                func.count(),
                func.count().filter(Membership.role == "admin"),
            ).group_by(Membership.org_id)
        )
    ).all()
    return {str(r[0]): (int(r[1]), int(r[2])) for r in rows}


async def _mailboxes(g: AsyncSession) -> dict[str, int]:
    rows = (await g.execute(text("select org_id, mailboxes from ci_tenant_counts()"))).all()
    return {str(r.org_id): int(r.mailboxes) for r in rows}


def _summary(o: Org, people: tuple[int, int], mailboxes: int, prov: str | None) -> pdto.TenantSummaryDTO:
    return pdto.TenantSummaryDTO(
        id=o.id,
        slug=o.slug,
        name=o.name,
        legal_name=o.legal_name,
        status=o.status,  # type: ignore[arg-type]
        region=o.region,
        plan=o.plan,
        created_at=iso_ms(o.created_at),
        status_changed_at=iso_ms(o.status_changed_at),
        members=people[0],
        admins=people[1],
        mailboxes=mailboxes,
        provisioning=prov,  # type: ignore[arg-type]
    )


async def list_tenants(op: OperatorCtx) -> list[pdto.TenantSummaryDTO]:
    require_platform(op, "tenants.view", "view tenants")
    async with global_tx() as g:
        orgs = (await g.execute(select(Org).order_by(Org.created_at.desc()))).scalars().all()
        people, boxes, prov = await _people(g), await _mailboxes(g), await _step_rollup(g)
    return [_summary(o, people.get(o.id, (0, 0)), boxes.get(o.id, 0), prov.get(o.id)) for o in orgs]


def _limits(raw: dict[str, Any]) -> pdto.TenantLimitsDTO:
    return pdto.TenantLimitsDTO(
        mailboxes=int(raw.get("mailboxes", 1)),
        seats=int(raw.get("seats", 25)),
        monthly_mail=int(raw.get("monthlyMail", 20_000)),
        model_spend_cap_minor=int(raw.get("modelSpendCapMinor", 5_000_000)),
        storage_gb=int(raw.get("storageGb", 50)),
        api_per_minute=int(raw.get("apiPerMinute", 3000)),
    )


def _audit_dto(e: PlatformAuditEvent) -> pdto.PlatformAuditEventDTO:
    return pdto.PlatformAuditEventDTO(
        seq=e.seq,
        at=iso_ms(e.at),
        operator_email=e.operator_email,
        action=e.action,
        tenant_id=str(e.tenant_id) if e.tenant_id else None,
        summary=e.summary,
        data=e.data or {},
    )


async def tenant_detail(op: OperatorCtx, tenant_id: str) -> pdto.TenantDetailDTO:
    require_platform(op, "tenants.view", "view tenants")
    async with global_tx() as g:
        o = (await g.execute(select(Org).where(Org.id == tenant_id))).scalar_one_or_none()
        if o is None:
            raise not_found("Tenant")
        people = (await _people(g)).get(o.id, (0, 0))
        boxes = (await _mailboxes(g)).get(o.id, 0)
        prov = (await _step_rollup(g)).get(o.id)
        steps = (
            (
                await g.execute(
                    select(TenantProvisioning)
                    .where(TenantProvisioning.tenant_id == o.id)
                    .order_by(TenantProvisioning.position)
                )
            )
            .scalars()
            .all()
        )
        keys = (
            (
                await g.execute(
                    select(TenantKey).where(TenantKey.tenant_id == o.id).order_by(TenantKey.version)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await g.execute(
                    select(PlatformAuditEvent)
                    .where(PlatformAuditEvent.tenant_id == o.id)
                    .order_by(PlatformAuditEvent.seq.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        operators = {
            str(r.id): r.name
            for r in (await g.execute(select(PlatformOperator.id, PlatformOperator.name))).all()
        }
    async with tenant_tx(o.id) as tx:
        invites = (
            (
                await tx.execute(
                    select(Invitation).where(Invitation.org_id == o.id).order_by(Invitation.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    from command_inbox.auth.invitations import state_of

    summary = _summary(o, people, boxes, prov)
    return pdto.TenantDetailDTO(
        **summary.model_dump(),
        locale=o.locale,
        currency=o.currency,
        time_zone=o.time_zone,
        data_residency=o.data_residency,
        support_email=o.support_email,
        email_domains=list(o.sso_email_domains or []),
        sso_idp_alias=o.sso_idp_alias,
        limits=_limits(o.limits or {}),
        keys=[
            pdto.TenantKeyDTO(
                version=k.version, state=k.state, kek_ref=k.kek_ref, created_at=iso_ms(k.created_at)
            )  # type: ignore[arg-type]
            for k in keys
        ],
        steps=[
            pdto.ProvisioningStepDTO(
                step=s.step,
                label=STEP_LABEL.get(s.step, s.step),
                state=s.state,  # type: ignore[arg-type]
                attempts=s.attempts,
                detail=s.detail,
                updated_at=iso_ms(s.updated_at),
            )
            for s in steps
        ],
        invitations=[
            pdto.TenantInvitationDTO(
                id=i.id,
                email=i.email,
                name=i.name,
                role=i.role,  # type: ignore[arg-type]
                state=state_of(i),  # type: ignore[arg-type]
                created_at=iso_ms(i.created_at),
                expires_at=iso_ms(i.expires_at),
                sent_at=iso_ms(i.sent_at) if i.sent_at else None,
                send_count=i.send_count,
                invited_by=operators.get(str(i.invited_by_operator), "Command Inbox")
                if i.invited_by_operator
                else "Workspace admin",
            )
            for i in invites
        ],
        audit=[_audit_dto(e) for e in events],
        actions=_actions_for(op, o.status, prov == "failed", people[1]),  # type: ignore[arg-type]
    )


async def _enqueue_provisioning(
    g: AsyncSession, op: OperatorCtx, org: Org, admin: dict[str, str] | None
) -> None:
    await enqueue(
        g,
        org.id,
        "provision_tenant",
        {"tenantId": org.id, "operatorId": op.id, "operatorName": op.name, "admin": admin},
        dedupe_key=f"provision:{uuid.uuid4()}",
    )


async def create_tenant(op: OperatorCtx, body: CreateTenantBody) -> pdto.TenantDetailDTO:
    require_platform(op, "tenants.create", "create tenants")
    tint, bg = TINTS[sum(map(ord, body.slug)) % len(TINTS)]
    admin = {"name": body.admin.name, "email": body.admin.email}
    async with global_tx() as g:
        if (await g.execute(select(Org.id).where(Org.slug == body.slug))).first():
            raise conflict("slug_taken", f"A tenant with the slug {body.slug!r} already exists.")
        org = Org(
            slug=body.slug,
            name=body.name,
            short=_initials(body.name),
            tint=tint,
            bg=bg,
            plan=body.plan,
            legal_name=body.legal_name,
            region=body.region,
            data_residency=body.data_residency,
            support_email=body.support_email or "",
            locale=body.locale,
            currency=body.currency,
            time_zone=body.time_zone,
            sso_email_domains=sorted(set(body.email_domains)),
            limits=body.limits.model_dump(by_alias=True),
            status="provisioning" if body.provision else "draft",
            created_by_operator=op.id,
        )
        g.add(org)
        await g.flush()
        for position, (step, _label) in enumerate(STEPS):
            g.add(TenantProvisioning(tenant_id=org.id, step=step, position=position))
        await platform_audit(
            g,
            operator_id=op.id,
            operator_email=op.email,
            action="tenant.created",
            tenant_id=org.id,
            summary=f"{op.name} created the tenant {body.name} ({body.slug})",
            data={"slug": body.slug, "region": body.region, "plan": body.plan, "admin": admin},
        )
        if body.provision:
            await _enqueue_provisioning(g, op, org, admin)
    async with tenant_tx(org.id) as tx:
        await audit(
            tx,
            org.id,
            actor=operator_actor(op.name),
            action="tenant.created",
            entity="org",
            entity_id=org.id,
            summary=f"The workspace {body.name} was created",
            data={"region": body.region, "plan": body.plan},
        )
    return await tenant_detail(op, org.id)


async def _load_for_action(g: AsyncSession, op: OperatorCtx, tenant_id: str, action: str) -> tuple[Org, int]:
    org = (await g.execute(select(Org).where(Org.id == tenant_id).with_for_update())).scalar_one_or_none()
    if org is None:
        raise not_found("Tenant")
    admins = (await _people(g)).get(org.id, (0, 0))[1]
    failed = (await _step_rollup(g)).get(org.id) == "failed"
    if action not in allowed_actions(org.status, failed, admins):
        raise conflict(
            "invalid_transition", f"The {action} action is not available while the tenant is {org.status}."
        )
    return org, admins


async def _tenant_note(org_id: str, op: OperatorCtx, action: str, summary: str, data: dict[str, Any]) -> None:
    async with tenant_tx(org_id) as tx:
        await audit(
            tx,
            org_id,
            actor=operator_actor(op.name),
            action=action,
            entity="org",
            entity_id=org_id,
            summary=summary,
            data=data,
        )


async def provision(op: OperatorCtx, tenant_id: str) -> pdto.TenantDetailDTO:
    require_platform(op, "tenants.lifecycle", "provision tenants")
    async with global_tx() as g:
        org, _ = await _load_for_action(g, op, tenant_id, "provision")
        await g.execute(
            update(TenantProvisioning)
            .where(
                TenantProvisioning.tenant_id == org.id, TenantProvisioning.state.in_(("failed", "running"))
            )
            .values(state="pending", updated_at=clock.now())
        )
        created = (
            await g.execute(
                select(PlatformAuditEvent.data)
                .where(PlatformAuditEvent.tenant_id == org.id, PlatformAuditEvent.action == "tenant.created")
                .limit(1)
            )
        ).scalar_one_or_none() or {}
        org.status, org.status_changed_at = "provisioning", clock.now()
        await _enqueue_provisioning(g, op, org, created.get("admin"))
        await platform_audit(
            g,
            operator_id=op.id,
            operator_email=op.email,
            action="tenant.provision",
            tenant_id=org.id,
            summary=f"{op.name} started provisioning {org.name}",
        )
    return await tenant_detail(op, tenant_id)


async def _revoke_sessions(g: AsyncSession, org_id: str) -> int:
    rows = (
        await g.execute(
            update(Session)
            .where(Session.org_id == org_id, Session.revoked_at.is_(None))
            .values(revoked_at=clock.now())
            .returning(Session.id)
        )
    ).all()
    return len(rows)


async def suspend(op: OperatorCtx, tenant_id: str, body: TenantReasonBody) -> pdto.TenantDetailDTO:
    require_platform(op, "tenants.lifecycle", "suspend tenants")
    async with global_tx() as g:
        org, _ = await _load_for_action(g, op, tenant_id, "suspend")
        before = org.status
        org.status_before_suspend, org.status, org.status_changed_at = before, "suspended", clock.now()
        revoked = await _revoke_sessions(g, org.id)
        await platform_audit(
            g,
            operator_id=op.id,
            operator_email=op.email,
            action="tenant.suspended",
            tenant_id=org.id,
            summary=f"{op.name} suspended {org.name}: {body.reason}",
            data={"from": before, "reason": body.reason, "revokedSessions": revoked},
        )
    await _tenant_note(
        tenant_id,
        op,
        "tenant.suspended",
        f"The workspace was suspended: {body.reason}",
        {"reason": body.reason, "revokedSessions": revoked},
    )
    return await tenant_detail(op, tenant_id)


async def resume(op: OperatorCtx, tenant_id: str, body: TenantReasonBody) -> pdto.TenantDetailDTO:
    require_platform(op, "tenants.lifecycle", "resume tenants")
    async with global_tx() as g:
        org, _ = await _load_for_action(g, op, tenant_id, "resume")
        to = org.status_before_suspend or "onboarding"
        org.status, org.status_before_suspend, org.status_changed_at = to, None, clock.now()
        await platform_audit(
            g,
            operator_id=op.id,
            operator_email=op.email,
            action="tenant.resumed",
            tenant_id=org.id,
            summary=f"{op.name} resumed {org.name}: {body.reason}",
            data={"to": to, "reason": body.reason},
        )
    await _tenant_note(
        tenant_id, op, "tenant.resumed", f"The workspace was resumed: {body.reason}", {"reason": body.reason}
    )
    return await tenant_detail(op, tenant_id)


async def archive(op: OperatorCtx, tenant_id: str, body: TenantReasonBody) -> pdto.TenantDetailDTO:
    require_platform(op, "tenants.lifecycle", "archive tenants")
    async with global_tx() as g:
        org, _ = await _load_for_action(g, op, tenant_id, "archive")
        before = org.status
        org.status, org.status_before_suspend, org.status_changed_at = "archived", None, clock.now()
        revoked = await _revoke_sessions(g, org.id)
        await platform_audit(
            g,
            operator_id=op.id,
            operator_email=op.email,
            action="tenant.archived",
            tenant_id=org.id,
            summary=f"{op.name} archived {org.name}: {body.reason}",
            data={"from": before, "reason": body.reason, "revokedSessions": revoked},
        )
    async with tenant_tx(tenant_id) as tx:
        await tx.execute(
            update(Invitation)
            .where(
                Invitation.org_id == tenant_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .values(revoked_at=clock.now())
        )
    await _tenant_note(
        tenant_id,
        op,
        "tenant.archived",
        f"The workspace was archived: {body.reason}",
        {"reason": body.reason},
    )
    return await tenant_detail(op, tenant_id)


async def reinvite(op: OperatorCtx, tenant_id: str, body: ReinviteBody) -> pdto.TenantDetailDTO:
    """Revoke any pending admin invitation and send a new one (a lost email, a wrong address, a new person)."""
    require_platform(op, "tenants.invite", "invite a tenant's admin")
    from command_inbox.auth.invitations import issue

    async with global_tx() as g:
        org, _ = await _load_for_action(g, op, tenant_id, "reinvite")
        g.expunge(org)
    async with tenant_tx(tenant_id) as tx:
        revoked = (
            await tx.execute(
                update(Invitation)
                .where(
                    Invitation.org_id == tenant_id,
                    Invitation.role == "admin",
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
                .values(revoked_at=clock.now())
                .returning(Invitation.email)
            )
        ).all()
        await issue(
            tx,
            org=org,
            email=body.email,
            name=body.name,
            role="admin",
            inviter_name=op.name,
            invited_by_operator=op.id,
        )
        await audit(
            tx,
            tenant_id,
            actor=operator_actor(op.name),
            action="invitation.created",
            entity="invitation",
            summary=f"An admin invitation was sent to {body.email}",
            data={"email": body.email, "role": "admin", "replaced": [r[0] for r in revoked]},
        )
    async with global_tx() as g:
        await platform_audit(
            g,
            operator_id=op.id,
            operator_email=op.email,
            action="tenant.admin_invited",
            tenant_id=tenant_id,
            summary=f"{op.name} invited {body.email} as the admin of {org.name}",
            data={"email": body.email, "replaced": [r[0] for r in revoked]},
        )
    return await tenant_detail(op, tenant_id)


async def audit_log(op: OperatorCtx, tenant_id: str | None, limit: int) -> list[pdto.PlatformAuditEventDTO]:
    require_platform(op, "audit.view", "view the platform audit log")
    async with global_tx() as g:
        q = select(PlatformAuditEvent).order_by(PlatformAuditEvent.seq.desc()).limit(limit)
        if tenant_id:
            q = q.where(PlatformAuditEvent.tenant_id == tenant_id)
        return [_audit_dto(e) for e in (await g.execute(q)).scalars().all()]
