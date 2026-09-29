"""Provider notification ingress: `/v1/hooks/graph` (and `/lifecycle`), `/v1/hooks/gmail`.

- Gmail (Pub/Sub push): the request carries a Google-signed OIDC token. We verify its signature against
  Google's keys, the issuer, our configured audience, and that it was issued to the push subscription's
  service account with a verified email; otherwise the push is refused (401) and Pub/Sub retries.

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


# ── Gmail (Pub/Sub push) ──────────────────────────────────────────────────────────────────────────────

GOOGLE_CERTS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_google_keys: tuple[float, Any] | None = None


async def google_keys() -> Any:
    """Google's OIDC signing keys, cached for an hour. Replaced in tests."""
    import time

    import httpx
    from joserfc.jwk import KeySet

    global _google_keys
    if _google_keys is None or time.monotonic() - _google_keys[0] > 3600:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(GOOGLE_CERTS)
            r.raise_for_status()
        _google_keys = (time.monotonic(), KeySet.import_key_set(r.json()))
    return _google_keys[1]


async def verify_google_push(authorization: str | None) -> bool:
    from joserfc import jwt
    from joserfc.errors import JoseError

    from command_inbox.config import settings

    if not (settings.google_push_audience and settings.google_push_service_account):
        return False
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    try:
        token = jwt.decode(authorization[7:].strip(), await google_keys(), algorithms=["RS256"])
        jwt.JWTClaimsRegistry(
            leeway=30,
            iss={"essential": True, "values": list(GOOGLE_ISSUERS)},
            aud={"essential": True, "value": settings.google_push_audience},
            exp={"essential": True},
        ).validate(token.claims)
    except (JoseError, ValueError):
        return False
    claims = token.claims
    return (
        claims.get("email") == settings.google_push_service_account and claims.get("email_verified") is True
    )


@router.post("/gmail")
async def gmail(request: Request) -> Response:
    import base64
    import json

    if not await verify_google_push(request.headers.get("authorization")):
        return Response(status_code=401)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return Response(status_code=204)
    try:
        envelope = json.loads(raw)
        data = json.loads(base64.b64decode(envelope["message"]["data"]))
        address = str(data["emailAddress"]).lower()
    except (ValueError, KeyError, TypeError):
        return Response(status_code=204)  # acknowledged: a malformed message would only be redelivered
    found = await _stream(address)
    if found is None:
        return Response(status_code=204)
    org_id, mailbox_id, _secret = found
    async with tenant_tx(org_id) as tx:
        await tx.execute(
            update(Mailbox)
            .where(Mailbox.org_id == org_id, Mailbox.id == mailbox_id)
            .values(last_notification_at=clock.now())
        )
        tx.add(
            MailSyncEvent(
                org_id=org_id,
                mailbox_id=mailbox_id,
                kind="notification",
                detail={"historyId": str(data.get("historyId", ""))},
            )
        )
        await enqueue_sync(tx, org_id, mailbox_id, "notification")
    return Response(status_code=204)
