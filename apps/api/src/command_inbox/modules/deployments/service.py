"""Deployment use cases: versions, drafts, mailbox bindings, rollout (shadow → canary → published), rollback.

Publishing rules (docs/architecture/python-platform.md §3 and §9):
- Only drafts are editable; every save is validated by `DeploymentConfig` and re-hashed.
- Four-eyes: whoever last edited a version cannot put it in front of customers (canary or published). A
  tenant with a single admin may do so only with an explicit acknowledgement, which is audited.
- Canary and publish need a passed eval run whose config hash equals the version's hash, so an edit after
  the run invalidates it.
- Publishing retires the previous published version; rollback re-publishes an earlier, once-published one.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.config import DeploymentConfig
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import bad_request, conflict, forbidden, not_found
from command_inbox.core.outbox import publish
from command_inbox.db.models import (
    BucketRule,
    Department,
    Deployment,
    DeploymentVersion,
    EvalRun,
    Mailbox,
    Membership,
    Org,
    PriorityRule,
    QueryType,
    Ticket,
    User,
)
from command_inbox.modules.deployments.defaults import DEFAULT_KEY, Setup, build_default_config, config_json
from command_inbox.modules.deployments.schemas import (
    BindMailboxesBody,
    CreateDeploymentBody,
    DraftConfigBody,
    NewDraftBody,
    PromoteBody,
    RollbackBody,
)
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto

ORDER = {"draft": 0, "shadow": 1, "canary": 2, "published": 3}
GATED = ("canary", "published")  # states in which a version acts on real mail


# ── Reads ─────────────────────────────────────────────────────────────────────────────────────────────


async def user_refs(tx: AsyncSession, ids: set[str | None]) -> dict[str, dto.UserRef]:
    wanted = {str(i) for i in ids if i}
    if not wanted:
        return {}
    rows = (await tx.execute(select(User.id, User.name, User.initials).where(User.id.in_(wanted)))).all()
    return {str(r.id): dto.UserRef(id=str(r.id), name=r.name, initials=r.initials) for r in rows}


async def admin_count(tx: AsyncSession, org_id: str) -> int:
    return int(
        (
            await tx.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.org_id == org_id, Membership.role == "admin")
            )
        ).scalar_one()
    )


async def passing_run(tx: AsyncSession, version: DeploymentVersion) -> EvalRun | None:
    """The latest passed run that scored exactly this configuration on a frozen dataset."""
    return (
        await tx.execute(
            select(EvalRun)
            .where(
                EvalRun.org_id == version.org_id,
                EvalRun.deployment_version_id == version.id,
                EvalRun.state == "passed",
                EvalRun.config_hash == version.config_hash,
                EvalRun.config_hash != "",
                EvalRun.dataset_snapshot != "",
            )
            .order_by(EvalRun.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _hash_of(config: dict[str, Any]) -> str:
    return DeploymentConfig.model_validate(config).config_hash()


async def _publish_check(
    tx: AsyncSession, ctx: Ctx, v: DeploymentVersion, admins: int
) -> dto.PublishCheckDTO | None:
    if v.state not in ("draft", "shadow", "canary"):
        return None
    blockers: list[str] = []
    run = await passing_run(tx, v)
    if run is None:
        blockers.append("Needs a passed eval run on this exact configuration.")
    if not ctx.can("deployment.publish"):
        blockers.append("Only an admin can publish.")
    ack = False
    if v.edited_by and str(v.edited_by) == ctx.user.id:
        if admins > 1:
            blockers.append("You made the last edit, so another admin has to publish it.")
        else:
            ack = True
    return dto.PublishCheckDTO(
        ready=not blockers,
        blockers=blockers,
        requires_single_admin_ack=ack,
        eval_run_id=run.id if run else None,
    )


async def _version_dto(
    tx: AsyncSession, ctx: Ctx, v: DeploymentVersion, users: dict[str, dto.UserRef], admins: int
) -> dto.DeploymentVersionDTO:
    return dto.DeploymentVersionDTO(
        id=v.id,
        deployment_id=v.deployment_id,
        version=v.version,
        state=v.state,  # type: ignore[arg-type]
        canary_percent=v.canary_percent,
        config_hash=v.config_hash,
        notes=v.notes,
        created_by=users.get(str(v.created_by)) if v.created_by else None,
        created_at=iso_ms(v.created_at),
        edited_by=users.get(str(v.edited_by)) if v.edited_by else None,
        edited_at=iso_ms(v.edited_at) if v.edited_at else None,
        published_by=users.get(str(v.published_by)) if v.published_by else None,
        published_at=iso_ms(v.published_at) if v.published_at else None,
        retired_at=iso_ms(v.retired_at) if v.retired_at else None,
        eval_run_id=v.eval_run_id,
        config=v.config,
        publish_check=await _publish_check(tx, ctx, v, admins),
    )


async def _versions(tx: AsyncSession, org_id: str, deployment_id: str) -> list[DeploymentVersion]:
    return list(
        (
            await tx.execute(
                select(DeploymentVersion)
                .where(DeploymentVersion.org_id == org_id, DeploymentVersion.deployment_id == deployment_id)
                .order_by(DeploymentVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )


async def _deployment_dto(
    tx: AsyncSession, d: Deployment, versions: list[DeploymentVersion]
) -> dto.DeploymentDTO:
    by_state = {v.state: v for v in versions if v.state != "retired"}
    active = next((v for v in versions if v.id == d.active_version_id), None)
    mailboxes = (
        await tx.execute(
            select(Mailbox.id, Mailbox.address)
            .where(Mailbox.org_id == d.org_id, Mailbox.deployment_id == d.id)
            .order_by(Mailbox.sort, Mailbox.address)
        )
    ).all()
    canary = by_state.get("canary")
    return dto.DeploymentDTO(
        id=d.id,
        key=d.key,
        name=d.name,
        description=d.description,
        status=d.status,  # type: ignore[arg-type]
        active_version_id=d.active_version_id,
        active_version=active.version if active else None,
        shadow_version=by_state["shadow"].version if "shadow" in by_state else None,
        canary=dto.DeploymentDTOCanary(version=canary.version, percent=canary.canary_percent or 0)
        if canary
        else None,
        draft_version_id=by_state["draft"].id if "draft" in by_state else None,
        mailboxes=[dto.DeploymentDTOMailboxes(id=m.id, address=m.address) for m in mailboxes],
        created_at=iso_ms(d.created_at),
    )


async def _get(tx: AsyncSession, ctx: Ctx, deployment_id: str, *, lock: bool = False) -> Deployment:
    q = select(Deployment).where(Deployment.org_id == ctx.org_id, Deployment.id == deployment_id)
    if lock:
        q = q.with_for_update()
    d = (await tx.execute(q)).scalar_one_or_none()
    if d is None:
        raise not_found("Deployment")
    return d


async def _get_version(
    tx: AsyncSession, d: Deployment, version_id: str, *, lock: bool = False
) -> DeploymentVersion:
    q = select(DeploymentVersion).where(
        DeploymentVersion.org_id == d.org_id,
        DeploymentVersion.deployment_id == d.id,
        DeploymentVersion.id == version_id,
    )
    if lock:
        q = q.with_for_update()
    v = (await tx.execute(q)).scalar_one_or_none()
    if v is None:
        raise not_found("Deployment version")
    return v


async def list_deployments(tx: AsyncSession, ctx: Ctx) -> list[dto.DeploymentDTO]:
    require(ctx, "deployment.view", "see deployments")
    deps = (
        (
            await tx.execute(
                select(Deployment).where(Deployment.org_id == ctx.org_id).order_by(Deployment.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [await _deployment_dto(tx, d, await _versions(tx, ctx.org_id, d.id)) for d in deps]


async def get_deployment(tx: AsyncSession, ctx: Ctx, deployment_id: str) -> dto.DeploymentDetailDTO:
    require(ctx, "deployment.view", "see deployments")
    d = await _get(tx, ctx, deployment_id)
    versions = await _versions(tx, ctx.org_id, d.id)
    users = await user_refs(tx, {x for v in versions for x in (v.created_by, v.edited_by, v.published_by)})
    admins = await admin_count(tx, ctx.org_id)
    base = await _deployment_dto(tx, d, versions)
    return dto.DeploymentDetailDTO(
        **base.model_dump(), versions=[await _version_dto(tx, ctx, v, users, admins) for v in versions]
    )


async def get_version(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, version_id: str
) -> dto.DeploymentVersionDTO:
    require(ctx, "deployment.view", "see deployments")
    d = await _get(tx, ctx, deployment_id)
    v = await _get_version(tx, d, version_id)
    return await _one_version_dto(tx, ctx, v)


async def _one_version_dto(tx: AsyncSession, ctx: Ctx, v: DeploymentVersion) -> dto.DeploymentVersionDTO:
    users = await user_refs(tx, {v.created_by, v.edited_by, v.published_by})
    return await _version_dto(tx, ctx, v, users, await admin_count(tx, ctx.org_id))


# ── Drafts ────────────────────────────────────────────────────────────────────────────────────────────


def validate_config(raw: dict[str, Any]) -> DeploymentConfig:
    """400 `invalid_config` naming the failing path(s), e.g. `taxonomy.categories.0.key: String should …`."""
    try:
        return DeploymentConfig.model_validate(raw)
    except ValidationError as err:
        parts = []
        for e in err.errors()[:5]:
            path = ".".join(str(p) for p in e["loc"])
            msg = str(e["msg"]).removeprefix("Value error, ")
            parts.append(f"{path}: {msg}" if path else msg)
        raise bad_request(
            "invalid_config", "The deployment configuration is not valid.", "; ".join(parts)
        ) from None


async def _source_config(tx: AsyncSession, ctx: Ctx, deployment_id: str | None) -> dict[str, Any]:
    """The config a new deployment starts from: another deployment's live version, else the default one."""
    q = select(DeploymentVersion.config).join(
        Deployment,
        (Deployment.id == DeploymentVersion.deployment_id)
        & (Deployment.active_version_id == DeploymentVersion.id),
    )
    if deployment_id:
        row = (
            await tx.execute(q.where(Deployment.org_id == ctx.org_id, Deployment.id == deployment_id))
        ).first()
        if row is None:
            raise not_found("Deployment to copy from")
        return dict(row[0])
    row = (await tx.execute(q.where(Deployment.org_id == ctx.org_id, Deployment.key == DEFAULT_KEY))).first()
    if row is not None:
        return dict(row[0])
    config, _notes = build_default_config(await load_setup(tx, ctx.org_id))
    return config_json(config)


