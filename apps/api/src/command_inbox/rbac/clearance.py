"""Per-department clearance (none / read / resolve / approve), checked in domain services."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.core.errors import forbidden
from command_inbox.db.models import Clearance

CLEARANCE = {"none": 0, "read": 1, "resolve": 2, "approve": 3}
CLEARANCE_LABEL = ["None", "Can read", "Can resolve", "Can approve"]


async def clearance_of(tx: AsyncSession, org_id: str, user_id: str, department_id: str | None) -> int:
    if not department_id:
        return 0
    level = (
        await tx.execute(
            select(Clearance.level).where(
                Clearance.org_id == org_id,
                Clearance.user_id == user_id,
                Clearance.department_id == department_id,
            )
        )
    ).scalar_one_or_none()
    return int(level or 0)


async def require_clearance(
    tx: AsyncSession, ctx: Ctx, department_id: str | None, minimum: int, doing: str
) -> None:
    # Mail no team owns yet (unrouted) stays workable: admins may approve, everyone else may resolve.
    level = (
        await clearance_of(tx, ctx.org_id, ctx.user.id, department_id)
        if department_id
        else (3 if ctx.role == "admin" else 2)
    )
    if level < minimum:
        raise forbidden(
            f'You need "{CLEARANCE_LABEL[minimum]}" clearance for this team to {doing}. '
            f'You have "{CLEARANCE_LABEL[level]}".',
            "clearance_required",
        )
