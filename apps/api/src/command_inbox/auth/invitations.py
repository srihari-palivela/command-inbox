"""Tokenised invitations: the emailed link is the invitee's proof, bound to their email address.

- `issue` creates the invitation with the SHA-256 of a random single-use token (the token itself exists only
  in the email) and queues the email in the same transaction.
- `preview` answers the public accept page: tenant name, inviter, role and state, nothing else.
- An invitation is accepted through SSO (`/v1/auth/oidc/login?invite=…`): the identity provider's verified
  email must equal the invited email. In development without SSO, `accept_direct` stands in for it.
- A tenant's first admin accepting moves the tenant from provisioned to onboarding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.config import settings
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import ROLE_LABEL, Actor
from command_inbox.core.crypto import random_token, sha256
from command_inbox.core.email import queue_email
from command_inbox.core.errors import conflict, forbidden, not_found
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Invitation, Membership, Org, User
from command_inbox.schemas import platform_dto as pdto


def accept_url(token: str) -> str:
    return f"{settings.web_origin.rstrip('/')}/accept?token={token}"


def state_of(i: Invitation) -> str:
    if i.accepted_at:
        return "accepted"
    if i.revoked_at:
        return "revoked"
    if i.expires_at <= clock.now():
        return "expired"
    return "pending"


async def issue(
    tx: AsyncSession,
    *,
    org: Org,
    email: str,
    role: str,
    inviter_name: str,
    name: str = "",
    invited_by: str | None = None,
    invited_by_operator: str | None = None,
) -> Invitation:
    token = random_token(32)
    now = clock.now()
    inv = Invitation(
        org_id=org.id,
        email=email.lower(),
        name=name,
        role=role,
        invited_by=invited_by,
        invited_by_operator=invited_by_operator,
        token_hash=sha256(token),
        expires_at=now + timedelta(hours=settings.invitation_ttl_hours),
        sent_at=now,
        send_count=1,
    )
    tx.add(inv)
    await tx.flush()
    greeting = f"Hello {name}," if name else "Hello,"
    days = max(1, settings.invitation_ttl_hours // 24)
    body = (
        f"{greeting}\n\n"
        f"{inviter_name} has invited you to join {org.name} on Command Inbox as {ROLE_LABEL.get(role, role)}.\n\n"
        f"Accept the invitation (the link works once, for {days} day{'s' if days != 1 else ''}):\n"
        f"{accept_url(token)}\n\n"
        "You will sign in with your organisation's account. If you were not expecting this, ignore this email.\n"
    )
    await queue_email(
        tx,
        tenant_id=org.id,
        template="invitation",
        to=inv.email,
        subject=f"You're invited to {org.name} on Command Inbox",
        text=body,
    )
    return inv


@dataclass(frozen=True, slots=True)
class Found:
    invitation: Invitation
    org: Org


async def lookup(token: str) -> Found | None:
    """Cross-tenant lookup by token hash (the one narrow SECURITY DEFINER function), then a tenant read."""
    async with global_tx() as g:
        row = (
            await g.execute(
                text("select invitation_id, org_id from ci_invitation_by_token(:h)"), {"h": sha256(token)}
            )
        ).first()
        if row is None:
            return None
        org = (await g.execute(select(Org).where(Org.id == row.org_id))).scalar_one()
        g.expunge(org)
    async with tenant_tx(str(row.org_id)) as tx:
        inv = (await tx.execute(select(Invitation).where(Invitation.id == row.invitation_id))).scalar_one()
        tx.expunge(inv)
    return Found(inv, org)


async def _inviter_name(inv: Invitation) -> str:
    async with global_tx() as g:
        if inv.invited_by:
            return (
                await g.execute(select(User.name).where(User.id == inv.invited_by))
            ).scalar_one_or_none() or ""
        if inv.invited_by_operator:
            from command_inbox.db.models import PlatformOperator

            op = (
                await g.execute(
                    select(PlatformOperator.name).where(PlatformOperator.id == inv.invited_by_operator)
                )
            ).scalar_one_or_none()
            return f"{op} (Command Inbox)" if op else "Command Inbox"
    return ""


async def preview(token: str) -> pdto.InvitationPreviewDTO:
    found = await lookup(token)
    if found is None:
        raise not_found("Invitation")
    inv, org = found.invitation, found.org
    return pdto.InvitationPreviewDTO(
        tenant_name=org.name,
        email=inv.email,
        name=inv.name,
        role=inv.role,  # type: ignore[arg-type]
        invited_by=await _inviter_name(inv),
        expires_at=iso_ms(inv.expires_at),
        state=state_of(inv),  # type: ignore[arg-type]
        sign_in="sso" if settings.oidc_enabled else "direct",
        can_bootstrap=can_bootstrap(org),
    )


def can_bootstrap(org: Org) -> bool:
    """A first-time account can be created only while the bank's own SSO is not connected yet."""
    from command_inbox.platform import keycloak

    return settings.oidc_enabled and keycloak.configured() and not org.sso_idp_alias