async def create_deployment(
    tx: AsyncSession, ctx: Ctx, body: CreateDeploymentBody
) -> dto.DeploymentDetailDTO:
    require(ctx, "deployment.edit", "create deployments")
    taken = (
        await tx.execute(
            select(Deployment.id).where(Deployment.org_id == ctx.org_id, Deployment.key == body.key)
        )
    ).first()
    if taken:
        raise conflict("key_taken", f"A deployment with the key {body.key!r} already exists.")
    config = validate_config(await _source_config(tx, ctx, body.copy_from))
    now = clock.now()
    d = Deployment(
        org_id=ctx.org_id, key=body.key, name=body.name, description=body.description, created_by=ctx.user.id
    )
    tx.add(d)
    await tx.flush()
    v = DeploymentVersion(
        org_id=ctx.org_id,
        deployment_id=d.id,
        version=1,
        state="draft",
        config=config_json(config),
        config_hash=config.config_hash(),
        created_by=ctx.user.id,
        edited_by=ctx.user.id,
        edited_at=now,
    )
    tx.add(v)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="deployment.created",
        entity="deployment",
        entity_id=d.id,
        summary=f"{ctx.user.name} created the deployment {body.name}",
        data={"key": body.key, "copyFrom": body.copy_from},
    )
    await publish(
        tx, ctx.org_id, "deployment.updated", {"deploymentId": d.id, "versionId": v.id, "state": "draft"}
    )
    await tx.refresh(d)
    return await get_deployment(tx, ctx, d.id)


