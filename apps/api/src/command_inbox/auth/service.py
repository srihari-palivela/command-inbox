"""Who am I, which workspaces can I use, and my sessions."""

from __future__ import annotations

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.status import llm_status
from command_inbox.config import settings
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Actor, Ctx
from command_inbox.core.errors import forbidden, not_found, unauthorized
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Board, Membership, Org, Session, User, UserSetting
from command_inbox.schemas import dto


def org_dto(o: Org) -> dto.OrgDTO:
    return dto.OrgDTO(
        id=o.id,
        slug=o.slug,
        name=o.name,
        short=o.short,
        tint=o.tint,
        bg=o.bg,
        plan=o.plan,
        confidence_bar=o.confidence_bar,
        locale=o.locale,
        currency=o.currency,
        time_zone=o.time_zone,
        status=o.status,  # type: ignore[arg-type]
    )


async def _one(tx: AsyncSession, sql: str, **params: object) -> int:
    return int((await tx.execute(text(sql), params)).scalar() or 0)


async def build_me(ctx: Ctx, csrf_token: str) -> dto.MeDTO:
    async with global_tx() as g:
        org = await g.get(Org, ctx.org_id)
        membership = await g.get(Membership, (ctx.org_id, ctx.user.id))
        mine = (
            await g.execute(
                select(Org, Membership.role)
                .join(Membership, Membership.org_id == Org.id)
                .where(Membership.user_id == ctx.user.id)
                .order_by(Org.created_at, Org.slug)
            )
        ).all()
    if org is None or membership is None:
        raise unauthorized()

    memberships: list[dto.MembershipDTO] = []
    for o, role in mine:
        async with tenant_tx(o.id) as tx:
            boards = await _one(tx, "select count(*) from boards")
            unread = await _one(
                tx,
                """select count(*) from notifications n where not exists (
                select 1 from notification_reads r where r.notification_id = n.id and r.user_id = :u)""",
                u=ctx.user.id,
            )
        memberships.append(
            dto.MembershipDTO(org=org_dto(o), role=role, boards=boards, people=o.headcount, unread=unread)
        )

    async with tenant_tx(ctx.org_id) as tx:
        can_check = ctx.can("action.approve_checker")
        nav = dto.NavCounts(
            # Same rule as the Inbox's own count: open work assigned to me, plus maker-approved actions I can check.
            inbox=await _one(
                tx,
                """select count(*) from tickets t where t.merged_into_id is null
                and t.status not in ('resolved','closed') and (t.assignee_id = :u or (:chk and exists (
                  select 1 from action_instances ai where ai.ticket_id = t.id and ai.state = 'awaiting_checker'
                     and ai.maker_id <> :u)))""",
                u=ctx.user.id,
                chk=can_check,
            ),
            tickets=await _one(
                tx,
                """select count(*) from tickets where status not in ('closed')
                and (resolved_at is null or resolved_at > now() - interval '24 hours') and merged_into_id is null""",
            ),
            boards=await _one(tx, "select count(*) from boards"),
            alerts=await _one(tx, "select count(*) from alerts where resolved_at is null"),
            learning=await _one(
                tx,
                """select count(*) from notifications n where n.kind = 'learning' and not exists (
                select 1 from notification_reads r where r.notification_id = n.id and r.user_id = :u)""",
                u=ctx.user.id,
            ),
            agents=await _one(tx, "select count(*) from agents"),
            gaps=await _one(tx, "select count(*) from gap_tickets where closed_at is null"),
            autonomous_cells=await _one(tx, "select count(*) from autonomy_dial where level >= 2"),
        )
        us = (
            await tx.execute(
                select(UserSetting).where(
                    UserSetting.org_id == ctx.org_id, UserSetting.user_id == ctx.user.id
                )
            )
        ).scalar_one_or_none()

    provider, degraded = llm_status()
    return dto.MeDTO(
        user=dto.MeDTOUser(
            id=ctx.user.id,
            name=ctx.user.name,
            initials=ctx.user.initials,
            email=ctx.user.email,
            title=membership.title,
            pod=membership.pod,
            joined_at=iso_ms(membership.joined_at),
        ),
        org=org_dto(org),
        role=ctx.role,
        capabilities=sorted(ctx.capabilities),
        memberships=memberships,
        csrf_token=csrf_token,
        demo_mode=settings.demo_mode,
        settings=dto.SettingsDTO(prefs=(us.prefs if us else {}) or {}, signature=us.signature if us else ""),
        nav=nav,
        worker=dto.MeDTOWorker(state="degraded" if degraded else "live", provider=provider),
        features=dto.MeDTOFeatures(telephony=settings.feature_telephony),
    )


async def first_org_for(user_id: str) -> str | None:
    async with global_tx() as g:
        return (
            await g.execute(
                select(Membership.org_id)
                .join(Org, Org.id == Membership.org_id)
                .where(Membership.user_id == user_id)
                .order_by(Org.created_at, Org.slug)
                .limit(1)
            )
        ).scalar_one_or_none()


