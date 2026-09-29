"""The full ticket workspace DTO: thread, triage, action/draft/brief, gate, trace, log, customer history."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx
from command_inbox.db.models import (
    Attachment,
    Brief,
    Comment,
    Customer,
    Mailbox,
    Message,
    Subtask,
    Ticket,
    TicketLink,
    TraceSpan,
    TriageRun,
    User,
    Watcher,
)
from command_inbox.domain.risk import cell_of
from command_inbox.domain.transitions import allowed_transitions
from command_inbox.modules.gateway.gate import (
    compute_gate,
    current_action,
    current_draft,
    duplicate_check,
    user_ref,
)
from command_inbox.modules.tickets.queries import load_summaries, load_ticket, ticket_number, to_summary
from command_inbox.schemas import dto


def _outcome_of(t: Ticket) -> tuple[str, str]:
    if t.resolution:
        bad = re.search(r"no resolution", t.resolution, re.I)
        warn = re.search(r"1d|days", t.resolution, re.I)
        return t.resolution, "bad" if bad else "warn" if warn else "ok"
    if not t.resolved_at:
        return "Still open", "warn"
    h = (t.resolved_at - t.received_at).total_seconds() / 3600
    return (
        f"Resolved in {max(1, int(h + 0.5))}h" if h < 12 else "Resolved",
        "warn" if h > 24 else "ok",
    )


async def _trace(tx: AsyncSession, org_id: str, run: TriageRun) -> dto.TraceDTO:
    spans = (
        (
            await tx.execute(
                select(TraceSpan)
                .where(TraceSpan.org_id == org_id, TraceSpan.run_id == run.id)
                .order_by(TraceSpan.seq.asc())
            )
        )
        .scalars()
        .all()
    )
    return dto.TraceDTO(
        trace_id=run.trace_id,
        total_ms=run.latency_ms,
        cost_minor=run.cost_minor,
        spans=[
            dto.SpanDTO(
                seq=x.seq,
                offset_ms=x.offset_ms,
                agent=x.agent,
                model=x.model,
                action=x.action,
                output=x.output,
                latency_ms=x.latency_ms,
                tokens=x.tokens,
                cost_minor=x.cost_minor,
                status=x.status,  # type: ignore[arg-type]
            )
            for x in spans
        ],
    )


async def _customer(tx: AsyncSession, org_id: str, t: Ticket) -> dto.CustomerDTO | None:
    if not t.customer_id:
        return None
    c = (
        await tx.execute(select(Customer).where(Customer.org_id == org_id, Customer.id == t.customer_id))
    ).scalar_one_or_none()
    if c is None:
        return None
    past = (
        (
            await tx.execute(
                select(Ticket)
                .where(Ticket.org_id == org_id, Ticket.customer_id == c.id, Ticket.id != t.id)
                .order_by(Ticket.received_at.desc())
            )
        )
        .scalars()
        .all()
    )
    history = []
    for p in past:
        outcome, tone = _outcome_of(p)
        history.append(
            dto.PastTicketDTO(
                number=ticket_number(p.number),
                subject=p.subject,
                at=iso_ms(p.received_at),
                outcome=outcome,
                tone=tone,  # type: ignore[arg-type]
                same_topic=bool(t.query_type_id) and p.query_type_id == t.query_type_id,
            )
        )
    return dto.CustomerDTO(
        id=c.id,
        cif=c.cif,
        name=c.name,
        email=c.email,
        since_year=c.since_year,
        segment=c.segment,
        account=c.account,
        history=history,
    )


async def get_ticket_detail(tx: AsyncSession, ctx: Ctx, id_or_number: str) -> dto.TicketDetailDTO:
    """Build the ticket detail DTO (by id or `QRY-n`) as the signed-in person sees it."""
    found = await load_ticket(tx, ctx.org_id, id_or_number)
    loaded = (await load_summaries(tx, ctx.org_id, Ticket.id == found.id))[0]
    t = loaded.row
    summary = to_summary(t, loaded.x, clock.now())
    org = ctx.org_id

    messages = (
        (
            await tx.execute(
                select(Message)
                .where(Message.org_id == org, Message.ticket_id == t.id)
                .order_by(Message.sent_at.asc())
            )
        )
        .scalars()
        .all()
    )
    run = (
        await tx.execute(
            select(TriageRun)
            .where(TriageRun.org_id == org, TriageRun.ticket_id == t.id)
            .order_by(TriageRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    cur = await current_action(tx, org, t.id)
    action: dto.ActionDTO | None = None
    if cur is not None:
        a, tpl = cur.a, cur.t
        dup_clear, dup_text = await duplicate_check(tx, a)
        action = dto.ActionDTO(
            id=a.id,
            template_code=tpl.code,
            name=tpl.name,
            system=tpl.system,
            endpoint=tpl.endpoint,
            reversible=tpl.reversible,
            money_moves=tpl.money_moves,
            cell=cell_of(tpl.reversible, tpl.money_moves),
            chain=a.chain,  # type: ignore[arg-type]
            fields=a.fields,
            validation=a.validation,
            state=a.state,  # type: ignore[arg-type]
            maker=await user_ref(tx, a.maker_id),
            maker_at=iso_ms(a.maker_at) if a.maker_at else None,
            checker=await user_ref(tx, a.checker_id),
            checker_at=iso_ms(a.checker_at) if a.checker_at else None,
            execute_after=iso_ms(a.execute_after) if a.execute_after else None,
            executed_at=iso_ms(a.executed_at) if a.executed_at else None,
            external_ref=a.external_ref,
            duplicate=dto.ActionDTODuplicate(clear=dup_clear, text=dup_text),
        )

    d = await current_draft(tx, org, t.id)
    draft = (
        dto.DraftDTO(
            id=d.id,
            subject=d.subject,
            to=d.to_addr,
            original_body=d.original_body,
            current_body=d.current_body,
            citations=d.citations,
            flagged=d.flagged,
            state=d.state,  # type: ignore[arg-type]
            send_after=iso_ms(d.send_after) if d.send_after else None,
            sent_at=iso_ms(d.sent_at) if d.sent_at else None,
        )
        if d
        else None
    )
    brief = (
        await tx.execute(select(Brief).where(Brief.org_id == org, Brief.ticket_id == t.id))
    ).scalar_one_or_none()
    gate = await compute_gate(tx, ctx, t, cur, d)

    subtasks = (
        (
            await tx.execute(
                select(Subtask)
                .where(Subtask.org_id == org, Subtask.ticket_id == t.id)
                .order_by(Subtask.sort.asc())
            )
        )
        .scalars()
        .all()
    )
    log = (
        (
            await tx.execute(
                select(Comment)
                .where(Comment.org_id == org, Comment.ticket_id == t.id)
                .order_by(Comment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    watchers = [
        dto.UserRef(id=w.id, name=w.name, initials=w.initials)
        for w in (
            await tx.execute(
                select(User.id, User.name, User.initials)
                .join(Watcher, Watcher.user_id == User.id)
                .where(Watcher.org_id == org, Watcher.ticket_id == t.id)
            )
        ).all()
    ]
    attachments = (
        (
            await tx.execute(
                select(Attachment)
                .where(Attachment.org_id == org, Attachment.ticket_id == t.id)
                .order_by(Attachment.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    links = (
        (
            await tx.execute(
                select(TicketLink)
                .where(TicketLink.org_id == org, TicketLink.ticket_id == t.id)
                .order_by(TicketLink.sort.asc())
            )
        )
        .scalars()
        .all()
    )
    mailbox = (
        (await tx.execute(select(Mailbox.address).where(Mailbox.id == t.mailbox_id))).scalar_one_or_none()
        if t.mailbox_id
        else None
    )
    can_work = ctx.can("ticket.work")

    return dto.TicketDetailDTO(
        **summary.model_dump(),
        version=t.version,
        from_email=t.from_email,
        mailbox=mailbox or "",
        category=t.category,
        subcategory=t.subcategory,
        product=t.product,
        regulatory_flag=t.regulatory_flag,
        reopen_count=t.reopen_count,
        first_reply_at=iso_ms(t.first_reply_at) if t.first_reply_at else None,
        resolved_at=iso_ms(t.resolved_at) if t.resolved_at else None,
        split_proposed=t.split_proposed,
        messages=[
            dto.MessageDTO(
                id=m.id,
                direction=m.direction,  # type: ignore[arg-type]
                from_name=m.from_name,
                from_addr=m.from_addr,
                to_addr=m.to_addr,
                body=m.body,
                sent_at=iso_ms(m.sent_at),
            )
            for m in messages
        ],
        triage=dto.TriageDTO(
            reasoning=run.reasoning,
            evidence=run.evidence,
            confidence=run.confidence,
            latency_ms=run.latency_ms,
            provider=run.provider,
            classified_at=iso_ms(run.created_at),
        )
        if run
        else None,
        action=action,
        draft=draft,
        brief=dto.BriefDTO(
            why=brief.why, summary=brief.summary, context=brief.context, suggestions=brief.suggestions
        )
        if brief
        else None,
        gate=gate,
        trace=await _trace(tx, org, run) if run else None,
        subtasks=[dto.SubtaskDTO(key=x.key, label=x.label, owner=x.owner, done=x.done) for x in subtasks],
        log=[
            dto.LogEntryDTO(
                id=c.id,
                kind=c.kind,  # type: ignore[arg-type]
                author_name=c.author_name,
                author_initials=c.author_initials,
                body=c.body,
                at=iso_ms(c.created_at),
            )
            for c in log
        ],
        watchers=watchers,
        watching=any(w.id == ctx.user.id for w in watchers),
        attachments=[dto.AttachmentDTO(id=a.id, ext=a.ext, name=a.name, size=a.size) for a in attachments],
        links=[dto.LinkDTO(kind=lk.kind, label=lk.label, ref=lk.ref) for lk in links],
        customer=await _customer(tx, org, t),
        logged_minutes=t.logged_minutes,
        allowed_transitions=allowed_transitions(t.status) if can_work else [],  # type: ignore[arg-type]
        permissions=dto.TicketDetailDTOPermissions(
            can_work=can_work,
            can_assign=ctx.can("ticket.assign"),
            can_override_up=ctx.can("ticket.override_up"),
            can_reply=ctx.can("ticket.reply"),
        ),
    )


# Public alias for other modules (gateway, calls) that return the refreshed workspace after a command.
build_ticket_detail = get_ticket_detail