async def new_draft(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, body: NewDraftBody
) -> dto.DeploymentVersionDTO:
    require(ctx, "deployment.edit", "edit deployment drafts")
    d = await _get(tx, ctx, deployment_id, lock=True)
    versions = await _versions(tx, ctx.org_id, d.id)
    if any(v.state == "draft" for v in versions):
        raise conflict("draft_exists", "This deployment already has a draft. Edit that one instead.")
    source = next((v for v in versions if v.id == d.active_version_id), versions[0] if versions else None)
    if source is None:
        raise conflict("no_version", "This deployment has no version to copy.")
    v = DeploymentVersion(
        org_id=ctx.org_id,
        deployment_id=d.id,
        version=max(x.version for x in versions) + 1,
        state="draft",
        config=source.config,
        config_hash=_hash_of(source.config),
        notes=body.notes or f"Copy of version {source.version}",
        created_by=ctx.user.id,
        edited_by=ctx.user.id,
        edited_at=clock.now(),
    )
    tx.add(v)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="deployment.draft_created",
        entity="deployment_version",
        entity_id=v.id,
        summary=f"{ctx.user.name} started draft v{v.version} of {d.name} from v{source.version}",
        data={"deploymentId": d.id, "version": v.version, "from": source.version},
    )
    await publish(
        tx, ctx.org_id, "deployment.updated", {"deploymentId": d.id, "versionId": v.id, "state": "draft"}
    )
    await tx.refresh(v)
    return await _one_version_dto(tx, ctx, v)


