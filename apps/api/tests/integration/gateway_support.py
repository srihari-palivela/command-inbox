"""Helpers for the gateway, calls and intake integration tests (no HTTP route for the ticket detail needed)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from command_inbox.core.context import Ctx
from command_inbox.core.jobs import Worker


async def ctx_of(client: Any) -> Ctx:
    """The server-side identity behind a signed-in test client."""
    from command_inbox.auth.sessions import SESSION_COOKIE, resolve_session

    resolved = await resolve_session(client.http.cookies.get(SESSION_COOKIE), "test")
    assert resolved is not None
    return resolved.ctx


async def gate(client: Any, id_or_number: str) -> dict[str, Any]:
    """The gate as this person sees it (camelCase, like `TicketDetailDTO.gate`)."""
    from command_inbox.db.engine import tenant_tx
    from command_inbox.modules.gateway.gate import gate_for
    from command_inbox.modules.tickets.queries import load_ticket

    ctx = await ctx_of(client)
    async with tenant_tx(ctx.org_id) as tx:
        t = await load_ticket(tx, ctx.org_id, id_or_number)
        return (await gate_for(tx, ctx, t)).model_dump(mode="json", by_alias=True)


async def ticket_row(org_id: str, id_or_number: str) -> Any:
    from command_inbox.db.engine import tenant_tx
    from command_inbox.modules.tickets.queries import load_ticket

    async with tenant_tx(org_id) as tx:
        return await load_ticket(tx, org_id, id_or_number)


async def action_of(org_id: str, ticket_id: str) -> Any:
    from command_inbox.db.engine import tenant_tx
    from command_inbox.modules.gateway.gate import current_action

    async with tenant_tx(org_id) as tx:
        cur = await current_action(tx, org_id, ticket_id)
        return cur.a if cur else None


async def make_action_ticket(
    org_id: str, like_number: int, template_code: str, chain: str, **action: Any
) -> str:
    """A fresh auto-lane ticket with a drafted action, in the same department as ticket `like_number`, so tests
    never depend on (or disturb) seeded tickets other suites use."""
    from command_inbox.core.clock import clock
    from command_inbox.core.crypto import random_token
    from command_inbox.db.engine import tenant_tx
    from command_inbox.db.models import ActionInstance, ActionTemplate, Subtask, Ticket
    from command_inbox.modules.tickets.ops import next_number

    async with tenant_tx(org_id) as tx:
        like = (await tx.execute(select(Ticket).where(Ticket.number == like_number))).scalar_one()
        tpl = (
            await tx.execute(select(ActionTemplate).where(ActionTemplate.code == template_code))
        ).scalar_one()
        now = clock.now()
        t = Ticket(
            org_id=org_id,
            number=await next_number(tx, org_id, "ticket"),
            subject=f"Gateway test {template_code}",
            from_name="Test Customer",
            from_email="test.customer@example.com",
            received_at=now,
            lane="auto",
            original_lane="auto",
            status="awaiting_approval",
            priority="P3",
            owner_kind="ai",
            sla_minutes=1440,
            department_id=like.department_id,
            customer_id=like.customer_id,
            mailbox_id=like.mailbox_id,
            confidence=0.9,
        )
        tx.add(t)
        await tx.flush()
        for i, key in enumerate(("a3", "a4")):
            tx.add(Subtask(org_id=org_id, ticket_id=t.id, key=key, label=key, owner="you", sort=i))
        tx.add(
            ActionInstance(
                org_id=org_id,
                ticket_id=t.id,
                template_id=tpl.id,
                account_ref=f"ACC-{random_token(6)}",
                fields=[{"label": "Amount", "value": "100", "source": "mail", "inferred": False}],
                state=action.pop("state", "drafted"),
                chain=chain,
                idempotency_key=random_token(16),
                **action,
            )
        )
        await tx.flush()
        return t.id


def worker() -> Worker:
    """The real registry plus the gateway's final-failure handler."""
    from command_inbox.modules.gateway.jobs import install
    from command_inbox.worker_registry import register_all

    return install(register_all(Worker()))
