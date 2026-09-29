"""Members and roles, invitations, and the tenant's permission overrides.

Invariants:
- Exactly one role per member (memberships primary key + CHECK).
- A workspace always keeps at least one admin: checked here under a per-tenant lock, and enforced again by
  a deferred constraint trigger on memberships (migration 0004) so concurrent demotions cannot both win.
- Nobody changes their own role or removes themselves (another admin has to).
- Removing a member revokes their sessions in this workspace immediately.
- Overrides are limited to delegable capabilities for staff and team leads (`rbac.policy.validate_override`).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import ROLE_LABEL, ROLES, Ctx, actor_of
from command_inbox.core.errors import conflict, forbidden, not_found
from command_inbox.core.outbox import publish
from command_inbox.db.models import Invitation, Membership, Org, RolePolicy, Session, User
from command_inbox.modules.deployments.service import user_refs
from command_inbox.modules.members.schemas import InvitationBody, MemberRoleBody, PermissionOverridesBody
from command_inbox.rbac.policy import (
    ADMIN_ONLY,
    CAPABILITIES,
    CAPABILITY_LABEL,
    DELEGABLE,
    NEVER,
    policies,
    require,
    validate_override,
)
from command_inbox.schemas import dto

# ── Members ───────────────────────────────────────────────────────────────────────────────────────────


async def _lock_members(tx: AsyncSession, org_id: str) -> None:
    await tx.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"{org_id}:members"}
    )


async def _admins(tx: AsyncSession, org_id: str) -> int:
    return int(
        (
            await tx.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.org_id == org_id, Membership.role == "admin")
            )
        ).scalar_one()
    )


def _member_dto(ctx: Ctx, m: Membership, u: User) -> dto.MemberDTO:
    return dto.MemberDTO(
        user=dto.MemberDTOUser(id=u.id, name=u.name, initials=u.initials, email=u.email),
        role=m.role,  # type: ignore[arg-type]
        title=m.title,
        joined_at=iso_ms(m.joined_at),
        last_login_at=iso_ms(u.last_login_at) if u.last_login_at else None,
        is_me=u.id == ctx.user.id,
    )


async def list_members(tx: AsyncSession, ctx: Ctx) -> list[dto.MemberDTO]:
    require(ctx, "members.manage", "manage members")
    rows = (
        await tx.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org_id)
            .order_by(User.name)
        )
    ).all()
    return [_member_dto(ctx, m, u) for m, u in rows]


async def _member(tx: AsyncSession, ctx: Ctx, user_id: str) -> tuple[Membership, User]:
    row = (
        await tx.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org_id, Membership.user_id == user_id)
            .with_for_update(of=Membership)
        )
    ).first()
    if row is None:
        raise not_found("Member")
    return row[0], row[1]


async def change_role(tx: AsyncSession, ctx: Ctx, user_id: str, body: MemberRoleBody) -> dto.MemberDTO:
    require(ctx, "members.manage", "change roles")
    if user_id == ctx.user.id:
        raise forbidden("You cannot change your own role. Ask another admin.", "own_role")
    await _lock_members(tx, ctx.org_id)
    m, u = await _member(tx, ctx, user_id)
    before = m.role
    if before == body.role:
        return _member_dto(ctx, m, u)
    if before == "admin" and await _admins(tx, ctx.org_id) <= 1:
        raise conflict("last_admin", "A workspace must keep at least one admin.")
    await tx.execute(
        update(Membership)
        .where(Membership.org_id == ctx.org_id, Membership.user_id == user_id)
        .values(role=body.role)
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="member.role_changed",
        entity="membership",
        entity_id=user_id,
        summary=f"{ctx.user.name} changed {u.name}'s role from {ROLE_LABEL[before]} to {ROLE_LABEL[body.role]}",
        data={"from": before, "to": body.role},
    )
    await publish(tx, ctx.org_id, "people.updated", {"userId": user_id})
    await tx.refresh(m)
    return _member_dto(ctx, m, u)


async def remove_member(tx: AsyncSession, ctx: Ctx, user_id: str) -> int:
    """Returns how many sessions were revoked."""
    require(ctx, "members.manage", "remove members")
    if user_id == ctx.user.id:
        raise forbidden("You cannot remove yourself from the workspace. Ask another admin.", "own_membership")
    await _lock_members(tx, ctx.org_id)
    m, u = await _member(tx, ctx, user_id)
    if m.role == "admin" and await _admins(tx, ctx.org_id) <= 1:
        raise conflict("last_admin", "A workspace must keep at least one admin.")
    await tx.execute(delete(Membership).where(Membership.org_id == ctx.org_id, Membership.user_id == user_id))
    revoked = (
        await tx.execute(
            update(Session)
            .where(Session.org_id == ctx.org_id, Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=clock.now())
            .returning(Session.id)
        )
    ).all()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="member.removed",
        entity="membership",
        entity_id=user_id,
        summary=f"{ctx.user.name} removed {u.name} ({ROLE_LABEL[m.role]}) from the workspace",
        data={"role": m.role, "revokedSessions": len(revoked)},
    )
    await publish(tx, ctx.org_id, "people.updated", {"userId": user_id})
    return len(revoked)


# ── Invitations ───────────────────────────────────────────────────────────────────────────────────────


def _invitation_state(i: Invitation) -> str:
    if i.accepted_at:
        return "accepted"
    if i.revoked_at:
        return "revoked"
    if i.expires_at <= clock.now():
        return "expired"
    return "pending"


def _invitation_dto(i: Invitation, users: dict[str, dto.UserRef]) -> dto.InvitationDTO:
    return dto.InvitationDTO(
        id=i.id,
        email=i.email,
        role=i.role,  # type: ignore[arg-type]
        state=_invitation_state(i),  # type: ignore[arg-type]
        invited_by=users.get(str(i.invited_by)),
        created_at=iso_ms(i.created_at),
        expires_at=iso_ms(i.expires_at),
        accepted_at=iso_ms(i.accepted_at) if i.accepted_at else None,
        revoked_at=iso_ms(i.revoked_at) if i.revoked_at else None,
    )


async def list_invitations(tx: AsyncSession, ctx: Ctx) -> list[dto.InvitationDTO]:
    require(ctx, "members.manage", "manage invitations")
    rows = (
        (
            await tx.execute(
                select(Invitation)
                .where(Invitation.org_id == ctx.org_id)
                .order_by(Invitation.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    users = await user_refs(tx, {r.invited_by for r in rows})
    return [_invitation_dto(r, users) for r in rows]


async def create_invitation(tx: AsyncSession, ctx: Ctx, body: InvitationBody) -> dto.InvitationDTO:
    require(ctx, "members.manage", "invite people")
    email = body.email.lower()
    now = clock.now()
    member = (
        await tx.execute(
            select(Membership.user_id)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org_id, func.lower(User.email) == email)
        )
    ).first()
    if member:
        raise conflict("already_member", f"{email} is already a member of this workspace.")
    open_ = (
        await tx.execute(
            select(Invitation)
            .where(
                Invitation.org_id == ctx.org_id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if open_ is not None:
        if open_.expires_at > now:
            raise conflict(
                "already_invited", f"{email} already has a pending invitation. Revoke it to re-invite."
            )
        open_.revoked_at = now  # an expired invitation frees the one-pending-per-email slot
        await tx.flush()
    from command_inbox.auth.invitations import issue

    org = (await tx.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one()
    inv = await issue(tx, org=org, email=email, role=body.role, inviter_name=ctx.user.name, invited_by=ctx.user.id)
    inv.expires_at = now + timedelta(days=body.expires_in_days)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="invitation.created",
        entity="invitation",
        entity_id=inv.id,
        summary=f"{ctx.user.name} invited {email} as {ROLE_LABEL[body.role]}",
        data={"email": email, "role": body.role, "expiresAt": iso_ms(inv.expires_at)},
    )
    await publish(tx, ctx.org_id, "people.updated", {"invitationId": inv.id})
    await tx.refresh(inv)
    return _invitation_dto(inv, await user_refs(tx, {inv.invited_by}))


async def revoke_invitation(tx: AsyncSession, ctx: Ctx, invitation_id: str) -> dto.InvitationDTO:
    require(ctx, "members.manage", "manage invitations")
    inv = (
        await tx.execute(
            select(Invitation)
            .where(Invitation.org_id == ctx.org_id, Invitation.id == invitation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if inv is None:
        raise not_found("Invitation")
    if inv.accepted_at or inv.revoked_at:
        raise conflict("not_pending", f"This invitation was already {_invitation_state(inv)}.")
    inv.revoked_at = clock.now()
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="invitation.revoked",
        entity="invitation",
        entity_id=inv.id,
        summary=f"{ctx.user.name} revoked the invitation for {inv.email}",
        data={"email": inv.email, "role": inv.role},
    )
    await publish(tx, ctx.org_id, "people.updated", {"invitationId": inv.id})
    return _invitation_dto(inv, await user_refs(tx, {inv.invited_by}))


# ── Permissions ───────────────────────────────────────────────────────────────────────────────────────


def _locked(role: str, capability: str) -> bool:
    return role == "admin" or capability not in DELEGABLE or capability in NEVER.get(role, frozenset())


async def permission_matrix(tx: AsyncSession, ctx: Ctx) -> dto.PermissionMatrixDTO:
    require(ctx, "rbac.manage", "change role permissions")
    rows = (
        (
            await tx.execute(
                select(RolePolicy)
                .where(RolePolicy.org_id == ctx.org_id)
                .order_by(RolePolicy.role, RolePolicy.capability)
            )
        )
        .scalars()
        .all()
    )
    overrides = {(r.role, r.capability): r for r in rows}
    allowed = {role: await policies.capabilities(tx, ctx.org_id, role) for role in ROLES}
    baseline = {role: policies.baseline(role) for role in ROLES}
    users = await user_refs(tx, {r.changed_by for r in rows})
    return dto.PermissionMatrixDTO(
        roles=list(ROLES),
        rows=[
            dto.PermissionMatrixDTORows(
                capability=cap,  # type: ignore[arg-type]
                label=CAPABILITY_LABEL[cap],
                delegable=cap in DELEGABLE,
                admin_only=cap in ADMIN_ONLY,
                cells=[
                    dto.PermissionMatrixDTORowsCells(
                        role=role,
                        allowed=cap in allowed[role],
                        baseline=cap in baseline[role],
                        override=o.effect if (o := overrides.get((role, cap))) else None,  # type: ignore[arg-type]
                        locked=_locked(role, cap),
                    )
                    for role in ROLES
                ],
            )
            for cap in CAPABILITIES
        ],
        overrides=[
            dto.RolePolicyDTO(
                role=r.role,  # type: ignore[arg-type]
                capability=r.capability,  # type: ignore[arg-type]
                effect=r.effect,  # type: ignore[arg-type]
                changed_by=users.get(str(r.changed_by)),
                changed_at=iso_ms(r.changed_at),
            )
            for r in rows
        ],
    )


async def set_overrides(tx: AsyncSession, ctx: Ctx, body: PermissionOverridesBody) -> int:
    """Apply the overrides (all or nothing). Returns how many rows changed.

    Other API processes drop their cached policy when the `rbac.updated` notification arrives (events hub);
    the caller invalidates this process's cache after commit.
    """
    require(ctx, "rbac.manage", "change role permissions")
    for o in body.overrides:
        validate_override(o.role, o.capability, o.effect or "allow")
    now = clock.now()
    changes: list[dict[str, str | None]] = []
    labels: list[str] = []
    for o in body.overrides:
        key = (
            RolePolicy.org_id == ctx.org_id,
            RolePolicy.role == o.role,
            RolePolicy.capability == o.capability,
        )
        before = (await tx.execute(select(RolePolicy.effect).where(*key))).scalar_one_or_none()
        if before == o.effect:
            continue
        if o.effect is None:
            await tx.execute(delete(RolePolicy).where(*key))
        else:
            await tx.execute(
                insert(RolePolicy)
                .values(
                    org_id=ctx.org_id,
                    role=o.role,
                    capability=o.capability,
                    effect=o.effect,
                    changed_by=ctx.user.id,
                    changed_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["org_id", "role", "capability"],
                    set_={"effect": o.effect, "changed_by": ctx.user.id, "changed_at": now},
                )
            )
        changes.append({"role": o.role, "capability": o.capability, "from": before, "to": o.effect})
        labels.append(f"{ROLE_LABEL[o.role]} · {CAPABILITY_LABEL[o.capability]}: {o.effect or 'baseline'}")
    if not changes:
        return 0
    described = "; ".join(labels)
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="rbac.overrides_changed",
        entity="role_policy",
        summary=f"{ctx.user.name} changed permissions — {described}"[:1000],
        data={"changes": changes},
    )
    await publish(tx, ctx.org_id, "rbac.updated", {"changes": len(changes)})
    return len(changes)