async def save_draft_config(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, version_id: str, body: DraftConfigBody
) -> dto.DeploymentVersionDTO:
    require(ctx, "deployment.edit", "edit deployment drafts")
    d = await _get(tx, ctx, deployment_id)
    v = await _get_version(tx, d, version_id, lock=True)
    if v.state != "draft":
        raise conflict("not_a_draft", "Only a draft can be edited; start a new draft from this version.")
    config = validate_config(body.config)
    before = v.config_hash
    v.config = config_json(config)
    v.config_hash = config.config_hash()
    v.edited_by = ctx.user.id
    v.edited_at = clock.now()
    if body.notes is not None:
        v.notes = body.notes
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="deployment.draft_edited",
        entity="deployment_version",
        entity_id=v.id,
        summary=f"{ctx.user.name} edited draft v{v.version} of {d.name}",
        data={"deploymentId": d.id, "version": v.version, "fromHash": before, "toHash": v.config_hash},
    )
    await publish(
        tx, ctx.org_id, "deployment.updated", {"deploymentId": d.id, "versionId": v.id, "state": "draft"}
    )
    await tx.refresh(v)
    return await _one_version_dto(tx, ctx, v)


async def bind_mailboxes(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, body: BindMailboxesBody
) -> dto.DeploymentDTO:
    """Set the mailboxes this deployment categorises. Every mailbox always belongs to exactly one deployment,
    so a mailbox leaves this one only by being bound to another."""
    require(ctx, "deployment.edit", "bind mailboxes to deployments")
    d = await _get(tx, ctx, deployment_id, lock=True)
    wanted = set(body.mailbox_ids)
    rows = (await tx.execute(select(Mailbox).where(Mailbox.org_id == ctx.org_id))).scalars().all()
    known = {m.id: m for m in rows}
    missing = wanted - set(known)
    if missing:
        raise not_found("Mailbox")
    dropped = [m.address for m in rows if m.deployment_id == d.id and m.id not in wanted]
    if dropped:
        raise conflict(
            "mailbox_unbound",
            "Every mailbox must belong to a deployment. Bind it to another deployment instead.",
            ", ".join(sorted(dropped)),
        )
    if wanted and d.active_version_id is None:
        raise conflict(
            "no_published_version", "Publish a version of this deployment before binding mailboxes."
        )
    moved = [m for m in rows if m.id in wanted and m.deployment_id != d.id]
    if moved:
        await tx.execute(
            update(Mailbox)
            .where(Mailbox.org_id == ctx.org_id, Mailbox.id.in_([m.id for m in moved]))
            .values(deployment_id=d.id)
        )
        await audit(
            tx,
            ctx.org_id,
            actor=actor_of(ctx),
            action="deployment.mailboxes_bound",
            entity="deployment",
            entity_id=d.id,
            summary=f"{ctx.user.name} bound {', '.join(m.address for m in moved)} to {d.name}",
            data={"mailboxIds": [m.id for m in moved], "from": [m.deployment_id for m in moved]},
        )
        await publish(tx, ctx.org_id, "deployment.updated", {"deploymentId": d.id})
    return await _deployment_dto(tx, d, await _versions(tx, ctx.org_id, d.id))


# ── Rollout ───────────────────────────────────────────────────────────────────────────────────────────


