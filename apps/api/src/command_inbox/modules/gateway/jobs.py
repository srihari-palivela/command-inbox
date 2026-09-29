"""Worker handlers for the approval gateway: execute actions, send drafts and replies, escalate checkers.

Every handler is idempotent. It opens its own tenant transaction, row-locks the subject and returns
quietly if the subject is no longer in the state that queued it (undone, recalled, already done). A crash
after the connector call re-runs the job; the connector's idempotency key stops a second effect.

`install(worker)` registers a final-failure handler: when one of these jobs exhausts its attempts, the
ticket moves to the manual lane with a system note and an `ai.handed_back` audit entry, so a person picks
it up instead of it silently stalling. The worker process and the embedded worker must call it.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select, update

from command_inbox.core.audit import Feed
from command_inbox.core.clock import clock
from command_inbox.core.context import AI_ACTOR, SYSTEM_ACTOR, Actor
from command_inbox.core.jobs import JobHandler, JobRow, Worker
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import (
    ActionInstance,
    ActionTemplate,
    Draft,
    Mailbox,
    Message,
    PredictionOutcome,
    Reply,
    User,
)
from command_inbox.domain.transitions import is_open
from command_inbox.modules.gateway.connectors import ExecutionRequest, connector
from command_inbox.modules.gateway.service import store_feedback
from command_inbox.modules.tickets.ops import (
    add_comment,
    lock_ticket,
    record_ticket_event,
    set_subtask,
    system_note,
    update_ticket,
)

log = structlog.get_logger(__name__)

GATEWAY_KINDS = frozenset({"execute_action", "send_draft", "send_reply", "escalate_checker"})
DEFAULT_FROM = "customercare@bank.example"


def _user_actor(u: User | None) -> Actor:
    return Actor("user", str(u.id), u.name, u.initials) if u else SYSTEM_ACTOR


async def _user(tx: Any, user_id: str | None) -> User | None:
    if not user_id:
        return None
    return (await tx.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def _mailbox_address(tx: Any, mailbox_id: str | None) -> str:
    if not mailbox_id:
        return DEFAULT_FROM
    addr = (await tx.execute(select(Mailbox.address).where(Mailbox.id == mailbox_id))).scalar()
    return addr or DEFAULT_FROM


async def run_execute_action(job: JobRow) -> None:
    """Execute an approved action. Refuses (and retries, then hands back) if the approval chain is incomplete."""
    action_id = str(job.payload["actionId"])
    async with tenant_tx(job.org_id) as tx:
        row = (
            await tx.execute(
                select(ActionInstance, ActionTemplate)
                .join(ActionTemplate, ActionTemplate.id == ActionInstance.template_id)
                .where(ActionInstance.org_id == job.org_id, ActionInstance.id == action_id)
                .with_for_update(of=ActionInstance)
            )
        ).first()
        if row is None or row[0].state not in ("scheduled", "executing"):
            return
        a, tpl = row[0], row[1]
        if a.execute_after is not None and a.execute_after > clock.now():
            raise RuntimeError("not due yet")
        chain_ok = a.chain != "dual" or (a.maker_id and a.checker_id and a.maker_id != a.checker_id)
        auto_ok = a.chain == "auto" or a.maker_id
        if not chain_ok or not auto_ok:
            raise RuntimeError(f"refusing to execute {tpl.code}: approval chain incomplete")
        await tx.execute(
            update(ActionInstance)
            .where(ActionInstance.id == a.id)
            .values(state="executing")
            .execution_options(synchronize_session=False)
        )
        prepared = {
            "id": a.id,
            "ticket_id": a.ticket_id,
            "idempotency_key": a.idempotency_key,
            "fields": list(a.fields or []),
            "maker_id": a.maker_id,
            "checker_id": a.checker_id,
            "code": tpl.code,
            "name": tpl.name,
            "system": tpl.system,
            "endpoint": tpl.endpoint,
        }

    result = await connector.execute(
        ExecutionRequest(
            idempotency_key=prepared["idempotency_key"],
            system=prepared["system"],
            endpoint=prepared["endpoint"],
            code=prepared["code"],
            fields=prepared["fields"],
        )
    )

    async with tenant_tx(job.org_id) as tx:
        now = clock.now()
        await tx.execute(
            update(ActionInstance)
            .where(ActionInstance.id == prepared["id"])
            .values(state="executed", executed_at=now, external_ref=result.external_ref)
            .execution_options(synchronize_session=False)
        )
        t = await lock_ticket(tx, job.org_id, prepared["ticket_id"])
        maker = await _user(tx, prepared["maker_id"])
        checker = await _user(tx, prepared["checker_id"])
        by = (
            f"approved by {maker.name}{f', checked by {checker.name}' if checker else ''}"
            if maker
            else "executed by the AI in the approved cell"
        )
        t = await update_ticket(
            tx,
            t,
            {
                "status": "resolved",
                "resolved_at": now,
                "resolution": f"Action carried out · {by}",
                "next_move": f"Executed {now.strftime('%H:%M')}",
            },
        )
        await system_note(
            tx,
            t.org_id,
            t.id,
            f"Executed {prepared['code']} in {prepared['system']} (ref {result.external_ref}) — {by}. "
            "Written to the audit log.",
        )
        tx.add(
            PredictionOutcome(
                org_id=t.org_id,
                agent_name="Field Extractor",
                ticket_id=t.id,
                confidence=t.confidence,
                correct=True,
            )
        )
        await tx.flush()
        await record_ticket_event(
            tx,
            t,
            actor=AI_ACTOR,
            action="action.executed",
            summary=f"Executed {prepared['code']} · {prepared['name'].lower()} for QRY-{t.number}",
            data={
                "externalRef": result.external_ref,
                "idempotencyKey": prepared["idempotency_key"],
                "alreadyApplied": result.already_applied,
            },
            feed=Feed("ok", f"QRY-{t.number} · {'approved' if maker else 'auto'}"),
        )


async def _to_mailbox(tx: Any, t: Any, source: str, source_id: str) -> None:
    """With a connected mailbox that may send, the approved reply goes out in the customer's thread."""
    from command_inbox.mail.sync import live_mailbox_for_ticket, queue_send

    mb = await live_mailbox_for_ticket(tx, t.org_id, t.mailbox_id)
    if mb is not None:
        await queue_send(tx, t.org_id, mb, t.id, source, source_id)


