"""Operator sessions: opaque token in an HttpOnly cookie, SHA-256 in the database, per-session CSRF token.

Shorter-lived than tenant sessions (idle 20 minutes, absolute 8 hours by default), and a different cookie, so
a tenant session can never act on the platform API and the other way round.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.core.crypto import random_token, sha256
from command_inbox.db.engine import global_tx
from command_inbox.db.models import PlatformOperator, PlatformSession
from command_inbox.platform.rbac import CAPABILITIES, OperatorCtx


def cookie_name() -> str:
    return "__Host-ci_platform" if settings.cookie_secure else "ci_platform"


PLATFORM_COOKIE = cookie_name()


async def resolve_platform_session(token: str, request_id: str) -> tuple[OperatorCtx, str] | None:
    now = clock.now()
    async with global_tx() as tx:
        row = (
            await tx.execute(
                select(PlatformSession, PlatformOperator)
                .join(PlatformOperator, PlatformOperator.id == PlatformSession.operator_id)
                .where(
                    PlatformSession.token_hash == sha256(token),
                    PlatformSession.revoked_at.is_(None),
                    PlatformSession.expires_at > now,
                    PlatformOperator.disabled_at.is_(None),
                )
            )
        ).first()
        if row is None:
            return None
        sess, op = row
        absolute_end = sess.created_at + timedelta(hours=settings.platform_session_ttl_hours)
        idle = timedelta(minutes=settings.platform_session_idle_minutes)
        if now - sess.last_seen_at > idle or now >= absolute_end:
            await tx.execute(
                update(PlatformSession).where(PlatformSession.id == sess.id).values(revoked_at=now)
            )
            return None
        if (now - sess.last_seen_at).total_seconds() > 60:
            await tx.execute(
                update(PlatformSession)
                .where(PlatformSession.id == sess.id)
                .values(last_seen_at=now, expires_at=min(absolute_end, now + idle))
            )
    ctx = OperatorCtx(
        id=op.id,
        email=op.email,
        name=op.name,
        role=op.role,  # type: ignore[arg-type]
        session_id=sess.id,
        request_id=request_id,
        capabilities=CAPABILITIES[op.role],
    )
    return ctx, sess.csrf_token


async def create_platform_session(operator_id: str, *, user_agent: str, ip: str | None) -> tuple[str, str]:
    token, csrf = random_token(), random_token(18)
    now = clock.now()
    async with global_tx() as tx:
        tx.add(
            PlatformSession(
                operator_id=operator_id,
                token_hash=sha256(token),
                csrf_token=csrf,
                user_agent=user_agent[:400],
                ip=ip or "",
                expires_at=now + timedelta(minutes=settings.platform_session_idle_minutes),
            )
        )
        await tx.execute(
            update(PlatformOperator).where(PlatformOperator.id == operator_id).values(last_login_at=now)
        )
    return token, csrf


async def revoke_platform_session(session_id: str) -> None:
    async with global_tx() as tx:
        await tx.execute(
            update(PlatformSession).where(PlatformSession.id == session_id).values(revoked_at=clock.now())
        )