async def bootstrap_account(token: str) -> str:
    """Create the invitee's local sign-in (first admin, before bank SSO) and email its set-up link."""
    from command_inbox.platform import keycloak

    found = usable(await lookup(token))
    inv, org = found.invitation, found.org
    if not can_bootstrap(org):
        raise forbidden("Sign in with your organisation's account.", "bootstrap_unavailable")
    async with tenant_tx(org.id) as tx:
        tries = (
            await tx.execute(
                text(
                    "select count(*) from audit_events where org_id = :o and action = 'invitation.bootstrap_account' "
                    "and data->>'invitationId' = :i"
                ),
                {"o": org.id, "i": inv.id},
            )
        ).scalar_one()
        if tries >= 3:
            raise conflict(
                "bootstrap_limit", "The set-up email was already sent three times. Check your inbox."
            )
        result = await keycloak.ensure_bootstrap_user(
            email=inv.email, name=inv.name or inv.email, redirect_uri=accept_url(token)
        )
        await audit(
            tx,
            org.id,
            actor=Actor("system", None, "Command Inbox", "SY"),
            action="invitation.bootstrap_account",
            entity="invitation",
            entity_id=inv.id,
            summary=f"A first-time sign-in was set up for {inv.email} ({result.state})",
            data={"invitationId": inv.id, "state": result.state},
        )
    if result.state == "exists":
        raise conflict("account_exists", result.detail)
    return result.detail


def usable(found: Found | None) -> Found:
    if found is None:
        raise not_found("Invitation")
    state = state_of(found.invitation)
    if state != "pending":
        raise conflict(f"invitation_{state}", f"This invitation was already {state}. Ask for a new one.")
    if found.org.status in ("suspended", "archived", "draft"):
        raise forbidden("This workspace is not open for sign-in.", "tenant_unavailable")
    return found


async def complete(found: Found, user: User, *, idp: str | None) -> None:
    """Membership, invitation accepted, tenant audit, and the first admin moving the tenant to onboarding."""
    inv, org = found.invitation, found.org
    now = clock.now()
    async with global_tx() as g:
        existing = (
            await g.execute(
                select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id)
            )
        ).scalar_one_or_none()
        if existing is None:
            g.add(
                Membership(org_id=org.id, user_id=user.id, role=inv.role, title=ROLE_LABEL.get(inv.role, ""))
            )
        promoted = False
        if inv.role == "admin":
            promoted = bool(
                (
                    await g.execute(
                        update(Org)
                        .where(Org.id == org.id, Org.status == "provisioned")
                        .values(status="onboarding", status_changed_at=now)
                        .returning(Org.id)
                    )
                ).first()
            )
        if promoted:
            from command_inbox.platform.audit import platform_audit

            await platform_audit(
                g,
                operator_id=None,
                operator_email="system",
                action="tenant.onboarding",
                tenant_id=org.id,
                summary=f"{user.name} accepted the first-admin invitation; {org.name} is onboarding",
                data={"from": "provisioned", "to": "onboarding"},
            )
    async with tenant_tx(org.id) as tx:
        await grant_default_clearances(tx, org.id, user.id, inv.role)
        await tx.execute(update(Invitation).where(Invitation.id == inv.id).values(accepted_at=now))
        await audit(
            tx,
            org.id,
            actor=Actor("user", user.id, user.name, user.initials),
            action="invitation.accepted",
            entity="membership",
            entity_id=user.id,
            summary=f"{user.name} joined as {ROLE_LABEL.get(inv.role, inv.role)}",
            data={"role": inv.role, "idp": idp, "invitationId": inv.id},
        )


DEFAULT_CLEARANCE = {
    "admin": 3,
    "lead": 3,
    "staff": 2,
}  # approve, approve, resolve; admins change it in People


async def grant_default_clearances(tx: AsyncSession, org_id: str, user_id: str, role: str) -> None:
    """A new member can work every team's mail at their role's default level; nothing is overwritten."""
    from sqlalchemy.dialects.postgresql import insert

    from command_inbox.db.models import Clearance, Department

    departments = (await tx.execute(select(Department.id).where(Department.org_id == org_id))).scalars().all()
    for dept in departments:
        await tx.execute(
            insert(Clearance)
            .values(org_id=org_id, user_id=user_id, department_id=dept, level=DEFAULT_CLEARANCE.get(role, 2))
            .on_conflict_do_nothing()
        )


def initials_of(name: str) -> str:
    return "".join(p[0] for p in name.replace(".", " ").split()[:2]).upper() or "?"


async def accept_direct(token: str) -> tuple[User, str]:
    """Development only (demo mode without SSO): the link alone signs the invitee in."""
    if not settings.demo_mode or settings.oidc_enabled:
        raise forbidden(
            "Accept this invitation by signing in with your organisation's account.", "sso_required"
        )
    found = usable(await lookup(token))
    inv = found.invitation
    async with global_tx() as g:
        user = (await g.execute(select(User).where(User.email == inv.email))).scalar_one_or_none()
        if user is None:
            name = inv.name or inv.email.split("@")[0]
            user = User(email=inv.email, name=name, initials=initials_of(name))
            g.add(user)
            await g.flush()
        g.expunge(user)
    await complete(found, user, idp=None)
    return user, found.org.id
