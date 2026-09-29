"""Microsoft 365 through Microsoft Graph, with a delegated token for the connected account (decision D3).

- Every call asks for immutable ids (`Prefer: IdType="ImmutableId"`), so ids survive moves between folders.
- At most 3 requests in flight per mailbox (Graph allows 4 per app and mailbox); 429/503 raise `Throttled`
  with the provider's `Retry-After`, and the job queue retries after it.
- Change notifications are basic (no resource data), which allows a subscription of up to 7 days; we ask
  for 6 and renew well before. Missed notifications are recovered by the delta sweep.
- Replies are created with `createReply` (Exchange keeps the conversation and sets In-Reply-To and
  References) and carry our `x-ci-intent` header; sending is a separate call, so a crash between the two is
  recoverable without sending twice. Messages are marked with categories and never deleted.

Header behaviour to confirm against the bank's tenant: custom `x-` internet headers set on createReply
survive to the sent copy **[verify]**.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.mail.types import (
    INTENT_HEADER,
    TEST_HEADER,
    Address,
    AttachmentMeta,
    ConnectorError,
    CursorExpired,
    Delta,
    NotFound,
    PermissionDenied,
    RawMessage,
    ReauthRequired,
    Stream,
    StreamGone,
    Throttled,
)

TokenSource = Callable[..., Awaitable[str]]
STREAM_DAYS = 6
MIME_LIMIT = 10 * 1024 * 1024
_SELECT = (
    "id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,body,"
    "internetMessageHeaders,hasAttachments,categories,isDraft"
)
_locks: dict[str, asyncio.Semaphore] = {}


def _semaphore(key: str) -> asyncio.Semaphore:
    if key not in _locks:
        _locks[key] = asyncio.Semaphore(3)
    return _locks[key]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _addr(obj: dict[str, Any] | None) -> Address:
    ea = (obj or {}).get("emailAddress") or {}
    return Address(email=str(ea.get("address") or "").lower(), name=str(ea.get("name") or ""))


class GraphConnector:
    provider = "graph"

    def __init__(self, token: TokenSource, *, key: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._key = key
        self._client = client

    # ── plumbing ───────────────────────────────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        full = url if url.startswith("http") else settings.graph_base_url.rstrip("/") + url
        async with _semaphore(self._key):
            for attempt in (1, 2):
                token = await self._token(force=attempt == 2)
                h = {"authorization": f"Bearer {token}", "prefer": 'IdType="ImmutableId"', **(headers or {})}
                client = self._client or httpx.AsyncClient(timeout=20)
                try:
                    r = await client.request(method, full, json=json, params=params, headers=h)
                finally:
                    if self._client is None:
                        await client.aclose()
                if r.status_code == 401 and attempt == 1:
                    continue  # the token may have been revoked early: refresh once, then give up
                break
        if r.status_code in (429, 503):
            raise Throttled(float(r.headers.get("retry-after", "10")), f"Graph {r.status_code}")
        if r.status_code == 401:
            raise ReauthRequired("Microsoft refused the sign-in.")
        if r.status_code == 403:
            raise PermissionDenied(_error_text(r))
        if r.status_code == 404:
            raise NotFound(_error_text(r))
        if r.status_code == 410:
            raise CursorExpired(_error_text(r))
        if r.status_code >= 400:
            text = _error_text(r)
            if "syncStateNotFound" in text or "SyncStateNotFound" in text:
                raise CursorExpired(text)
            raise ConnectorError(f"Graph {r.status_code}: {text}")
        if raw:
            return r.content
        return r.json() if r.content else None

    # ── interface ──────────────────────────────────────────────────────────────────────────────────────

    async def account(self) -> str:
        me = await self._request("GET", "/me", params={"$select": "mail,userPrincipalName"})
        return str(me.get("mail") or me.get("userPrincipalName") or "").lower()

    async def start_stream(self, *, notify_url: str, lifecycle_url: str) -> Stream:
        from command_inbox.core.crypto import random_token

        secret = random_token(32)
        expires = clock.now() + timedelta(days=STREAM_DAYS)
        body = {
            "changeType": "created",
            "notificationUrl": notify_url,
            "lifecycleNotificationUrl": lifecycle_url,
            "resource": "me/mailFolders('inbox')/messages",
            "expirationDateTime": expires.isoformat(),
            "clientState": secret,
        }
        sub = await self._request("POST", "/subscriptions", json=body)
        return Stream(
            id=str(sub["id"]), secret=secret, expires_at=_parse_time(sub.get("expirationDateTime")) or expires
        )

    async def renew_stream(self, stream_id: str) -> datetime:
        expires = clock.now() + timedelta(days=STREAM_DAYS)
        try:
            sub = await self._request(
                "PATCH", f"/subscriptions/{stream_id}", json={"expirationDateTime": expires.isoformat()}
            )
        except NotFound as err:
            raise StreamGone(str(err)) from err
        return _parse_time(sub.get("expirationDateTime")) or expires

    async def reauthorize_stream(self, stream_id: str) -> None:
        try:
            await self._request("POST", f"/subscriptions/{stream_id}/reauthorize")
        except NotFound as err:
            raise StreamGone(str(err)) from err

    async def stop_stream(self, stream_id: str) -> None:
        try:
            await self._request("DELETE", f"/subscriptions/{stream_id}")
        except NotFound:
            return

    async def catch_up(self, cursor: str | None, *, since: datetime) -> Delta:
        if cursor:
            url, params = cursor, None
        else:
            url = "/me/mailFolders('inbox')/messages/delta"
            params = {
                "$select": "id,receivedDateTime",
                "$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            }
        ids: list[str] = []
        while True:
            page = await self._request(
                "GET", url, params=params, headers={"prefer": 'IdType="ImmutableId", odata.maxpagesize=50'}
            )
            ids += [str(v["id"]) for v in page.get("value", []) if "@removed" not in v]
            if page.get("@odata.nextLink"):
                url, params = page["@odata.nextLink"], None
                continue
            return Delta(ids=ids, cursor=str(page["@odata.deltaLink"]))

    async def fetch(self, provider_id: str) -> RawMessage:
        pid = quote(provider_id, safe="")
        m = await self._request(
            "GET",
            f"/me/messages/{pid}",
            params={"$select": _SELECT},
            headers={"prefer": 'IdType="ImmutableId", outlook.body-content-type="text"'},
        )
        headers: dict[str, list[str]] = {}
        for h in m.get("internetMessageHeaders") or []:
            headers.setdefault(str(h.get("name", "")).lower(), []).append(str(h.get("value", "")))
        attachments: list[AttachmentMeta] = []
        if m.get("hasAttachments"):
            page = await self._request(
                "GET",
                f"/me/messages/{pid}/attachments",
                params={"$select": "id,name,contentType,size,isInline"},
            )
            attachments = [
                AttachmentMeta(
                    id=str(a["id"]),
                    name=str(a.get("name") or "attachment"),
                    content_type=str(a.get("contentType") or "application/octet-stream"),
                    size=int(a.get("size") or 0),
                    is_inline=bool(a.get("isInline")),
                )
                for a in page.get("value", [])
            ]
        mime: bytes | None = None
        try:
            mime = await self._request("GET", f"/me/messages/{pid}/$value", raw=True)
            if mime is not None and len(mime) > MIME_LIMIT:
                mime = None
        except (NotFound, PermissionDenied):
            mime = None
        return RawMessage(
            provider_id=str(m["id"]),
            internet_message_id=m.get("internetMessageId"),
            conversation_id=m.get("conversationId"),
            subject=str(m.get("subject") or ""),
            sender=_addr(m.get("from")),
            to=[_addr(a) for a in m.get("toRecipients") or []],
            cc=[_addr(a) for a in m.get("ccRecipients") or []],
            received_at=_parse_time(m.get("receivedDateTime")),
            body_text=str((m.get("body") or {}).get("content") or ""),
            headers=headers,
            mime=mime,
            attachments=attachments,
            labels=list(m.get("categories") or []),
        )

    async def create_reply(self, reply_to_id: str, body: str, *, intent_id: str) -> str:
        draft = await self._request(
            "POST",
            f"/me/messages/{quote(reply_to_id, safe='')}/createReply",
            json={
                "message": {"internetMessageHeaders": [{"name": INTENT_HEADER, "value": intent_id}]},
                "comment": body,
            },
        )
        return str(draft["id"])

    async def send_draft(self, draft_id: str) -> None:
        await self._request("POST", f"/me/messages/{quote(draft_id, safe='')}/send")

    async def delete_draft(self, draft_id: str) -> None:
        try:
            await self._request("DELETE", f"/me/messages/{quote(draft_id, safe='')}")
        except NotFound:
            return

    async def find_sent(self, intent_id: str) -> str | None:
        page = await self._request(
            "GET",
            "/me/mailFolders('sentitems')/messages",
            params={"$select": "id,internetMessageHeaders", "$top": "50", "$orderby": "sentDateTime desc"},
        )
        for m in page.get("value", []):
            for h in m.get("internetMessageHeaders") or []:
                if str(h.get("name", "")).lower() == INTENT_HEADER and h.get("value") == intent_id:
                    return str(m["id"])
        return None

    async def label(self, provider_id: str, labels: list[str]) -> None:
        pid = quote(provider_id, safe="")
        current = await self._request("GET", f"/me/messages/{pid}", params={"$select": "categories"})
        merged = sorted(set(current.get("categories") or []) | set(labels))
        await self._request("PATCH", f"/me/messages/{pid}", json={"categories": merged})

    async def send_test(self, subject: str, body: str, nonce: str) -> None:
        me = await self.account()
        await self._request(
            "POST",
            "/me/sendMail",
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": me}}],
                    "internetMessageHeaders": [{"name": TEST_HEADER, "value": nonce}],
                },
                "saveToSentItems": False,
            },
        )


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error") or {}
        return f"{err.get('code', '')}: {err.get('message', '')}".strip(": ")
    except Exception:
        return r.text[:300]
