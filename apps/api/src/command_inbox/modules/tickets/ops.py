"""Write primitives shared by ticket commands (and reusable by the gateway, calls and intake modules)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core import outbox
from command_inbox.core.audit import Feed, audit
from command_inbox.core.clock import clock
from command_inbox.core.context import Actor
from command_inbox.core.errors import conflict, not_found
from command_inbox.db.models import Comment, Counter, Subtask, Ticket
from command_inbox.modules.tickets.queries import ticket_number

CommentKind = Literal["note", "public", "call", "system"]


async def lock_ticket(tx: AsyncSession, org_id: str, ticket_id: str) -> Ticket:
    """Row-lock a ticket for the rest of the transaction."""
    row = (
        await tx.execute(
            select(Ticket)
            .where(Ticket.org_id == org_id, Ticket.id == ticket_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Ticket")
    return row


async def update_ticket(
    tx: AsyncSession, t: Ticket, patch: dict[str, Any], expect_version: int | None = None
) -> Ticket:
    """Update a ticket, bumping its version. Pass `expect_version` for optimistic concurrency."""
    if expect_version is not None and expect_version != t.version:
        raise conflict(
            "stale_version",
            "This ticket changed since you opened it.",
            "Reload to see the latest version, then try again.",
        )
    await tx.execute(
        update(Ticket)
        .where(Ticket.org_id == t.org_id, Ticket.id == t.id)
        .values(**patch, version=Ticket.version + 1, updated_at=clock.now())
        .execution_options(synchronize_session=False)
    )
    return (
        await tx.execute(
            select(Ticket)
            .where(Ticket.org_id == t.org_id, Ticket.id == t.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def system_note(tx: AsyncSession, org_id: str, ticket_id: str, body: str) -> None:
    tx.add(
        Comment(
            org_id=org_id,
            ticket_id=ticket_id,
            kind="system",
            author_name="Command Inbox",
            author_initials="AI",
            body=body,
            created_at=clock.now(),
        )
    )
    await tx.flush()


async def add_comment(
    tx: AsyncSession, org_id: str, ticket_id: str, actor: Actor, kind: CommentKind, body: str
) -> None:
    tx.add(
        Comment(
            org_id=org_id,
            ticket_id=ticket_id,
            kind=kind,
            author_id=actor.id,
            author_name=actor.name,
            author_initials=actor.initials,
            body=body,
            created_at=clock.now(),
        )
    )
    await tx.flush()


async def set_subtask(
    tx: AsyncSession, org_id: str, ticket_id: str, key: str, done: bool, user_id: str | None
) -> None:
    await tx.execute(
        update(Subtask)
        .where(Subtask.org_id == org_id, Subtask.ticket_id == ticket_id, Subtask.key == key)
        .values(done=done, done_by=user_id if done else None, done_at=clock.now() if done else None)
        .execution_options(synchronize_session=False)
    )


async def record_ticket_event(
    tx: AsyncSession,
    t: Ticket,
    *,
    actor: Actor,
    action: str,
    summary: str,
    data: dict[str, Any] | None = None,
    feed: Feed | None = None,
) -> None:
    """Audit + outbox for a ticket change, in one call."""
    await audit(
        tx,
        t.org_id,
        actor=actor,
        action=action,
        entity="ticket",
        entity_id=t.id,
        ticket_id=t.id,
        summary=summary,
        data=data,
        feed=feed,
    )
    await outbox.publish(
        tx, t.org_id, "ticket.updated", {"ticketId": t.id, "number": ticket_number(t.number)}
    )
    if feed is not None:
        await outbox.publish(tx, t.org_id, "activity.created", {"ticketId": t.id})


async def next_number(tx: AsyncSession, org_id: str, counter: Literal["ticket", "gap"]) -> int:
    value = (
        await tx.execute(
            update(Counter)
            .where(Counter.org_id == org_id, Counter.name == counter)
            .values(value=Counter.value + 1)
            .returning(Counter.value)
        )
    ).scalar_one_or_none()
    if value is not None:
        return int(value)
    start = 1000 if counter == "ticket" else 1
    created = (
        await tx.execute(
            insert(Counter)
            .values(org_id=org_id, name=counter, value=start)
            .on_conflict_do_update(index_elements=["org_id", "name"], set_={"value": Counter.value + 1})
            .returning(Counter.value)
        )
    ).scalar_one()
    return int(created)


add_system_note = system_note


async def assign_ticket(tx: AsyncSession, t: Ticket, user_id: str) -> Ticket:
    """Give a ticket to a person (no checks, no audit: the caller's use case does both)."""
    return await update_ticket(tx, t, {"assignee_id": user_id, "owner_kind": "user"})
