"""The workspace's model policy: which System 2 providers its agreements allow, and the monthly spend cap.

The installation decides which providers can be called at all (API keys); the workspace narrows that to what
the bank has agreed to (data processing terms, zero data retention, residency). A provider still in use by
a live deployment version cannot be removed: that would silently send its mail to people.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.budget import month_of
from command_inbox.agents.config import DeploymentConfig
from command_inbox.agents.providers import PROVIDER_KEYS, available
from command_inbox.agents.specs import PROVIDER_NAME, policy_problems
from command_inbox.config import settings
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import conflict
from command_inbox.db.models import Deployment, DeploymentVersion, ModelSpend, Org
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import ModelPolicyBody

LIVE_STATES = ("shadow", "canary", "published")


def _default_model(key: str) -> str:
    return settings.copilot_model if key == "anthropic" else settings.openai_model


async def get_policy(tx: AsyncSession, ctx: Ctx) -> dto.ModelPolicyDTO:
    require(ctx, "setup.view", "view the model policy")
    org = (await tx.execute(select(Org).where(Org.id == ctx.org_id))).scalar_one()
    month = month_of(clock.now())
    rows = {
        r.provider: r
        for r in (
            await tx.execute(
                select(ModelSpend).where(ModelSpend.org_id == ctx.org_id, ModelSpend.month == month)
            )
        ).scalars()
    }
    spent = sum(r.spent_minor for r in rows.values())
    allowed = set(org.allowed_providers or [])
    cap = org.model_budget_monthly_minor
    return dto.ModelPolicyDTO(
        providers=[
            dto.ModelProviderDTO(
                key=key,  # type: ignore[arg-type]
                name=PROVIDER_NAME[key],
                configured=available(key),
                allowed=key in allowed,
                is_default=settings.default_provider == key,
                default_model=_default_model(key),
                spent_minor=rows[key].spent_minor if key in rows else 0,
                calls=rows[key].calls if key in rows else 0,
            )
            for key in PROVIDER_KEYS
        ],
        monthly_budget_minor=cap,
        spent_minor=spent,
        month=month.isoformat(),
        budget_reached=cap is not None and spent >= cap,
        can_edit=ctx.can("workspace.manage"),
    )


async def update_policy(tx: AsyncSession, ctx: Ctx, body: ModelPolicyBody) -> dto.ModelPolicyDTO:
    require(ctx, "workspace.manage", "change the model policy")
    org = (await tx.execute(select(Org).where(Org.id == ctx.org_id).with_for_update())).scalar_one()
    allowed = sorted(set(body.allowed_providers))
    removed = set(org.allowed_providers or []) - set(allowed)
    if removed:
        live = (
            await tx.execute(
                select(Deployment.name, DeploymentVersion.version, DeploymentVersion.config)
                .join(Deployment, Deployment.id == DeploymentVersion.deployment_id)
                .where(DeploymentVersion.org_id == ctx.org_id, DeploymentVersion.state.in_(LIVE_STATES))
            )
        ).all()
        users = [
            f"{name} v{version}"
            for name, version, config in live
            if policy_problems(DeploymentConfig.model_validate(config), allowed)
        ]
        if users:
            raise conflict(
                "provider_in_use",
                "A live deployment still uses this provider.",
                f"Move {', '.join(users)} to another provider first, then remove it from the policy.",
            )
    before = {
        "allowedProviders": list(org.allowed_providers or []),
        "monthlyBudgetMinor": org.model_budget_monthly_minor,
    }
    org.allowed_providers = allowed
    org.model_budget_monthly_minor = body.monthly_budget_minor
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="workspace.model_policy_changed",
        entity="org",
        entity_id=ctx.org_id,
        summary=f"{ctx.user.name} set the model policy: "
        + (", ".join(PROVIDER_NAME[k] for k in allowed) or "no providers")
        + (
            f"; monthly budget {body.monthly_budget_minor} (minor units)"
            if body.monthly_budget_minor is not None
            else "; no monthly budget"
        ),
        data={
            "before": before,
            "after": {"allowedProviders": allowed, "monthlyBudgetMinor": body.monthly_budget_minor},
        },
    )
    return await get_policy(tx, ctx)
