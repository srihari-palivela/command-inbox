"""Tenant provisioning: one resumable job, one recorded step at a time.

Every step is idempotent (it checks for its own result first), so a retry after a crash or a failed step
resumes where it stopped. The console shows each step's state, attempts and detail. When every step is done
or skipped the tenant moves from provisioning to provisioned.

Steps:
1. data_key            the tenant's data-encryption key, wrapped by the KMS (keys.py)
2. identity            the tenant's Keycloak Organization with its email domains (keycloak.py)
3. starter_deployment  version 1 of the default deployment, from the starter pack (no automatic lane)
4. first_admin         the tokenised first-admin invitation, emailed
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select, update

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import Actor
from command_inbox.core.jobs import JobRow
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Deployment, DeploymentVersion, Invitation, Org, TenantProvisioning
from command_inbox.platform import keycloak
from command_inbox.platform.audit import platform_audit
from command_inbox.platform.keys import ensure_tenant_key

log = structlog.get_logger(__name__)

STEPS: list[tuple[str, str]] = [
    ("data_key", "Tenant data key"),
    ("identity", "Identity (Keycloak organization)"),
    ("starter_deployment", "Starter deployment"),
    ("first_admin", "First-admin invitation"),
]
STEP_LABEL = dict(STEPS)


def operator_actor(name: str) -> Actor:
    """How an operator appears in the tenant's own audit chain."""
    return Actor("system", None, f"{name} (Command Inbox operator)", "OP")


async def _set(tenant_id: str, step: str, **values: Any) -> None:
    async with global_tx() as g:
        await g.execute(
            update(TenantProvisioning)
            .where(TenantProvisioning.tenant_id == tenant_id, TenantProvisioning.step == step)
            .values(updated_at=clock.now(), **values)
        )


async def _data_key(org: Org, _payload: dict[str, Any]) -> tuple[str, str]:
    async with global_tx() as g:
        key = await ensure_tenant_key(g, org.id)
    return "done", f"Key version {key.version}, wrapped by {key.kek_ref}."


async def _identity(org: Org, _payload: dict[str, Any]) -> tuple[str, str]:
    result = await keycloak.ensure_organization(org.slug, org.name, list(org.sso_email_domains or []))
    return result.state, result.detail


async def _starter_deployment(org: Org, payload: dict[str, Any]) -> tuple[str, str]:
    from command_inbox.modules.deployments.defaults import DEFAULT_KEY, config_json
    from command_inbox.starter import STARTER_PACK_VERSION, starter_config

    async with tenant_tx(org.id) as tx:
        existing = (
            await tx.execute(
                select(Deployment.id).where(Deployment.org_id == org.id, Deployment.key == DEFAULT_KEY)
            )
        ).first()
        if existing:
            return "done", "The default deployment already exists."
        config = starter_config()
        d = Deployment(
            org_id=org.id,
            key=DEFAULT_KEY,
            name="Default",
            description=f"Starter pack {STARTER_PACK_VERSION}. Replace its categories, examples and rules "
            "with your own, and label real mail, before shadow mode.",
        )
        tx.add(d)
        await tx.flush()
        v = DeploymentVersion(
            org_id=org.id,
            deployment_id=d.id,
            version=1,
            state="published",
            config=config_json(config),
            config_hash=config.config_hash(),
            notes=f"Starter pack {STARTER_PACK_VERSION}: neutral banking categories, the mandatory hard stops, "
            "and no automatic lane.",
            published_at=clock.now(),
        )
        tx.add(v)
        await tx.flush()
        d.active_version_id = v.id
        await audit(
            tx,
            org.id,
            actor=operator_actor(str(payload.get("operatorName", "Provisioning"))),
            action="deployment.created",
            entity="deployment",
            entity_id=d.id,
            summary=f"The default deployment was created from starter pack {STARTER_PACK_VERSION}",
            data={"starterPack": STARTER_PACK_VERSION, "configHash": v.config_hash},
        )
    return "done", f"Default deployment v1 from starter pack {STARTER_PACK_VERSION}."


async def _first_admin(org: Org, payload: dict[str, Any]) -> tuple[str, str]:
    from command_inbox.auth.invitations import issue

    admin = payload.get("admin") or {}
    email = str(admin.get("email", "")).lower()
    if not email:
        return "skipped", "No first admin was given; invite one from the tenant page."
    async with tenant_tx(org.id) as tx:
        open_ = (
            await tx.execute(
                select(Invitation.id).where(
                    Invitation.org_id == org.id,
                    Invitation.email == email,
                    Invitation.revoked_at.is_(None),
                )
            )
        ).first()
        if open_:
            return "done", f"Invitation to {email} already exists."
        await issue(
            tx,
            org=org,
            email=email,
            name=str(admin.get("name", "")),
            role="admin",
            inviter_name=str(payload.get("operatorName", "Command Inbox")),
            invited_by_operator=payload.get("operatorId"),
        )
        await audit(
            tx,
            org.id,
            actor=operator_actor(str(payload.get("operatorName", "Provisioning"))),
            action="invitation.created",
            entity="invitation",
            summary=f"The first admin, {email}, was invited",
            data={"email": email, "role": "admin"},
        )
    return "done", f"Invitation emailed to {email}."


RUNNERS = {
    "data_key": _data_key,
    "identity": _identity,
    "starter_deployment": _starter_deployment,
    "first_admin": _first_admin,
}


async def run_provision_tenant(job: JobRow) -> None:
    tenant_id = str(job.payload["tenantId"])
    async with global_tx() as g:
        org = (await g.execute(select(Org).where(Org.id == tenant_id))).scalar_one_or_none()
        if org is None or org.status != "provisioning":
            return
        steps = (
            (
                await g.execute(
                    select(TenantProvisioning)
                    .where(TenantProvisioning.tenant_id == tenant_id)
                    .order_by(TenantProvisioning.position)
                )
            )
            .scalars()
            .all()
        )
        todo = [(s.step, s.attempts) for s in steps if s.state not in ("done", "skipped")]
        g.expunge(org)
    for step, attempts in todo:
        await _set(tenant_id, step, state="running", attempts=attempts + 1, detail="")
        try:
            state, detail = await RUNNERS[step](org, job.payload)
        except Exception as err:
            await _set(tenant_id, step, state="failed", detail=f"{type(err).__name__}: {err}"[:500])
            log.warning("provisioning step failed", tenant_id=tenant_id, step=step, error=str(err))
            raise
        await _set(tenant_id, step, state=state, detail=detail)
    async with global_tx() as g:
        moved = (
            await g.execute(
                update(Org)
                .where(Org.id == tenant_id, Org.status == "provisioning")
                .values(status="provisioned", status_changed_at=clock.now())
                .returning(Org.id)
            )
        ).first()
        if moved:
            await platform_audit(
                g,
                operator_id=None,
                operator_email="system",
                action="tenant.provisioned",
                tenant_id=tenant_id,
                summary=f"{org.name} is provisioned; waiting for the first admin to accept",
                data={"steps": [s for s, _ in todo]},
            )
