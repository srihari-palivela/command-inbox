"""Teams (departments) and query types, managed by the workspace's admin.

Deployment configurations name teams and query types by name, so a rename or delete that would orphan a
category in a live version (shadow, canary or published) is refused with the versions to update first.
Anything with history (tickets, documents, mailboxes) cannot be deleted: rename it, or take a query type
out of service (`live: false`), instead. A new team gives every current member their role's default
clearance for it, as joining the workspace would.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.config import DeploymentConfig
from command_inbox.auth.invitations import DEFAULT_CLEARANCE
from command_inbox.core.audit import audit
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import conflict, not_found
from command_inbox.db.models import (
    Clearance,
    Department,
    Deployment,
    DeploymentVersion,
    KnowledgeDoc,
    Mailbox,
    Membership,
    QueryType,
    Ticket,
    User,
)
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import DepartmentBody, QueryTypeBody

LIVE_STATES = ("shadow", "canary", "published")
CLOSED = ("resolved", "closed")


async def _live_configs(tx: AsyncSession, org_id: str) -> list[tuple[str, DeploymentConfig]]:
    rows = (
        await tx.execute(
            select(Deployment.name, DeploymentVersion.version, DeploymentVersion.config)
            .join(Deployment, Deployment.id == DeploymentVersion.deployment_id)
            .where(DeploymentVersion.org_id == org_id, DeploymentVersion.state.in_(LIVE_STATES))
        )
    ).all()
    return [(f"{name} v{version}", DeploymentConfig.model_validate(c)) for name, version, c in rows]


async def _refuse_if_live_names(
    tx: AsyncSession, org_id: str, *, department: str | None = None, category: str | None = None
) -> None:
    users = [
        label
        for label, config in await _live_configs(tx, org_id)
        if any(
            (department and c.department.lower() == department.lower())
            or (category and c.name.lower() == category.lower())
            for c in config.taxonomy.categories
        )
    ]
    if users:
        what = f"the team {department!r}" if department else f"the query type {category!r}"
        raise conflict(
            "name_in_use",
            f"A live deployment routes mail by {what}.",
            f"Update {', '.join(users)} in a new draft first, or keep the name.",
        )


async def _count(tx: AsyncSession, model: type, *where: object) -> int:
    return int((await tx.execute(select(func.count()).select_from(model).where(*where))).scalar_one())


async def admin_view(tx: AsyncSession, ctx: Ctx) -> dto.TaxonomyAdminDTO:
    require(ctx, "setup.view", "view teams and query types")
    oid = ctx.org_id
    depts = list(
        (
            await tx.execute(
                select(Department).where(Department.org_id == oid).order_by(Department.sort, Department.name)
            )
        ).scalars()
    )
    qts = list(
        (
            await tx.execute(
                select(QueryType).where(QueryType.org_id == oid).order_by(QueryType.sort, QueryType.name)
            )
        ).scalars()
    )
    tickets_by_qt = dict(
        (
            await tx.execute(
                select(Ticket.query_type_id, func.count())
                .where(Ticket.org_id == oid)
                .group_by(Ticket.query_type_id)
            )
        )
        .tuples()
        .all()
    )
    tickets_by_dept = dict(
        (
            await tx.execute(
                select(Ticket.department_id, func.count())
                .where(Ticket.org_id == oid)
                .group_by(Ticket.department_id)
            )
        )
        .tuples()
        .all()
    )
    open_by_dept = dict(
        (
            await tx.execute(
                select(Ticket.department_id, func.count())
                .where(Ticket.org_id == oid, Ticket.status.not_in(CLOSED))
                .group_by(Ticket.department_id)
            )
        )
        .tuples()
        .all()
    )
    docs_by_dept = dict(
        (
            await tx.execute(
                select(KnowledgeDoc.department_id, func.count())
                .where(KnowledgeDoc.org_id == oid)
                .group_by(KnowledgeDoc.department_id)
            )
        )
        .tuples()
        .all()
    )
    boxes_by_dept = dict(
        (
            await tx.execute(
                select(Mailbox.department_id, func.count())
                .where(Mailbox.org_id == oid)
                .group_by(Mailbox.department_id)
            )
        )
        .tuples()
        .all()
    )
    owner_ids = {d.owner_id for d in depts if d.owner_id}
    owners = {
        u.id: dto.UserRef(id=u.id, name=u.name, initials=u.initials)
        for u in (
            (await tx.execute(select(User).where(User.id.in_(owner_ids)))).scalars() if owner_ids else []
        )
    }
    qt_count: dict[str, int] = {}
    for q in qts:
        if q.department_id:
            qt_count[q.department_id] = qt_count.get(q.department_id, 0) + 1
    return dto.TaxonomyAdminDTO(
        departments=[
            dto.DepartmentAdminDTO(
                id=d.id,
                name=d.name,
                risk=d.risk,
                owner=owners.get(d.owner_id) if d.owner_id else None,
                query_types=qt_count.get(d.id, 0),
                open_tickets=open_by_dept.get(d.id, 0),
                deletable=not (
                    qt_count.get(d.id)
                    or tickets_by_dept.get(d.id)
                    or docs_by_dept.get(d.id)
                    or boxes_by_dept.get(d.id)
                ),
            )
            for d in depts
        ],
        query_types=[
            dto.QueryTypeAdminDTO(
                id=q.id,
                name=q.name,
                department_id=q.department_id,
                default_lane=q.default_lane,  # type: ignore[arg-type]
                live=q.live,
                tickets=tickets_by_qt.get(q.id, 0),
                deletable=not tickets_by_qt.get(q.id),
            )
            for q in qts
        ],
        can_edit=ctx.can("setup.edit"),
    )


async def _dept(tx: AsyncSession, ctx: Ctx, department_id: str, lock: bool = False) -> Department:
    q = select(Department).where(Department.org_id == ctx.org_id, Department.id == department_id)
    d = (await tx.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if d is None:
        raise not_found("Team")
    return d


async def _unique_name(
    tx: AsyncSession, model: type, org_id: str, name: str, exclude: str | None = None
) -> None:
    q = select(model.id).where(model.org_id == org_id, func.lower(model.name) == name.lower())  # type: ignore[attr-defined]
    if exclude:
        q = q.where(model.id != exclude)  # type: ignore[attr-defined]
    if (await tx.execute(q)).first():
        raise conflict("name_taken", f"{name!r} already exists.")


async def create_department(tx: AsyncSession, ctx: Ctx, body: DepartmentBody) -> dto.TaxonomyAdminDTO:
    require(ctx, "setup.edit", "add teams")
    await _unique_name(tx, Department, ctx.org_id, body.name)
    sort = (
        await tx.execute(
            select(func.coalesce(func.max(Department.sort), 0)).where(Department.org_id == ctx.org_id)
        )
    ).scalar_one()
    d = Department(org_id=ctx.org_id, name=body.name, risk=body.risk, sort=sort + 1)
    tx.add(d)
    await tx.flush()
    members = (
        await tx.execute(select(Membership.user_id, Membership.role).where(Membership.org_id == ctx.org_id))
    ).all()
    tx.add_all(
        [
            Clearance(
                org_id=ctx.org_id, user_id=uid, department_id=d.id, level=DEFAULT_CLEARANCE.get(role, 2)
            )
            for uid, role in members
        ]
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="department.created",
        entity="department",
        entity_id=d.id,
        summary=f"{ctx.user.name} added the team {d.name}",
        data={"name": d.name, "risk": d.risk, "clearancesGranted": len(members)},
    )
    return await admin_view(tx, ctx)


async def update_department(
    tx: AsyncSession, ctx: Ctx, department_id: str, body: DepartmentBody
) -> dto.TaxonomyAdminDTO:
    require(ctx, "setup.edit", "change teams")
    d = await _dept(tx, ctx, department_id, lock=True)
    before = {"name": d.name, "risk": d.risk}
    if body.name != d.name:
        await _unique_name(tx, Department, ctx.org_id, body.name, exclude=d.id)
        await _refuse_if_live_names(tx, ctx.org_id, department=d.name)
    d.name, d.risk = body.name, body.risk
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="department.updated",
        entity="department",
        entity_id=d.id,
        summary=f"{ctx.user.name} updated the team {d.name}",
        data={"before": before, "after": {"name": d.name, "risk": d.risk}},
    )
    return await admin_view(tx, ctx)


async def delete_department(tx: AsyncSession, ctx: Ctx, department_id: str) -> dto.TaxonomyAdminDTO:
    require(ctx, "setup.edit", "delete teams")
    d = await _dept(tx, ctx, department_id, lock=True)
    oid = ctx.org_id
    used = {
        "query types": await _count(tx, QueryType, QueryType.org_id == oid, QueryType.department_id == d.id),
        "tickets": await _count(tx, Ticket, Ticket.org_id == oid, Ticket.department_id == d.id),
        "documents": await _count(
            tx, KnowledgeDoc, KnowledgeDoc.org_id == oid, KnowledgeDoc.department_id == d.id
        ),
        "mailboxes": await _count(tx, Mailbox, Mailbox.org_id == oid, Mailbox.department_id == d.id),
    }
    if any(used.values()):
        what = ", ".join(f"{n} {k}" for k, n in used.items() if n)
        raise conflict(
            "department_in_use",
            f"{d.name} still has {what}.",
            "Move them to another team, or rename this one instead.",
        )
    await _refuse_if_live_names(tx, oid, department=d.name)
    await tx.execute(delete(Clearance).where(Clearance.org_id == oid, Clearance.department_id == d.id))
    await tx.delete(d)
    await tx.flush()
    await audit(
        tx,
        oid,
        actor=actor_of(ctx),
        action="department.deleted",
        entity="department",
        entity_id=department_id,
        summary=f"{ctx.user.name} deleted the team {d.name}",
        data={"name": d.name},
    )
    return await admin_view(tx, ctx)


async def _qt(tx: AsyncSession, ctx: Ctx, query_type_id: str) -> QueryType:
    q = (
        await tx.execute(
            select(QueryType)
            .where(QueryType.org_id == ctx.org_id, QueryType.id == query_type_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if q is None:
        raise not_found("Query type")
    return q


def _qt_json(q: QueryType) -> dict[str, object]:
    return {"name": q.name, "departmentId": q.department_id, "defaultLane": q.default_lane, "live": q.live}


async def create_query_type(tx: AsyncSession, ctx: Ctx, body: QueryTypeBody) -> dto.TaxonomyAdminDTO:
    require(ctx, "setup.edit", "add query types")
    await _unique_name(tx, QueryType, ctx.org_id, body.name)
    if body.department_id:
        await _dept(tx, ctx, body.department_id)
    sort = (
        await tx.execute(
            select(func.coalesce(func.max(QueryType.sort), 0)).where(QueryType.org_id == ctx.org_id)
        )
    ).scalar_one()
    q = QueryType(
        org_id=ctx.org_id,
        name=body.name,
        department_id=body.department_id,
        default_lane=body.default_lane,
        live=body.live,
        sort=sort + 1,
    )
    tx.add(q)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="query_type.created",
        entity="query_type",
        entity_id=q.id,
        summary=f"{ctx.user.name} added the query type {q.name}",
        data=_qt_json(q),
    )
    return await admin_view(tx, ctx)


async def update_query_type(
    tx: AsyncSession, ctx: Ctx, query_type_id: str, body: QueryTypeBody
) -> dto.TaxonomyAdminDTO:
    require(ctx, "setup.edit", "change query types")
    q = await _qt(tx, ctx, query_type_id)
    before = _qt_json(q)
    if body.name != q.name:
        await _unique_name(tx, QueryType, ctx.org_id, body.name, exclude=q.id)
        await _refuse_if_live_names(tx, ctx.org_id, category=q.name)
    if body.department_id:
        await _dept(tx, ctx, body.department_id)
    q.name, q.department_id, q.default_lane, q.live = (
        body.name,
        body.department_id,
        body.default_lane,
        body.live,
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="query_type.updated",
        entity="query_type",
        entity_id=q.id,
        summary=f"{ctx.user.name} updated the query type {q.name}",
        data={"before": before, "after": _qt_json(q)},
    )
    return await admin_view(tx, ctx)


async def delete_query_type(tx: AsyncSession, ctx: Ctx, query_type_id: str) -> dto.TaxonomyAdminDTO:
    require(ctx, "setup.edit", "delete query types")
    q = await _qt(tx, ctx, query_type_id)
    n = await _count(tx, Ticket, Ticket.org_id == ctx.org_id, Ticket.query_type_id == q.id)
    if n:
        raise conflict(
            "query_type_in_use",
            f"{n} tickets are filed under {q.name}.",
            "Take it out of service instead: new mail will no longer be sorted into it.",
        )
    await _refuse_if_live_names(tx, ctx.org_id, category=q.name)
    await tx.delete(q)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="query_type.deleted",
        entity="query_type",
        entity_id=query_type_id,
        summary=f"{ctx.user.name} deleted the query type {q.name}",
        data=_qt_json(q),
    )
    return await admin_view(tx, ctx)