async def run_send_draft(job: JobRow) -> None:
    draft_id = str(job.payload["draftId"])
    async with tenant_tx(job.org_id) as tx:
        d = (
            await tx.execute(
                select(Draft).where(Draft.org_id == job.org_id, Draft.id == draft_id).with_for_update()
            )
        ).scalar_one_or_none()
        if d is None or d.state != "scheduled":
            return
        t = await lock_ticket(tx, job.org_id, d.ticket_id)
        sender = await _user(tx, d.sent_by)
        now = clock.now()
        tx.add(
            Message(
                org_id=t.org_id,
                ticket_id=t.id,
                direction="outbound",
                from_name=sender.name if sender else "Customer care",
                from_addr=await _mailbox_address(tx, t.mailbox_id),
                to_addr=d.to_addr,
                body=d.current_body,
                sent_at=now,
            )
        )
        await tx.execute(
            update(Draft)
            .where(Draft.id == d.id)
            .values(state="sent", sent_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await _to_mailbox(tx, t, "draft", d.id)
        await set_subtask(tx, t.org_id, t.id, "b4", True, d.sent_by)
        edited = d.current_body.strip() != d.original_body.strip()
        if edited:
            # Draft edits are the cheapest eval data there is — store the diff against the drafter.
            await store_feedback(
                tx,
                None,
                t,
                "Reply Drafter",
                "edit",
                f"Sent after staff edits on QRY-{t.number}. Diff stored for the next version’s golden set.",
                "prompt",
                {"original": d.original_body, "current": d.current_body},
            )
        else:
            tx.add(
                PredictionOutcome(
                    org_id=t.org_id,
                    agent_name="Reply Drafter",
                    ticket_id=t.id,
                    confidence=t.confidence,
                    correct=True,
                )
            )
            await tx.flush()
        actor = _user_actor(sender)
        await add_comment(
            tx,
            t.org_id,
            t.id,
            actor,
            "public",
            f"Sent the reply “{d.subject}”{' (edited from the AI draft)' if edited else ' as drafted'}.",
        )
        t = await update_ticket(
            tx,
            t,
            {
                "status": "resolved",
                "resolved_at": now,
                "first_reply_at": t.first_reply_at or now,
                "resolution": "Reply sent and thread closed",
                "next_move": "Reply sent",
            },
        )
        await record_ticket_event(
            tx,
            t,
            actor=actor,
            action="draft.sent",
            summary=f"Reply sent to {t.from_name} on QRY-{t.number}{' (edited)' if edited else ''}",
            data={"draftId": d.id, "edited": edited},
            feed=Feed("ok", f"QRY-{t.number} · sent"),
        )


async def run_send_reply(job: JobRow) -> None:
    reply_id = str(job.payload["replyId"])
    async with tenant_tx(job.org_id) as tx:
        r = (
            await tx.execute(
                select(Reply).where(Reply.org_id == job.org_id, Reply.id == reply_id).with_for_update()
            )
        ).scalar_one_or_none()
        if r is None or r.state != "scheduled":
            return
        t = await lock_ticket(tx, job.org_id, r.ticket_id)
        author = await _user(tx, r.author_id)
        now = clock.now()
        tx.add(
            Message(
                org_id=t.org_id,
                ticket_id=t.id,
                direction="outbound",
                from_name=author.name if author else "Customer care",
                from_addr=await _mailbox_address(tx, t.mailbox_id),
                to_addr=t.from_email,
                body=r.body,
                sent_at=now,
            )
        )
        await tx.execute(
            update(Reply)
            .where(Reply.id == r.id)
            .values(state="sent", sent_at=now)
            .execution_options(synchronize_session=False)
        )
        await _to_mailbox(tx, t, "reply", r.id)
        actor = _user_actor(author)
        await add_comment(
            tx, t.org_id, t.id, actor, "public", r.body[:237] + "…" if len(r.body) > 240 else r.body
        )
        t = await update_ticket(tx, t, {"first_reply_at": t.first_reply_at or now})
        await record_ticket_event(
            tx, t, actor=actor, action="reply.sent", summary=f"Reply sent to {t.from_name} on QRY-{t.number}"
        )


async def run_escalate_checker(job: JobRow) -> None:
    """Checker silent for 2 h: escalate to the department lead and say so on the ticket."""
    action_id = str(job.payload["actionId"])
    async with tenant_tx(job.org_id) as tx:
        a = (
            await tx.execute(
                select(ActionInstance).where(
                    ActionInstance.org_id == job.org_id, ActionInstance.id == action_id
                )
            )
        ).scalar_one_or_none()
        if a is None or a.state != "awaiting_checker":
            return
        t = await lock_ticket(tx, job.org_id, a.ticket_id)
        t = await update_ticket(
            tx, t, {"priority": "P1", "next_move": "Checker silent 2h — escalated to the team lead"}
        )
        await system_note(
            tx,
            t.org_id,
            t.id,
            "The checker has been silent for 2 hours. Escalated to the team lead per the approval policy.",
        )
        await record_ticket_event(
            tx,
            t,
            actor=AI_ACTOR,
            action="action.checker_escalated",
            summary=f"QRY-{t.number} escalated — checker silent for 2 hours",
            feed=Feed("stop", f"QRY-{t.number} · escalation"),
        )


# ── Final failure: hand the ticket to a person ────────────────────────────────

_WHAT = {
    "execute_action": "execute the approved action",
    "send_draft": "send the approved reply",
    "send_reply": "send the reply",
    "escalate_checker": "escalate the waiting checker",
}


async def _ticket_of(tx: Any, job: JobRow) -> str | None:
    p = job.payload
    if job.kind in ("execute_action", "escalate_checker") and p.get("actionId"):
        return (
            await tx.execute(select(ActionInstance.ticket_id).where(ActionInstance.id == str(p["actionId"])))
        ).scalar()
    if job.kind == "send_draft" and p.get("draftId"):
        return (await tx.execute(select(Draft.ticket_id).where(Draft.id == str(p["draftId"])))).scalar()
    if job.kind == "send_reply" and p.get("replyId"):
        return (await tx.execute(select(Reply.ticket_id).where(Reply.id == str(p["replyId"])))).scalar()
    return None


async def handle_final_failure(job: JobRow) -> None:
    """A gateway job used up its retries: park the subject and put the ticket in the manual lane."""
    what = _WHAT.get(job.kind, job.kind)
    async with tenant_tx(job.org_id) as tx:
        ticket_id = await _ticket_of(tx, job)
        if ticket_id is None:
            log.warning("final failure for a job with no ticket", job_id=job.id, kind=job.kind)
            return
        t = await lock_ticket(tx, job.org_id, str(ticket_id))
        failure = f"Could not {what} after {job.attempts or job.max_attempts} attempts."
        if job.kind == "execute_action":
            # Only a subject still in flight is parked; an executed action stays executed.
            await tx.execute(
                update(ActionInstance)
                .where(
                    ActionInstance.id == str(job.payload["actionId"]),
                    ActionInstance.state.in_(("scheduled", "executing")),
                )
                .values(state="failed", failure=failure)
                .execution_options(synchronize_session=False)
            )
        elif job.kind == "send_draft":
            await tx.execute(
                update(Draft)
                .where(Draft.id == str(job.payload["draftId"]), Draft.state == "scheduled")
                .values(state="draft", send_after=None, updated_at=clock.now())
                .execution_options(synchronize_session=False)
            )
        elif job.kind == "send_reply":
            await tx.execute(
                update(Reply)
                .where(Reply.id == str(job.payload["replyId"]), Reply.state == "scheduled")
                .values(state="failed")
                .execution_options(synchronize_session=False)
            )
        patch: dict[str, Any] = {
            "lane": "manual",
            "lane_note": f"Handed back — could not {what}",
            "next_move": "The system could not finish this — check and handle it by hand",
        }
        if is_open(t.status):
            patch["status"] = "with_human"
        t = await update_ticket(tx, t, patch)
        await system_note(
            tx, t.org_id, t.id, f"{failure} The AI stepped back; a person needs to handle this ticket."
        )
        await record_ticket_event(
            tx,
            t,
            actor=SYSTEM_ACTOR,
            action="ai.handed_back",
            summary=f"QRY-{t.number} handed to a person — could not {what}",
            data={"jobId": job.id, "kind": job.kind},
            feed=Feed("stop", f"QRY-{t.number} · job failed"),
        )


def install(worker: Worker) -> Worker:
    """Register `handle_final_failure` for the gateway job kinds, chaining any handler already installed
    (e.g. triage's) for every other kind. Call once per worker, after `register_all`."""
    previous: JobHandler | None = getattr(worker, "_on_final_failure", None)

    async def dispatch(job: JobRow) -> None:
        if job.kind in GATEWAY_KINDS:
            await handle_final_failure(job)
        elif previous is not None:
            await previous(job)

    return worker.on_final_failure(dispatch)
