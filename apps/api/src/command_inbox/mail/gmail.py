"""Google Workspace through the Gmail API, with a delegated token for the connected account (decision D3).

- Notifications: `users.watch` on INBOX publishes to the bank's Pub/Sub topic; the push subscription calls
  `/v1/hooks/gmail` with a Google-signed OIDC token. A watch lasts at most 7 days; we renew it daily.
- Catch-up: `history.list` from the stored historyId (messageAdded, INBOX). A 404 means the history is gone:
  resync with `messages.list` since a day before the last message, then continue from the new historyId.
- Fetch: `messages.get?format=raw` (the full MIME, parsed here), thread id as the conversation id.
- Replies: an RFC 5322 message with In-Reply-To and References from the original, the same subject, sent in
  the original's thread (`threadId`). We set its Message-ID from our intent id, so a retry after a crash
  finds the sent copy with `rfc822msgid:` instead of sending again. Labels `CI/…`, never delete.
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from typing import Any

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
    Throttled,
)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TokenSource = Callable[..., Awaitable[str]]
_locks: dict[str, asyncio.Semaphore] = {}
_TAG = re.compile(r"<[^>]+>")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def label_name(label: str) -> str:
    """ "CI: triaged" → "CI/triaged" (a nested Gmail label)."""
    return label.replace(": ", "/")


def intent_message_id(intent_id: str, account: str) -> str:
    return f"<ci-{intent_id}@{account.rsplit('@', 1)[-1]}>"


def parse_mime(provider_id: str, thread_id: str | None, raw: bytes, labels: list[str]) -> RawMessage:
    msg = message_from_bytes(raw, policy=policy.default)
    headers: dict[str, list[str]] = {}
    for name, value in msg.items():
        headers.setdefault(name.lower(), []).append(str(value))
    body_text, html = "", ""
    attachments: list[AttachmentMeta] = []
    for i, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if filename or disposition == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                AttachmentMeta(
                    id=f"part-{i}",
                    name=filename or "attachment",
                    content_type=part.get_content_type(),
                    size=len(payload),
                    is_inline=disposition == "inline",
                )
            )
            continue
        if part.get_content_type() == "text/plain" and not body_text:
            body_text = str(part.get_content())
        elif part.get_content_type() == "text/html" and not html:
            html = str(part.get_content())
    if not body_text and html:
        body_text = unescape(_TAG.sub(" ", html))
    sender = getaddresses([str(msg.get("from", ""))])
    date = msg.get("date")
    try:
        received = parsedate_to_datetime(str(date)) if date else None
    except (TypeError, ValueError):
        received = None
    return RawMessage(
        provider_id=provider_id,
        internet_message_id=str(msg.get("message-id")) if msg.get("message-id") else None,
        conversation_id=thread_id,
        subject=str(msg.get("subject", "")),
        sender=Address(email=(sender[0][1] if sender else "").lower(), name=sender[0][0] if sender else ""),
        to=[
            Address(email=a.lower(), name=n) for n, a in getaddresses([str(v) for v in msg.get_all("to", [])])
        ],
        cc=[
            Address(email=a.lower(), name=n) for n, a in getaddresses([str(v) for v in msg.get_all("cc", [])])
        ],
        received_at=received,
        body_text=body_text,
        headers=headers,
        mime=raw,
        attachments=attachments,
        labels=labels,
    )


class GmailConnector:
    provider = "gmail"

    def __init__(self, token: TokenSource, *, key: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._key = key
        self._client = client
        self._account: str | None = None
        self._labels: dict[str, str] | None = None

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, str] | None = None
    ) -> Any:
        url = path if path.startswith("http") else API + path
        sem = _locks.setdefault(self._key, asyncio.Semaphore(3))
        async with sem:
            for attempt in (1, 2):
                token = await self._token(force=attempt == 2)
                client = self._client or httpx.AsyncClient(timeout=20)
                try:
                    r = await client.request(
                        method, url, json=json, params=params, headers={"authorization": f"Bearer {token}"}
                    )
                finally:
                    if self._client is None:
                        await client.aclose()
                if r.status_code == 401 and attempt == 1:
                    continue
                break
        if (
            r.status_code == 429
            or (r.status_code == 403 and "rateLimitExceeded" in r.text)
            or r.status_code == 503
        ):
            raise Throttled(float(r.headers.get("retry-after", "10")), f"Gmail {r.status_code}")
        if r.status_code == 401:
            raise ReauthRequired("Google refused the sign-in.")
        if r.status_code == 403:
            raise PermissionDenied(r.text[:300])
        if r.status_code == 404:
            raise NotFound(r.text[:300])
        if r.status_code >= 400:
            raise ConnectorError(f"Gmail {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    async def account(self) -> str:
        if self._account is None:
            self._account = str((await self._request("GET", "/profile"))["emailAddress"]).lower()
        return self._account

    async def start_stream(self, *, notify_url: str, lifecycle_url: str) -> Stream:
        from command_inbox.core.crypto import random_token

        if not settings.google_pubsub_topic:
            raise ConnectorError("GOOGLE_PUBSUB_TOPIC is not configured.")
        w = await self._request(
            "POST",
            "/watch",
            json={
                "topicName": settings.google_pubsub_topic,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            },
        )
        expires = datetime.fromtimestamp(int(w["expiration"]) / 1000, tz=UTC)
        # Gmail pushes carry the mailbox address, so the address identifies the stream.
        return Stream(id=await self.account(), secret=random_token(16), expires_at=expires)

    async def renew_stream(self, stream_id: str) -> datetime:
        stream = await self.start_stream(notify_url="", lifecycle_url="")
        return stream.expires_at

    async def stop_stream(self, stream_id: str) -> None:
        await self._request("POST", "/stop")

    async def catch_up(self, cursor: str | None, *, since: datetime) -> Delta:
        if cursor is None:
            baseline = str((await self._request("GET", "/profile"))["historyId"])
            ids: list[str] = []
            if since < clock.now() - timedelta(seconds=5):
                page_token: str | None = None
                while True:
                    params = {"q": f"in:inbox after:{int(since.timestamp())}", "maxResults": "100"}
                    if page_token:
                        params["pageToken"] = page_token
                    page = await self._request("GET", "/messages", params=params)
                    ids += [m["id"] for m in page.get("messages", [])]
                    page_token = page.get("nextPageToken")
                    if not page_token:
                        break
            return Delta(ids=ids, cursor=baseline)
        ids, latest, page_token = [], cursor, None
        while True:
            params = {"startHistoryId": cursor, "historyTypes": "messageAdded", "labelId": "INBOX"}
            if page_token:
                params["pageToken"] = page_token
            try:
                page = await self._request("GET", "/history", params=params)
            except NotFound as err:
                raise CursorExpired(str(err)) from err
            for h in page.get("history", []):
                for added in h.get("messagesAdded", []):
                    mid = added["message"]["id"]
                    if mid not in ids:
                        ids.append(mid)
            latest = str(page.get("historyId") or latest)
            page_token = page.get("nextPageToken")
            if not page_token:
                return Delta(ids=ids, cursor=latest)

    async def fetch(self, provider_id: str) -> RawMessage:
        m = await self._request("GET", f"/messages/{provider_id}", params={"format": "raw"})
        return parse_mime(str(m["id"]), m.get("threadId"), _b64d(m["raw"]), list(m.get("labelIds") or []))

    async def create_reply(self, reply_to_id: str, body: str, *, intent_id: str) -> str:
        full = await self.fetch(reply_to_id)
        me = await self.account()
        msg = EmailMessage()
        msg["From"] = me
        msg["To"] = full.header("reply-to") or full.sender.email
        subject = full.subject or ""
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        original_id = full.internet_message_id or ""
        if original_id:
            msg["In-Reply-To"] = original_id
            msg["References"] = " ".join(filter(None, [full.header("references") or "", original_id])).strip()
        msg["Message-ID"] = intent_message_id(intent_id, me)
        msg[INTENT_HEADER] = intent_id
        msg.set_content(body)
        draft = await self._request(
            "POST", "/drafts", json={"message": {"raw": _b64e(bytes(msg)), "threadId": full.conversation_id}}
        )
        return str(draft["id"])

    async def send_draft(self, draft_id: str) -> None:
        await self._request("POST", "/drafts/send", json={"id": draft_id})

    async def delete_draft(self, draft_id: str) -> None:
        try:
            await self._request("DELETE", f"/drafts/{draft_id}")
        except NotFound:
            return

    async def find_sent(self, intent_id: str) -> str | None:
        rfc_id = intent_message_id(intent_id, await self.account()).strip("<>")
        page = await self._request("GET", "/messages", params={"q": f"in:sent rfc822msgid:{rfc_id}"})
        found = page.get("messages") or []
        return str(found[0]["id"]) if found else None

    async def _label_ids(self, names: list[str]) -> list[str]:
        if self._labels is None:
            page = await self._request("GET", "/labels")
            self._labels = {lb["name"]: lb["id"] for lb in page.get("labels", [])}
        out = []
        for name in names:
            if name not in self._labels:
                created = await self._request(
                    "POST",
                    "/labels",
                    json={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
                )
                self._labels[name] = created["id"]
            out.append(self._labels[name])
        return out

    async def label(self, provider_id: str, labels: list[str]) -> None:
        ids = await self._label_ids([label_name(lb) for lb in labels])
        await self._request("POST", f"/messages/{provider_id}/modify", json={"addLabelIds": ids})

    async def send_test(self, subject: str, body: str, nonce: str) -> None:
        me = await self.account()
        msg = EmailMessage()
        msg["From"] = me
        msg["To"] = me
        msg["Subject"] = subject
        msg[TEST_HEADER] = nonce
        msg.set_content(body)
        await self._request("POST", "/messages/send", json={"raw": _b64e(bytes(msg))})