async def promote(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, version_id: str, body: PromoteBody
) -> dto.DeploymentVersionDTO:
    require(ctx, "deployment.publish", "publish deployments")
    d = await _get(tx, ctx, deployment_id, lock=True)
    v = await _get_version(tx, d, version_id, lock=True)
    to = body.to
    if v.state not in ("draft", "shadow", "canary"):
        raise conflict("bad_state", f"A {v.state} version cannot be promoted.")
    if ORDER[to] < ORDER[v.state] or (to == v.state and to != "canary"):
        raise conflict("bad_state", f"A {v.state} version cannot move to {to}; withdraw it instead.")
    if to == "canary" and body.canary_percent is None:
        raise bad_request("canary_percent_required", "Say what share of mail the canary should take (1-99%).")
    if _hash_of(v.config) != v.config_hash:
        raise conflict(
            "config_hash_mismatch", "This version's configuration does not match its recorded hash."
        )

    run: EvalRun | None = None
    single_admin_ack = False
    if to in GATED:
        run = await passing_run(tx, v)
        if run is None:
            raise conflict(
                "eval_gate",
                "Publishing needs a passed eval run on this exact configuration.",
                "Run the evals on this version; a run on an earlier edit no longer counts.",
            )
        if v.edited_by and str(v.edited_by) == ctx.user.id:
            if await admin_count(tx, ctx.org_id) > 1:
                raise forbidden(
                    "You made the last edit to this version; another admin has to publish it.", "four_eyes"
                )
            if not body.acknowledge_single_admin:
                raise conflict(
                    "single_admin_ack_required",
                    "You are the only admin and made the last edit. Confirm that you publish it without review.",
                )
            single_admin_ack = True

    others = {
        x.state: x for x in await _versions(tx, ctx.org_id, d.id) if x.id != v.id and x.state != "retired"
    }
    now = clock.now()
    retired: DeploymentVersion | None = None
    if to in ("shadow", "canary") and to in others:
        raise conflict("slot_taken", f"Version {others[to].version} is already in {to}. Withdraw it first.")
    if to == "published" and "published" in others:
        retired = others["published"]
        retired.state, retired.retired_at = "retired", now
        await tx.flush()  # free the one-published-version slot before taking it

    from_state = v.state
    v.state = to
    v.canary_percent = body.canary_percent if to == "canary" else None
    if to == "published":
        assert run is not None
        v.published_by, v.published_at, v.eval_run_id = ctx.user.id, now, run.id
        d.active_version_id = v.id
    await tx.flush()

    verb = {
        "shadow": "put in shadow",
        "canary": f"sent {body.canary_percent}% of mail to",
        "published": "published",
    }
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="deployment.published" if to == "published" else f"deployment.{to}",
        entity="deployment_version",
        entity_id=v.id,
        summary=f"{ctx.user.name} {verb[to]} v{v.version} of {d.name}"
        + (f" (v{retired.version} retired)" if retired else "")
        + (" — only admin, acknowledged without review" if single_admin_ack else ""),
        data={
            "deploymentId": d.id,
            "version": v.version,
            "from": from_state,
            "to": to,
            "canaryPercent": v.canary_percent,
            "configHash": v.config_hash,
            "evalRunId": run.id if run else None,
            "editedBy": v.edited_by,
            "singleAdminAcknowledged": single_admin_ack,
            "retiredVersion": retired.version if retired else None,
        },
    )
    await publish(
        tx, ctx.org_id, "deployment.updated", {"deploymentId": d.id, "versionId": v.id, "state": to}
    )
    await tx.refresh(v)
    return await _one_version_dto(tx, ctx, v)


async def withdraw(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, version_id: str
) -> dto.DeploymentVersionDTO:
    """Stop a shadow or canary (or discard a draft): the version is retired and never acted on again."""
    d = await _get(tx, ctx, deployment_id, lock=True)
    v = await _get_version(tx, d, version_id, lock=True)
    if v.state == "draft":
        require(ctx, "deployment.edit", "discard deployment drafts")
    else:
        require(ctx, "deployment.publish", "withdraw deployment versions")
    if v.state not in ("draft", "shadow", "canary"):
        raise conflict(
            "bad_state", "Only a draft, shadow or canary version can be withdrawn; use rollback instead."
        )
    from_state = v.state
    v.state, v.canary_percent, v.retired_at = "retired", None, clock.now()
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="deployment.withdrawn",
        entity="deployment_version",
        entity_id=v.id,
        summary=f"{ctx.user.name} withdrew {from_state} v{v.version} of {d.name}",
        data={"deploymentId": d.id, "version": v.version, "from": from_state},
    )
    await publish(
        tx, ctx.org_id, "deployment.updated", {"deploymentId": d.id, "versionId": v.id, "state": "retired"}
    )
    await tx.refresh(v)
    return await _one_version_dto(tx, ctx, v)


