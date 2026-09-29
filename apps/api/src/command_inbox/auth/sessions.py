"""Opaque server-side sessions.

The browser holds only a random token in an HttpOnly cookie; the database holds its SHA-256. Sessions slide
(last-seen written at most once a minute), can be revoked individually or all-but-current, and carry a
per-session CSRF token that every state-changing request must echo in `x-csrf-token`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.core.context import Ctx, UserRef
from command_inbox.core.crypto import random_token, sha256
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Membership, Session, User
from command_inbox.rbac.policy import policies


def cookie_name() -> str:
    # __Host- binds the cookie to this exact origin over HTTPS (no Domain, Path=/); plain name in local HTTP dev.
    return "__Host-ci_session" if settings.cookie_secure else "ci_session"


SESSION_COOKIE = cookie_name()


@dataclass(frozen=True, slots=True)
class Resolved:
    ctx: Ctx
    csrf_token: str


async def resolve_session(token: str, request_id: str) -> Resolved | None:
    now = clock.now()
    async with global_tx() as tx:
        row = (
            await tx.execute(
                select(Session, User, Membership.role)
                .join(User, User.id == Session.user_id)
                .join(
                    Membership,
                    (Membership.user_id == Session.user_id) & (Membership.org_id == Session.org_id),
                )
                .where(
                    Session.token_hash == sha256(token),
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                )
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        sess, user, role = row
        # Absolute lifetime and idle timeout: a stolen cookie cannot be kept alive by use.
        absolute_end = sess.created_at + timedelta(hours=settings.session_ttl_hours)
        idle = timedelta(minutes=settings.session_idle_minutes)
        if now - sess.last_seen_at > idle or now >= absolute_end:
            await tx.execute(update(Session).where(Session.id == sess.id).values(revoked_at=now))
            return None
        if (now - sess.last_seen_at).total_seconds() > 60:
            await tx.execute(
                update(Session)
                .where(Session.id == sess.id)
                .values(last_seen_at=now, expires_at=min(absolute_end, now + idle))
            )
    async with tenant_tx(sess.org_id) as ttx:
        caps = await policies.capabilities(ttx, sess.org_id, role)
    ctx = Ctx(
        org_id=sess.org_id,
        session_id=sess.id,
        request_id=request_id,
        role=role,
        user=UserRef(id=user.id, name=user.name, initials=user.initials, email=user.email),
        capabilities=caps,
    )
    return Resolved(ctx=ctx, csrf_token=sess.csrf_token)


async def create_session(user_id: str, org_id: str, *, user_agent: str, ip: str | None) -> tuple[str, str]:
    """Returns (token, csrf_token). Always a fresh token: signing in never reuses a prior session (no fixation)."""
    from command_inbox.auth.device import device_label, location_label

    token, csrf = random_token(), random_token(18)
    now = clock.now()
    async with global_tx() as tx:
        tx.add(
            Session(
                user_id=user_id,
                org_id=org_id,
                token_hash=sha256(token),
                csrf_token=csrf,
                user_agent=user_agent[:400],
                device=device_label(user_agent),
                location=location_label(ip),
                ip=ip or "",
                expires_at=now + timedelta(minutes=settings.session_idle_minutes),
            )
        )
        await tx.execute(update(User).where(User.id == user_id).values(last_login_at=now))
    return token, csrf


async def rotate(session_id: str) -> tuple[str, str]:
    """New token and CSRF value for the same session, after its user, workspace or role changes."""
    token, csrf = random_token(), random_token(18)
    async with global_tx() as tx:
        await tx.execute(
            update(Session).where(Session.id == session_id).values(token_hash=sha256(token), csrf_token=csrf)
        )
    return token, csrf
