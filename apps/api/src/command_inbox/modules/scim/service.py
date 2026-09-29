"""SCIM 2.0 (RFC 7643/7644) for the bank's identity provider: people and groups are provisioned into the
workspace, and leave it, as the bank's directory says.

- Users: `userName` is the work email. Creating one adds a membership (and default clearances); `active: false`
  or DELETE removes the membership and revokes the person's sessions. The global user record stays.
- Groups: stored with their members. When the workspace maps group names to roles (`scim_group_roles`), a
  provisioned person's role is the highest role among their mapped groups (staff if none); people who joined
  by invitation keep the role set in the app, and without a mapping all roles stay managed in the app. The last admin is never demoted or removed by provisioning.
- Everything is audited with the actor "Identity provider (SCIM)".

The subset implemented is what Microsoft Entra ID and Okta provisioning use: filters `userName eq`,
`externalId eq` and `displayName eq`, pagination, PATCH add/replace/remove (with and without paths).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.auth.invitations import grant_default_clearances, initials_of
from command_inbox.config import settings
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import ROLE_LABEL, Actor
from command_inbox.db.engine import global_tx
from command_inbox.db.models import Membership, Org, ScimGroup, Session, User

USER = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_ACTOR = Actor("system", None, "Identity provider (SCIM)", "ID")
RANK = {"staff": 0, "lead": 1, "admin": 2}


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"schemas": [ERROR], "status": str(self.status), "detail": self.detail}
        if self.scim_type:
            out["scimType"] = self.scim_type
        return out


def base_url() -> str:
    return f"{settings.public_api_url.rstrip('/')}/scim/v2"


# ── Users ────────────────────────────────────────────────────────────────────────────────────────────────
def user_resource(u: User, m: Membership | None) -> dict[str, Any]:
    return {
        "schemas": [USER],
        "id": u.id,
        "externalId": m.external_id if m else None,
        "userName": u.email,
        "displayName": u.name,
        "name": {"formatted": u.name},
        "emails": [{"value": u.email, "type": "work", "primary": True}],
        "active": m is not None,
        "meta": {
            "resourceType": "User",
            "created": iso_ms(m.joined_at) if m else iso_ms(u.created_at),
            "location": f"{base_url()}/Users/{u.id}",
        },
    }


def _email_of(body: dict[str, Any]) -> str:
    email = str(body.get("userName") or "").strip().lower()
    if "@" not in email:
        emails = body.get("emails") or []
        primary = next((e for e in emails if e.get("primary")), emails[0] if emails else {})
        email = str(primary.get("value") or "").strip().lower()
    if "@" not in email:
        raise ScimError(400, "userName must be the person's work email.", "invalidValue")
    return email


def _name_of(body: dict[str, Any], fallback: str) -> str:
    name = body.get("displayName") or (body.get("name") or {}).get("formatted")
    if not name:
        n = body.get("name") or {}
        name = " ".join(p for p in (n.get("givenName"), n.get("familyName")) if p)
    return str(name or fallback).strip()[:120]


async def _membership(tx: AsyncSession, org_id: str, user_id: str) -> Membership | None:
    return (
        await tx.execute(select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id))
    ).scalar_one_or_none()


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


async def list_users(
    tx: AsyncSession, org_id: str, flt: tuple[str, str] | None, start: int, count: int
) -> dict[str, Any]:
    q = (
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id)
    )
    if flt:
        attr, value = flt
        if attr == "username":
            q = q.where(func.lower(User.email) == value.lower())
        elif attr == "externalid":
            q = q.where(Membership.external_id == value)
        elif attr == "id":
            q = q.where(User.id == value)
        else:
            raise ScimError(400, f"Filtering on {attr} is not supported.", "invalidFilter")
    rows = (await tx.execute(q.order_by(User.email))).all()
    page = rows[start - 1 : start - 1 + count]
    return {
        "schemas": [LIST],
        "totalResults": len(rows),
        "startIndex": start,
        "itemsPerPage": len(page),
        "Resources": [user_resource(u, m) for u, m in page],
    }


async def get_user(tx: AsyncSession, org_id: str, user_id: str) -> dict[str, Any]:
    u = (await tx.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    m = await _membership(tx, org_id, user_id) if u else None
    if u is None or m is None:
        raise ScimError(404, "User not found.")
    return user_resource(u, m)


async def create_user(tx: AsyncSession, org_id: str, body: dict[str, Any]) -> dict[str, Any]:
    email = _email_of(body)
    if body.get("active") is False:
        raise ScimError(400, "A new user must be active.", "invalidValue")
    async with global_tx() as g:
        u = (await g.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
        if u is None:
            name = _name_of(body, email.split("@")[0])
            u = User(email=email, name=name, initials=initials_of(name))
            g.add(u)
            await g.flush()
        user_id, user_name = u.id, u.name
    if await _membership(tx, org_id, user_id):
        raise ScimError(409, f"{email} is already a member of this workspace.", "uniqueness")
    role = await _role_from_groups(tx, org_id, user_id) or "staff"
    m = Membership(
        org_id=org_id,
        user_id=user_id,
        role=role,
        external_id=body.get("externalId"),
        provisioned_by="scim",
        joined_at=clock.now(),
    )
    tx.add(m)
    await tx.flush()
    await grant_default_clearances(tx, org_id, user_id, role)
    await audit(
        tx,
        org_id,
        actor=SCIM_ACTOR,
        action="member.provisioned",
        entity="membership",
        entity_id=user_id,
        summary=f"{user_name} joined as {ROLE_LABEL[role]} (provisioned by the identity provider)",
        data={"email": email, "role": role, "externalId": m.external_id},
    )
    u = (await tx.execute(select(User).where(User.id == user_id))).scalar_one()
    return user_resource(u, m)


async def deprovision(tx: AsyncSession, org_id: str, user_id: str) -> None:
    m = await _membership(tx, org_id, user_id)
    if m is None:
        return
    if m.role == "admin" and await _admins(tx, org_id) <= 1:
        raise ScimError(409, "This is the workspace's last admin; appoint another admin first.", "mutability")
    await tx.execute(delete(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id))
    revoked = (
        await tx.execute(
            update(Session)
            .where(Session.org_id == org_id, Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=clock.now())
            .returning(Session.id)
        )
    ).all()
    name = (await tx.execute(select(User.name).where(User.id == user_id))).scalar_one()
    await audit(
        tx,
        org_id,
        actor=SCIM_ACTOR,
        action="member.deprovisioned",
        entity="membership",
        entity_id=user_id,
        summary=f"{name} left the workspace (deprovisioned by the identity provider)",
        data={"role": m.role, "revokedSessions": len(revoked)},
    )


async def _apply_user(tx: AsyncSession, org_id: str, user_id: str, values: dict[str, Any]) -> None:
    """Apply replaced attributes (from PUT, or PATCH replace ops) to a member."""
    m = await _membership(tx, org_id, user_id)
    if m is None:
        raise ScimError(404, "User not found.")
    if "externalId" in values:
        m.external_id = values["externalId"]
    name = values.get("displayName") or (values.get("name") or {}).get("formatted")
    if name:
        async with global_tx() as g:
            await g.execute(
                update(User)
                .where(User.id == user_id)
                .values(name=str(name)[:120], initials=initials_of(str(name)))
            )
    if values.get("active") is False:
        await deprovision(tx, org_id, user_id)


async def replace_user(tx: AsyncSession, org_id: str, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    await _apply_user(tx, org_id, user_id, body)
    return await _user_after(tx, org_id, user_id)


async def patch_user(tx: AsyncSession, org_id: str, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if await _membership(tx, org_id, user_id) is None:
        raise ScimError(404, "User not found.")
    for op in body.get("Operations") or []:
        kind = str(op.get("op", "")).lower()
        path = str(op.get("path") or "")
        value = op.get("value")
        if kind not in ("replace", "add"):
            continue  # removing a user attribute has no meaning here
        if path:
            key = {"name.formatted": "displayName"}.get(path, path)
            if key == "active" and isinstance(value, str):
                value = value.lower() == "true"
            await _apply_user(tx, org_id, user_id, {key: value})
        elif isinstance(value, dict):
            await _apply_user(tx, org_id, user_id, value)
    return await _user_after(tx, org_id, user_id)


async def _user_after(tx: AsyncSession, org_id: str, user_id: str) -> dict[str, Any]:
    u = (await tx.execute(select(User).where(User.id == user_id))).scalar_one()
    return user_resource(u, await _membership(tx, org_id, user_id))


# ── Groups ───────────────────────────────────────────────────────────────────────────────────────────────
async def _names(tx: AsyncSession, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    return dict((await tx.execute(select(User.id, User.name).where(User.id.in_(ids)))).tuples().all())


async def group_resource(tx: AsyncSession, g: ScimGroup) -> dict[str, Any]:
    names = await _names(tx, list(g.members or []))
    return {
        "schemas": [GROUP],
        "id": g.id,
        "externalId": g.external_id,
        "displayName": g.display_name,
        "members": [{"value": m, "display": names.get(m, "")} for m in g.members or []],
        "meta": {"resourceType": "Group", "location": f"{base_url()}/Groups/{g.id}"},
    }


async def _group(tx: AsyncSession, org_id: str, group_id: str) -> ScimGroup:
    g = (
        await tx.execute(
            select(ScimGroup).where(ScimGroup.org_id == org_id, ScimGroup.id == group_id).with_for_update()
        )
    ).scalar_one_or_none()
    if g is None:
        raise ScimError(404, "Group not found.")
    return g


async def list_groups(
    tx: AsyncSession, org_id: str, flt: tuple[str, str] | None, start: int, count: int
) -> dict[str, Any]:
    q = select(ScimGroup).where(ScimGroup.org_id == org_id)
    if flt:
        attr, value = flt
        if attr == "displayname":
            q = q.where(func.lower(ScimGroup.display_name) == value.lower())
        elif attr == "externalid":
            q = q.where(ScimGroup.external_id == value)
        else:
            raise ScimError(400, f"Filtering on {attr} is not supported.", "invalidFilter")
    groups = list((await tx.execute(q.order_by(ScimGroup.display_name))).scalars())
    page = groups[start - 1 : start - 1 + count]
    return {
        "schemas": [LIST],
        "totalResults": len(groups),
        "startIndex": start,
        "itemsPerPage": len(page),
        "Resources": [await group_resource(tx, g) for g in page],
    }


async def _member_ids(tx: AsyncSession, org_id: str, values: list[dict[str, Any]]) -> list[str]:
    wanted = [str(v.get("value")) for v in values or [] if v.get("value")]
    if not wanted:
        return []
    found = (
        await tx.execute(
            select(Membership.user_id).where(Membership.org_id == org_id, Membership.user_id.in_(wanted))
        )
    ).scalars()
    return list(found)


async def create_group(tx: AsyncSession, org_id: str, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("displayName") or "").strip()
    if not name:
        raise ScimError(400, "displayName is required.", "invalidValue")
    exists = (
        await tx.execute(
            select(ScimGroup.id).where(
                ScimGroup.org_id == org_id, func.lower(ScimGroup.display_name) == name.lower()
            )
        )
    ).first()
    if exists:
        raise ScimError(409, f"A group named {name!r} already exists.", "uniqueness")
    members = await _member_ids(tx, org_id, body.get("members") or [])
    g = ScimGroup(org_id=org_id, display_name=name[:200], external_id=body.get("externalId"), members=members)
    tx.add(g)
    await tx.flush()
    await _recompute(tx, org_id, set(members))
    return await group_resource(tx, g)


async def patch_group(tx: AsyncSession, org_id: str, group_id: str, body: dict[str, Any]) -> dict[str, Any]:
    g = await _group(tx, org_id, group_id)
    members = list(g.members or [])
    before = set(members)
    for op in body.get("Operations") or []:
        kind = str(op.get("op", "")).lower()
        path = str(op.get("path") or "")
        value = op.get("value")
        if path.startswith("members[") and kind == "remove":
            # members[value eq "id"]
            target = path.split('"')[1] if '"' in path else ""
            members = [m for m in members if m != target]
        elif path == "members" or (not path and isinstance(value, dict) and "members" in value):
            items = value if path == "members" else value["members"]
            ids = await _member_ids(tx, org_id, items if isinstance(items, list) else [items])
            if kind == "add":
                members += [i for i in ids if i not in members]
            elif kind == "remove":
                members = [m for m in members if m not in ids] if ids else []
            elif kind == "replace":
                members = ids
        elif path == "displayName" or (not path and isinstance(value, dict) and "displayName" in value):
            g.display_name = str(value if path else value["displayName"])[:200]
        elif path == "externalId":
            g.external_id = value
    g.members, g.updated_at = members, clock.now()
    await tx.flush()
    await _recompute(tx, org_id, before | set(members))
    return await group_resource(tx, g)


async def replace_group(tx: AsyncSession, org_id: str, group_id: str, body: dict[str, Any]) -> dict[str, Any]:
    g = await _group(tx, org_id, group_id)
    before = set(g.members or [])
    g.display_name = str(body.get("displayName") or g.display_name)[:200]
    g.external_id = body.get("externalId", g.external_id)
    g.members = await _member_ids(tx, org_id, body.get("members") or [])
    g.updated_at = clock.now()
    await tx.flush()
    await _recompute(tx, org_id, before | set(g.members))
    return await group_resource(tx, g)


async def delete_group(tx: AsyncSession, org_id: str, group_id: str) -> None:
    g = await _group(tx, org_id, group_id)
    members = set(g.members or [])
    await tx.delete(g)
    await tx.flush()
    await _recompute(tx, org_id, members)


# ── Roles from groups ────────────────────────────────────────────────────────────────────────────────────
async def _role_from_groups(tx: AsyncSession, org_id: str, user_id: str) -> str | None:
    """The role the directory gives this person, or None when the workspace maps no groups to roles."""
    mapping = {
        k.lower(): v
        for k, v in (
            (await tx.execute(select(Org.scim_group_roles).where(Org.id == org_id))).scalar_one() or {}
        ).items()
        if v in RANK
    }
    if not mapping:
        return None
    groups = (await tx.execute(select(ScimGroup).where(ScimGroup.org_id == org_id))).scalars()
    roles = [
        mapping[g.display_name.lower()]
        for g in groups
        if user_id in (g.members or []) and g.display_name.lower() in mapping
    ]
    return max(roles, key=RANK.__getitem__) if roles else "staff"


async def _recompute(tx: AsyncSession, org_id: str, user_ids: set[str]) -> None:
    for user_id in user_ids:
        m = await _membership(tx, org_id, user_id)
        # Only people the directory provisioned take their role from it; invited members keep theirs.
        if m is None or m.provisioned_by != "scim":
            continue
        role = await _role_from_groups(tx, org_id, user_id)
        if role is None or role == m.role:
            continue
        if m.role == "admin" and await _admins(tx, org_id) <= 1:
            continue  # never leave the workspace without an admin
        before, m.role = m.role, role
        await tx.flush()
        name = (await tx.execute(select(User.name).where(User.id == user_id))).scalar_one()
        await audit(
            tx,
            org_id,
            actor=SCIM_ACTOR,
            action="member.role_changed",
            entity="membership",
            entity_id=user_id,
            summary=f"{name}'s role changed from {ROLE_LABEL[before]} to {ROLE_LABEL[role]} (directory groups)",
            data={"before": before, "after": role, "source": "scim"},
        )
