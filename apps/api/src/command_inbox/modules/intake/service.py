"""Ingest one inbound email.

Dedupe on the provider message id, thread onto an open ticket from the same sender (by In-Reply-To, or by
subject within 14 days), otherwise open a new ticket in the triaging state and queue a `triage` job in the
same transaction (the handler lives in `command_inbox.agents.runner`).

The sender-authentication verdict is recorded on the `mail.received` audit entry (`data.senderAuth`) and
passed to triage in the job payload as `senderVerified` (+ `senderAuth`), so the lane policy can keep an
unverified sender out of the Auto lane.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core import outbox
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import SYSTEM_ACTOR
from command_inbox.core.errors import not_found
from command_inbox.core.jobs import enqueue
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Board, Customer, InboundMessage, Mailbox, Message, Ticket
from command_inbox.modules.intake.schemas import IngestResult
from command_inbox.modules.intake.security import SenderAuth
from command_inbox.modules.tickets.ops import next_number, system_note, update_ticket
from command_inbox.schemas.requests import IntakeMessageBody

OPEN_STATUSES = ("triaging", "awaiting_approval", "executing", "with_human", "waiting_customer")
_PREFIX = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.I)


def normalise_subject(subject: str) -> str:
    return _PREFIX.sub("", subject, count=1).strip().lower()


async def org_for_mailbox(address: str) -> str | None:
    """Which tenant owns this address (a security-definer lookup; a mailbox belongs to exactly one tenant)."""
    async with global_tx() as g:
        org = (await g.execute(text("select ci_mailbox_org(:a)"), {"a": address})).scalar()
    return str(org) if org else None


async def ingest(
    body: IntakeMessageBody, sender: SenderAuth, *, org_id: str | None = None, provider_id: str | None = None
) -> IngestResult:
    org = org_id or await org_for_mailbox(body.mailbox)
    if not org:
        raise not_found(f"Mailbox {body.mailbox}")
    async with tenant_tx(org) as tx:
        return await _ingest_tx(tx, org, body, sender, provider_id)


async def _ingest_tx(
    tx: AsyncSession, org_id: str, inp: IntakeMessageBody, sender: SenderAuth, provider_id: str | None
) -> IngestResult:
    now = clock.now()
    mailbox = (
        await tx.execute(
            select(Mailbox).where(
                Mailbox.org_id == org_id, func.lower(Mailbox.address) == inp.mailbox.lower()
            )
        )
    ).scalar_one_or_none()
    if mailbox is None:
        raise not_found(f"Mailbox {inp.mailbox}")
    pid = inp.message_id or provider_id or f"dev-{int(now.timestamp() * 1000)}-{secrets.token_hex(3)}"

    inserted_id = (
        await tx.execute(
            insert(InboundMessage)
            .values(org_id=org_id, mailbox_id=mailbox.id, provider_message_id=pid)
            .on_conflict_do_nothing()
            .returning(InboundMessage.id)
        )
    ).scalar()
    if inserted_id is None:
        prev_ticket = (
            await tx.execute(
                select(InboundMessage.ticket_id).where(
                    InboundMessage.org_id == org_id,
                    InboundMessage.mailbox_id == mailbox.id,
                    InboundMessage.provider_message_id == pid,
                )
            )
        ).scalar()
        t = (
            (await tx.execute(select(Ticket).where(Ticket.id == prev_ticket))).scalar_one_or_none()
            if prev_ticket
            else None
        )
        return IngestResult(
            ticket_id=t.id if t else "", number=t.number if t else 0, created=False, duplicate=True
        )

    # Threading
    existing: Ticket | None = None
    if inp.in_reply_to:
        tid = (
            await tx.execute(
                select(Message.ticket_id)
                .where(Message.org_id == org_id, Message.provider_message_id == inp.in_reply_to)
                .limit(1)
            )
        ).scalar()
        if tid:
            existing = (await tx.execute(select(Ticket).where(Ticket.id == tid))).scalar_one_or_none()
    if existing is None:
        candidates = (
            (
                await tx.execute(
                    select(Ticket)
                    .where(
                        Ticket.org_id == org_id,
                        Ticket.from_email == inp.from_email,
                        Ticket.received_at >= now - timedelta(days=14),
                        Ticket.status.in_(OPEN_STATUSES),
                    )
                    .order_by(Ticket.received_at.desc())
                )
            )
            .scalars()
            .all()
        )
        subject = normalise_subject(inp.subject)
        existing = next((c for c in candidates if normalise_subject(c.subject) == subject), None)

    if existing is not None:
        tx.add(
            Message(
                org_id=org_id,
                ticket_id=existing.id,
                direction="inbound",
                from_name=inp.from_name,
                from_addr=inp.from_email,
                to_addr=inp.mailbox,
                body=inp.body,
                sent_at=now,
                provider_message_id=pid,
            )
        )
        await tx.flush()
        await tx.execute(
            update(InboundMessage)
            .where(InboundMessage.id == inserted_id)
            .values(ticket_id=existing.id)
            .execution_options(synchronize_session=False)
        )
        if existing.status == "waiting_customer":
            resumed = (
                existing.due_at + (now - existing.paused_at)
                if existing.paused_at and existing.due_at
                else existing.due_at
            )
            await update_ticket(
                tx,
                existing,
                {
                    "status": "with_human",
                    "paused_at": None,
                    "due_at": resumed,
                    "next_move": "Customer replied — pick it back up",
                },
            )
            await system_note(
                tx, org_id, existing.id, "The customer replied. The deadline clock has resumed."
            )
        else:
            await update_ticket(tx, existing, {})
            await system_note(
                tx, org_id, existing.id, f"New message from {inp.from_name} added to the thread."
            )
        await outbox.publish(tx, org_id, "ticket.updated", {"ticketId": existing.id})
        return IngestResult(ticket_id=existing.id, number=existing.number, created=False, duplicate=False)

    # Customer match by email. An unknown sender gets an unmatched record: no CIF, nothing invented, until a
    # person links it to the real customer.
    customer = (
        (
            await tx.execute(
                select(Customer).where(Customer.org_id == org_id, Customer.email == inp.from_email)
            )
        )
        .scalars()
        .first()
    )
    if customer is None:
        customer = Customer(
            org_id=org_id,
            cif=None,
            name=inp.from_name,
            email=inp.from_email,
            segment="",
            since_year=None,
            account="",
        )
        tx.add(customer)
        await tx.flush()
    board_id = (
        await tx.execute(
            select(Board.id).where(Board.org_id == org_id, Board.mailbox_id == mailbox.id).limit(1)
        )
    ).scalar()
    number = await next_number(tx, org_id, "ticket")
    ticket = Ticket(
        org_id=org_id,
        number=number,
        board_id=board_id,
        mailbox_id=mailbox.id,
        deployment_id=mailbox.deployment_id,
        customer_id=customer.id,
        subject=inp.subject,
        from_name=inp.from_name,
        from_email=inp.from_email,
        received_at=now,
        lane="manual",
        original_lane="manual",
        lane_note="Being triaged",
        status="triaging",
        priority="P3",
        segment=customer.segment,
        owner_kind="ai",
        sla_minutes=1440,
        due_at=now + timedelta(minutes=1440),
        next_move="Triaging",
    )
    tx.add(ticket)
    await tx.flush()
    tx.add(
        Message(
            org_id=org_id,
            ticket_id=ticket.id,
            direction="inbound",
            from_name=inp.from_name,
            from_addr=inp.from_email,
            to_addr=inp.mailbox,
            body=inp.body,
            sent_at=now,
            provider_message_id=pid,
        )
    )
    await tx.flush()
    await tx.execute(
        update(InboundMessage)
        .where(InboundMessage.id == inserted_id)
        .values(ticket_id=ticket.id)
        .execution_options(synchronize_session=False)
    )
    payload: dict[str, Any] = {
        "ticketId": ticket.id,
        "senderVerified": sender.verified,
        "senderAuth": sender.as_dict(),
    }
    await enqueue(tx, org_id, "triage", payload, dedupe_key=f"triage:{ticket.id}")
    await audit(
        tx,
        org_id,
        actor=SYSTEM_ACTOR,
        action="mail.received",
        entity="ticket",
        entity_id=ticket.id,
        ticket_id=ticket.id,
        summary=f"Mail from {inp.from_name} to {inp.mailbox} opened QRY-{number}",
        data={"senderAuth": sender.as_dict()},
    )
    await outbox.publish(tx, org_id, "ticket.created", {"ticketId": ticket.id})
    return IngestResult(ticket_id=ticket.id, number=number, created=True, duplicate=False)


# Sample mails for the demo simulator ("new mail arrives"), exercising each lane.
SAMPLE_MAILS: list[dict[str, str]] = [
    {
        "mailbox": "customercare@bank.example",
        "fromName": "Nisha Kapoor",
        "fromEmail": "nisha.kapoor@gmail.com",
        "subject": "Statement for January to March please",
        "body": "Could you email me the account statement for my savings account ending 5521 for January to March "
        "this year? The net banking copy is password protected and I need an unlocked PDF for my visa file.",
    },
    {
        "mailbox": "tradeops@bank.example",
        "fromName": "Ramesh Iyer",
        "fromEmail": "ramesh@iyerexports.in",
        "subject": "Stop payment on cheque 552310 immediately",
        "body": "Please place an immediate stop payment on cheque number 552310 for ₹4,75,000 issued on 2 Sep from "
        "our current account ending 8812. The supplier has cancelled the order.",
    },
    {
        "mailbox": "nri.desk@bank.example",
        "fromName": "Mary Joseph",
        "fromEmail": "mary.joseph@outlook.com",
        "subject": "Adding my son as a joint holder on NRE account",
        "body": "I live in Muscat and hold an NRE savings account. What documents do I need to add my son, who "
        "lives in Kochi, as a joint holder? Do I need to travel to India?",
    },
    {
        "mailbox": "disputes@bank.example",
        "fromName": "Karthik Rao",
        "fromEmail": "karthik.rao@gmail.com",
        "subject": "Third email — unauthorised card transaction, going to the ombudsman",
        "body": "This is the third time I am writing. There is an unauthorised transaction of ₹38,200 on my card "
        "from a merchant in Dubai. Nobody has replied. If I do not hear back by tomorrow I will complain to the "
        "Banking Ombudsman.",
    },
    {
        "mailbox": "customercare@bank.example",
        "fromName": "Fatema Lokhandwala",
        "fromEmail": "fatema.l@gmail.com",
        "subject": "Locker rent waiver request",
        "body": "The branch locker was inaccessible for three months during repairs. Please waive this year’s "
        "locker rent.",
    },
]