async def demo_user(email: str) -> User:
    """Passwordless demo sign-in. Disabled outside demo mode; production signs in through Keycloak."""
    if not settings.demo_mode:
        raise forbidden("Sign in with your organisation's single sign-on.", "sso_required")
    async with global_tx() as g:
        user = (await g.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    if user is None:
        raise unauthorized("No workspace access for that email. Ask your administrator.")
    return user


async def record_sign_in(user: User, org_id: str, device: str, via: str) -> None:
    async with tenant_tx(org_id) as tx:
        await audit(
            tx,
            org_id,
            actor=Actor("user", user.id, user.name, user.initials),
            action="session.created",
            entity="session",
            summary=f"{user.name} signed in",
            data={"device": device, "via": via},
        )


async def _audit_session(ctx: Ctx, org_id: str, action: str, summary: str, data: dict | None = None) -> None:
    """Session and identity events go into the tenant's audit chain like any other change."""
    async with tenant_tx(org_id) as tx:
        await audit(
            tx,
            org_id,
            actor=Actor("user", ctx.user.id, ctx.user.name, ctx.user.initials),
            action=action,
            entity="session",
            entity_id=ctx.session_id,
            summary=summary,
            data=data or {},
        )


async def switch_org(ctx: Ctx, org_id: str) -> None:
    async with global_tx() as g:
        if await g.get(Membership, (org_id, ctx.user.id)) is None:
            raise forbidden("You are not a member of that workspace.")
        await g.execute(update(Session).where(Session.id == ctx.session_id).values(org_id=org_id))
    await _audit_session(ctx, org_id, "session.org_switched", f"{ctx.user.name} switched into this workspace")


async def logout(ctx: Ctx) -> None:
    async with global_tx() as g:
        await g.execute(update(Session).where(Session.id == ctx.session_id).values(revoked_at=clock.now()))
    await _audit_session(ctx, ctx.org_id, "session.ended", f"{ctx.user.name} signed out")


async def list_sessions(ctx: Ctx) -> list[dto.SessionDTO]:
    async with global_tx() as g:
        rows = (
            (
                await g.execute(
                    select(Session)
                    .where(
                        Session.user_id == ctx.user.id,
                        Session.revoked_at.is_(None),
                        Session.expires_at > clock.now(),
                    )
                    .order_by(Session.last_seen_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return [
        dto.SessionDTO(
            id=r.id,
            device=r.device or "Browser",
            location=r.location,
            last_seen_at=iso_ms(r.last_seen_at),
            current=r.id == ctx.session_id,
        )
        for r in rows
    ]


async def revoke_session(ctx: Ctx, session_id: str) -> None:
    async with global_tx() as g:
        res = await g.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == ctx.user.id)
            .values(revoked_at=clock.now())
            .returning(Session.id)
        )
        if res.first() is None:
            raise not_found("Session")
    await _audit_session(ctx, ctx.org_id, "session.revoked", f"{ctx.user.name} signed out another device")


async def revoke_other_sessions(ctx: Ctx) -> int:
    async with global_tx() as g:
        res = await g.execute(
            update(Session)
            .where(Session.user_id == ctx.user.id, Session.revoked_at.is_(None), Session.id != ctx.session_id)
            .values(revoked_at=clock.now())
            .returning(Session.id)
        )
        n = len(res.all())
    await _audit_session(
        ctx, ctx.org_id, "session.revoked_others", f"{ctx.user.name} signed out {n} other devices"
    )
    return n


async def demo_users() -> list[dto.DemoUserDTO]:
    if not settings.demo_mode:
        return []
    async with global_tx() as g:
        rows = (
            await g.execute(
                select(User.email, User.name, Membership.role, Membership.title)
                .join(Membership, Membership.user_id == User.id)
                .join(Org, (Org.id == Membership.org_id) & (Org.slug == "apex"))
            )
        ).all()
    order = {"staff": 0, "lead": 1, "admin": 2}
    rows = sorted(rows, key=lambda r: (order[r.role], r.name))
    return [dto.DemoUserDTO(email=r.email, name=r.name, role=r.role, title=r.title) for r in rows]


async def org_choices(email: str | None) -> list[dto.OrgChoiceDTO]:
    async with global_tx() as g:
        orgs = (await g.execute(select(Org).order_by(Org.created_at, Org.slug))).scalars().all()
        user = (
            (await g.execute(select(User).where(User.email == email))).scalar_one_or_none() if email else None
        )
        roles = {}
        if user is not None:
            roles = dict(
                (
                    await g.execute(
                        select(Membership.org_id, Membership.role).where(Membership.user_id == user.id)
                    )
                ).all()
            )
    out = []
    for o in orgs:
        async with tenant_tx(o.id) as tx:
            boards = int((await tx.execute(select(func.count()).select_from(Board))).scalar() or 0)
        out.append(
            dto.OrgChoiceDTO(
                **org_dto(o).model_dump(), role=roles.get(o.id), boards=boards, people=o.headcount
            )
        )
    return out
