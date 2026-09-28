"""Who can take work for a department, with their live load."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.db.engine import rows


@dataclass(slots=True)
class Candidate:
    id: str
    name: str
    level: int
    open: int
    capacity: int
    availability: str
    department: str


def load_ratio(open_: int, capacity: int) -> float:
    """Open work over capacity; someone with no capacity sorts last instead of dividing by zero."""
    return open_ / capacity if capacity > 0 else float("inf")


async def candidates(tx: AsyncSession, org_id: str, department_id: str) -> list[Candidate]:
    """Everyone cleared to resolve work for a department (level >= 2), with their live load."""
    found = await rows(
        tx,
        """
        select u.id::text as id, u.name, c.level, d.name as department,
               (m.base_load + (select count(*) from tickets t where t.org_id = :org and t.assignee_id = u.id
                                 and t.status not in ('resolved','closed')))::int as open,
               m.capacity, coalesce(a.status, 'available') as availability
          from memberships m
          join users u on u.id = m.user_id
          join clearances c on c.org_id = m.org_id and c.user_id = u.id and c.department_id = :dept
          join departments d on d.id = c.department_id
          left join staff_availability a on a.org_id = m.org_id and a.user_id = u.id
         where m.org_id = :org and c.level >= 2""",
        {"org": org_id, "dept": department_id},
    )
    return [Candidate(**r) for r in found]


async def pick_assignee(
    tx: AsyncSession,
    org_id: str,
    department_id: str | None,
    bucket: str,
    exclude: str | None = None,
) -> dict[str, str] | None:
    """Router: lightest relative load among available people, then the highest clearance."""
    if not department_id:
        return None
    pool = [
        c
        for c in await candidates(tx, org_id, department_id)
        if c.availability == "available" and c.id != exclude
    ]
    if not pool:
        return None
    pool.sort(key=lambda c: (load_ratio(c.open, c.capacity), -c.level))
    pick = pool[0]
    return {
        "id": pick.id,
        "name": pick.name,
        "reason": f"closest skill match for {bucket.lower()}, lightest live load in {pick.department}",
    }