async def rollback(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, body: RollbackBody
) -> dto.DeploymentDetailDTO:
    """Re-publish an earlier version that was live before (default: the most recent one)."""
    require(ctx, "deployment.publish", "roll back deployments")
    d = await _get(tx, ctx, deployment_id, lock=True)
    versions = await _versions(tx, ctx.org_id, d.id)
    candidates = [v for v in versions if v.state == "retired" and v.published_at is not None]
    if body.version_id:
        target = next((v for v in versions if v.id == body.version_id), None)
        if target is None:
            raise not_found("Deployment version")
        if target not in candidates:
            raise conflict(
                "not_rollbackable", "Only a retired version that was published before can be restored."
            )
    else:
        candidates.sort(key=lambda v: v.retired_at or v.published_at or v.created_at, reverse=True)
        if not candidates:
            raise conflict("nothing_to_roll_back", "There is no earlier published version to go back to.")
        target = candidates[0]
    if _hash_of(target.config) != target.config_hash:
        raise conflict(
            "config_hash_mismatch", "That version's configuration does not match its recorded hash."
        )
    now = clock.now()
    current = next((v for v in versions if v.state == "published"), None)
    if current is not None:
        current.state, current.retired_at = "retired", now
        await tx.flush()
    target.state, target.retired_at = "published", None
    target.published_by, target.published_at = ctx.user.id, now
    d.active_version_id = target.id
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="deployment.rolled_back",
        entity="deployment_version",
        entity_id=target.id,
        summary=f"{ctx.user.name} rolled {d.name} back to v{target.version}"
        + (f" (v{current.version} retired)" if current else ""),
        data={
            "deploymentId": d.id,
            "version": target.version,
            "retiredVersion": current.version if current else None,
            "configHash": target.config_hash,
        },
    )
    await publish(
        tx,
        ctx.org_id,
        "deployment.updated",
        {"deploymentId": d.id, "versionId": target.id, "state": "published"},
    )
    return await get_deployment(tx, ctx, d.id)


# ── Default deployment for tenants created after migration 0004 ─────────────────────────────────────


async def load_setup(tx: AsyncSession, org_id: str) -> Setup:
    org = (await tx.execute(select(Org.name, Org.confidence_bar).where(Org.id == org_id))).one()
    departments = (
        await tx.execute(select(Department.id, Department.name).where(Department.org_id == org_id))
    ).all()
    qts = (
        await tx.execute(
            select(QueryType.name, QueryType.default_lane, QueryType.department_id, QueryType.owner_label)
            .where(QueryType.org_id == org_id)
            .order_by(QueryType.sort, QueryType.name)
        )
    ).all()
    buckets = (
        await tx.execute(
            select(BucketRule.description, BucketRule.kind, BucketRule.pattern)
            .where(BucketRule.org_id == org_id)
            .order_by(BucketRule.sort)
        )
    ).all()
    prio = (
        await tx.execute(
            select(
                PriorityRule.key,
                PriorityRule.description,
                PriorityRule.target,
                PriorityRule.hard,
                PriorityRule.enabled,
            )
            .where(PriorityRule.org_id == org_id)
            .order_by(PriorityRule.sort)
        )
    ).all()
    return Setup(
        org_name=org.name,
        confidence_bar=float(org.confidence_bar),
        departments={str(r.id): r.name for r in departments},
        query_types=[dict(r._mapping) for r in qts],
        bucket_rules=[dict(r._mapping) for r in buckets],
        priority_rules=[dict(r._mapping) for r in prio],
    )


async def ensure_default_deployment(tx: AsyncSession, org_id: str) -> str:
    """Give a tenant its default deployment (published v1, bound to every unbound mailbox and ticket).

    Idempotent. Call inside `tenant_tx(org_id)` after seeding a tenant's query types, departments and rules.
    """
    existing = (
        await tx.execute(
            select(Deployment.id).where(Deployment.org_id == org_id, Deployment.key == DEFAULT_KEY)
        )
    ).scalar_one_or_none()
    if existing:
        return str(existing)
    config, notes = build_default_config(await load_setup(tx, org_id))
    d = Deployment(
        org_id=org_id, key=DEFAULT_KEY, name="Default", description="Every mailbox of this workspace."
    )
    tx.add(d)
    await tx.flush()
    v = DeploymentVersion(
        org_id=org_id,
        deployment_id=d.id,
        version=1,
        state="published",
        config=config_json(config),
        config_hash=config.config_hash(),
        notes=" ".join(["Created from the workspace's setup.", *notes])[:2000],
        published_at=clock.now(),
    )
    tx.add(v)
    await tx.flush()
    d.active_version_id = v.id
    await tx.execute(
        update(Mailbox)
        .where(Mailbox.org_id == org_id, Mailbox.deployment_id.is_(None))
        .values(deployment_id=d.id)
    )
    await tx.execute(
        update(Ticket)
        .where(Ticket.org_id == org_id, Ticket.deployment_id.is_(None))
        .values(deployment_id=d.id, deployment_version_id=v.id)
    )
    await tx.flush()
    return str(d.id)
