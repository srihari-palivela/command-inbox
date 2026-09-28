"""HTTP routes of the approval gateway: approve (maker / checker / send / take), reject, undo, batch approve,
action field edits, draft edits, free-text replies and their recall.

A ticket id in a path is resolved inside the caller's tenant transaction (`load_ticket`): under row-level
security a ticket of another tenant does not exist, so it is a 404, never a 403.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response

from command_inbox.core.context import Ctx
from command_inbox.core.errors import AppError
from command_inbox.core.http import current_ctx, idempotent, in_tenant
from command_inbox.core.telemetry import gate_decisions
from command_inbox.db.engine import tenant_tx
from command_inbox.modules.gateway import service
from command_inbox.modules.gateway.schemas import (
    ApproveResult,
    BatchApproveItem,
    BatchApproveResult,
    ReplyScheduled,
)
from command_inbox.modules.tickets.queries import load_ticket
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import (
    UUID_RE,
    ActionFieldsBody,
    ApproveBody,
    BatchApproveBody,
    DraftBody,
    RejectBody,
    ReplyBody,
)

router = APIRouter(prefix="/v1", tags=["gateway"])

TicketId = Annotated[str, Path(min_length=3, max_length=64)]
ReplyId = Annotated[str, Path(pattern=UUID_RE)]


async def _resolve(ctx: Ctx, id_or_number: str) -> str:
    """Resolve a ticket number or id to the canonical id, within the caller's tenant only."""
    async with tenant_tx(ctx.org_id) as tx:
        return (await load_ticket(tx, ctx.org_id, id_or_number)).id


@router.post("/tickets/{id}/gate/approve", response_model=ApproveResult)
async def approve(
    id: TicketId, body: ApproveBody, request: Request, response: Response, ctx: Ctx = Depends(current_ctx)
) -> ApproveResult:
    ticket_id = await _resolve(ctx, id)
    r = await idempotent(
        request,
        response,
        ctx,
        body.model_dump(by_alias=True),
        lambda tx: service.approve(tx, ctx, ticket_id, body.opened_evidence),
    )
    result = r if isinstance(r, ApproveResult) else ApproveResult.model_validate(r)
    gate_decisions.labels(result.outcome, "approved", str(body.opened_evidence).lower()).inc()
    return result


@router.post("/tickets/{id}/gate/reject", response_model=Ok)
async def reject(id: TicketId, body: RejectBody, ctx: Ctx = Depends(current_ctx)) -> Ok:
    ticket_id = await _resolve(ctx, id)
    await in_tenant(ctx, lambda tx: service.reject(tx, ctx, ticket_id, body.reason))
    gate_decisions.labels("reject", body.reason, "true").inc()
    return Ok()


@router.post("/tickets/{id}/gate/undo", response_model=Ok)
async def undo(id: TicketId, ctx: Ctx = Depends(current_ctx)) -> Ok:
    ticket_id = await _resolve(ctx, id)
    await in_tenant(ctx, lambda tx: service.undo(tx, ctx, ticket_id))
    return Ok()


@router.post("/gate/batch-approve", response_model=BatchApproveResult)
async def batch_approve(body: BatchApproveBody, ctx: Ctx = Depends(current_ctx)) -> BatchApproveResult:
    """Each approval is its own transaction with its own audit entry. Approving from the list never opens the
    evidence, so these count toward the approve-without-open canary — that is the point of it."""
    out: list[BatchApproveItem] = []
    for ticket_id in body.ticket_ids:
        try:
            r = await in_tenant(ctx, lambda tx, tid=ticket_id: service.approve(tx, ctx, tid, False))
            out.append(BatchApproveItem(ticket_id=ticket_id, ok=True, outcome=r.outcome))
        except AppError as err:
            out.append(BatchApproveItem(ticket_id=ticket_id, ok=False, error=err.title))
        except Exception:  # an unexpected failure on one ticket must not hide the others' results
            out.append(BatchApproveItem(ticket_id=ticket_id, ok=False, error="failed"))
    return BatchApproveResult(results=out)


@router.patch("/tickets/{id}/action/fields", response_model=Ok)
async def edit_action_fields(id: TicketId, body: ActionFieldsBody, ctx: Ctx = Depends(current_ctx)) -> Ok:
    ticket_id = await _resolve(ctx, id)
    await in_tenant(ctx, lambda tx: service.edit_action_fields(tx, ctx, ticket_id, body.fields))
    return Ok()


@router.put("/tickets/{id}/draft", response_model=Ok)
async def edit_draft(id: TicketId, body: DraftBody, ctx: Ctx = Depends(current_ctx)) -> Ok:
    ticket_id = await _resolve(ctx, id)
    await in_tenant(ctx, lambda tx: service.edit_draft(tx, ctx, ticket_id, body.body))
    return Ok()


@router.post("/tickets/{id}/replies", response_model=ReplyScheduled)
async def reply(
    id: TicketId, body: ReplyBody, request: Request, response: Response, ctx: Ctx = Depends(current_ctx)
) -> ReplyScheduled:
    ticket_id = await _resolve(ctx, id)
    r = await idempotent(
        request,
        response,
        ctx,
        body.model_dump(by_alias=True),
        lambda tx: service.reply(tx, ctx, ticket_id, body.body),
    )
    return r if isinstance(r, ReplyScheduled) else ReplyScheduled.model_validate(r)


@router.post("/replies/{id}/recall", response_model=Ok)
async def recall(id: ReplyId, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(ctx, lambda tx: service.recall_reply(tx, ctx, id))
    return Ok()
