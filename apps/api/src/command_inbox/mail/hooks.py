"""Provider notification ingress: `/v1/hooks/graph` (and `/lifecycle`), `/v1/hooks/gmail`.

Public by necessity, so nothing here trusts the caller:
- Graph: the validation handshake echoes `validationToken` as text/plain. Each notification must carry the
  `clientState` secret we set on that subscription (compared by hash, in constant time); anything else is
  dropped. A notification only *wakes* a sync: the sync reads mail through our own authenticated calls, so
  a forged notification can at most cause an extra catch-up.
- Answer fast (202): Graph drops an endpoint's notifications for 10 minutes when too many answers are slow.
  Work is queued, deduplicated per mailbox per 5 seconds.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import text, update

from command_inbox.core.clock import clock
from command_inbox.core.crypto import safe_equal, sha256
from command_inbox.core.jobs import enqueue
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Mailbox, MailSyncEvent
from command_inbox.mail.sync import enqueue_sync

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/hooks", tags=["hooks"], include_in_schema=False)
MAX_BODY = 256 * 1024


async def _stream(subscription_id: str) -> tuple[str, str, str | None] | None:
    async with global_tx() as g:
        row = (
            await g.execute(
                text("select org_id, mailbox_id, secret_hash from ci_mailbox_by_stream(:s)"),
                {"s": subscription_id},
            )
        ).first()
    return (str(row.org_id), str(row.mailbox_id), row.secret_hash) if row else None


async def _notifications(request: Request) -> list[dict[str, Any]]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return []
    try:
        body = await request.json()
    except ValueError:
        return []
    value = body.get("value") if isinstance(body, dict) else None
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


async def _accept(n: dict[str, Any], kind: str) -> None:
    sub = str(n.get("subscriptionId") or "")[:200]
    found = await _stream(sub) if sub else None
    if found is None:
        return
    org_id, mailbox_id, secret_hash = found
    if not secret_hash or not safe_equal(sha256(str(n.get("clientState") or "")), secret_hash):
        log.warning("graph notification with a wrong clientState", mailbox_id=mailbox_id)
        return
    event = str(n.get("lifecycleEvent") or "")
    async with tenant_tx(org_id) as tx:
        await tx.execute(
            update(Mailbox)
            .where(Mailbox.org_id == org_id, Mailbox.id == mailbox_id)
            .values(last_notification_at=clock.now())
        )
        tx.add(
            MailSyncEvent(
                org_id=org_id, mailbox_id=mailbox_id, kind=kind, detail={"event": event} if event else {}
            )
        )
        if event in ("reauthorizationRequired", "subscriptionRemoved"):
            reason = "reauthorize" if event == "reauthorizationRequired" else "recreate"
            await enqueue(
                tx,
                org_id,
                "mail_renew",
                {"mailboxId": mailbox_id, "reason": reason},
                dedupe_key=f"mail-renew:{mailbox_id}:{event}:{int(clock.now().timestamp() // 60)}",
            )
        await enqueue_sync(tx, org_id, mailbox_id, event or "notification")


@router.post("/graph")
async def graph(request: Request) -> Response:
    token = request.query_params.get("validationToken")
    if token is not None:
        return PlainTextResponse(token[:1024], status_code=200)
    for n in await _notifications(request):
        await _accept(n, "notification")
    return Response(status_code=202)


@router.post("/graph/lifecycle")
async def graph_lifecycle(request: Request) -> Response:
    token = request.query_params.get("validationToken")
    if token is not None:
        return PlainTextResponse(token[:1024], status_code=200)
    for n in await _notifications(request):
        await _accept(n, "lifecycle")
    return Response(status_code=202)
