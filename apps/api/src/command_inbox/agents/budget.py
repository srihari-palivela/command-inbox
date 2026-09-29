"""The workspace's monthly model budget: what is left before a run, and what a run spent after it.

Spend is recorded per calendar month (UTC) and provider. Once the cap is reached, model stages fall back to
the deterministic provider and mail goes to people; the crossing is audited once, so it is never silent.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.providers import ProviderRouter
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import SYSTEM_ACTOR
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import ModelSpend, Org


def month_of(now: datetime.datetime) -> datetime.date:
    return now.astimezone(datetime.UTC).date().replace(day=1)


async def spent_this_month(tx: AsyncSession, org_id: str, now: datetime.datetime | None = None) -> int:
    total = (
        await tx.execute(
            select(func.coalesce(func.sum(ModelSpend.spent_minor), 0)).where(
                ModelSpend.org_id == org_id, ModelSpend.month == month_of(now or clock.now())
            )
        )
    ).scalar_one()
    return int(total)


async def monthly_remaining(tx: AsyncSession, org: Org) -> int | None:
    if org.model_budget_monthly_minor is None:
        return None
    return max(0, org.model_budget_monthly_minor - await spent_this_month(tx, org.id))


async def record_spend(org_id: str, router: ProviderRouter) -> None:
    """Add a run's spend to the month's ledger; audit the moment the monthly cap is crossed."""
    spend = {k: v for k, v in router.spent_by_provider.items() if v or router.calls_by_provider.get(k)}
    if not spend:
        return
    now = clock.now()
    month = month_of(now)
    async with tenant_tx(org_id) as tx:
        org = (await tx.execute(select(Org).where(Org.id == org_id))).scalar_one()
        before = await spent_this_month(tx, org_id, now)
        for provider, minor in spend.items():
            calls = router.calls_by_provider.get(provider, 0)
            await tx.execute(
                insert(ModelSpend)
                .values(org_id=org_id, month=month, provider=provider, spent_minor=minor, calls=calls)
                .on_conflict_do_update(
                    index_elements=[ModelSpend.org_id, ModelSpend.month, ModelSpend.provider],
                    set_={
                        "spent_minor": ModelSpend.spent_minor + minor,
                        "calls": ModelSpend.calls + calls,
                        "updated_at": now,
                    },
                )
            )
        after = before + sum(spend.values())
        cap = org.model_budget_monthly_minor
        if cap is not None and before < cap <= after:
            await audit(
                tx,
                org_id,
                actor=SYSTEM_ACTOR,
                action="model.budget_reached",
                entity="org",
                entity_id=org_id,
                summary="The monthly model budget is spent: drafting stops and new mail goes to people until "
                "the budget is raised or the month ends.",
                data={"month": month.isoformat(), "capMinor": cap, "spentMinor": after},
            )
