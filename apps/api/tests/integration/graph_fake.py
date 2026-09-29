"""A small, stateful Microsoft Graph for tests (respx): one mailbox with an inbox, drafts and sent items.

It answers the calls the connector makes with the shapes Graph returns, and can be told to misbehave
(expire the delta cursor, throttle, refuse the refresh token) for the chaos tests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import respx

BASE = "https://graph.microsoft.com/v1.0"


@dataclass
class Msg:
    id: str
    subject: str
    sender: str
    body: str
    received: str = "2026-09-29T10:00:00Z"
    headers: dict[str, str] = field(default_factory=dict)
    conversation: str = ""
    internet_id: str = ""
    categories: list[str] = field(default_factory=list)
    to: str = ""


@dataclass
class FakeGraph:
    account: str
    inbox: list[Msg] = field(default_factory=list)
    drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    sent: list[dict[str, Any]] = field(default_factory=list)
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_seq: int = 0
    counter: int = 0
    # chaos switches
    expire_cursor: bool = False
    throttle_next: int = 0
    refuse_refresh: bool = False
    calls: list[str] = field(default_factory=list)

    def deliver(self, subject: str, sender: str, body: str, **kw: Any) -> Msg:
        self.counter += 1
        m = Msg(
            id=f"AAMk-{self.counter}",
            subject=subject,
            sender=sender,
            body=body,
            conversation=kw.pop("conversation", f"conv-{self.counter}"),
            internet_id=kw.pop("internet_id", f"<m{self.counter}@mail.test>"),
            to=self.account,
            **kw,
        )
        self.inbox.append(m)
        return m

    # ── respx handlers ─────────────────────────────────────────────────────────────────────────────────

    def _json(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    def _throttled(self) -> httpx.Response | None:
        if self.throttle_next > 0:
            self.throttle_next -= 1
            return self._json(
                429, {"error": {"code": "TooManyRequests", "message": "slow down"}}, {"retry-after": "2"}
            )
        return None

    def token(self, request: httpx.Request) -> httpx.Response:
        if self.refuse_refresh:
            return self._json(400, {"error": "invalid_grant", "error_description": "AADSTS50173"})
        self.token_seq += 1
        return self._json(
            200,
            {
                "access_token": f"at-{self.token_seq}",
                "expires_in": 3600,
                "refresh_token": f"rt-{self.token_seq}",
            },
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        if (t := self._throttled()) is not None:
            return t
        path = unquote(request.url.path.removeprefix("/v1.0"))
        q = parse_qs(urlparse(str(request.url)).query)
        body = json.loads(request.content) if request.content else {}
        if path == "/me":
            return self._json(200, {"mail": self.account, "userPrincipalName": self.account})
        if path == "/subscriptions" and request.method == "POST":
            sid = f"sub-{len(self.subscriptions) + 1}"
            self.subscriptions[sid] = body
            return self._json(201, {"id": sid, "expirationDateTime": body["expirationDateTime"]})
        if m := re.fullmatch(r"/subscriptions/([^/]+)", path):
            if m.group(1) not in self.subscriptions:
                return self._json(404, {"error": {"code": "ResourceNotFound", "message": "gone"}})
            if request.method == "DELETE":
                self.subscriptions.pop(m.group(1))
                return httpx.Response(204)
            return self._json(200, {"id": m.group(1), "expirationDateTime": body.get("expirationDateTime")})
        if path == "/me/mailFolders('inbox')/messages/delta":
            if "$deltatoken" in q:
                if self.expire_cursor:
                    self.expire_cursor = False
                    return self._json(410, {"error": {"code": "SyncStateNotFound", "message": "expired"}})
                start = int(q["$deltatoken"][0])
                items = self.inbox[start:]
            else:
                items = list(self.inbox)  # the filter (received since) is honoured by the caller's baseline
                if "$filter" in q and "ge" in q["$filter"][0] and not self.inbox_history_visible:
                    items = []
            return self._json(
                200,
                {
                    "value": [{"id": m.id} for m in items],
                    "@odata.deltaLink": f"{BASE}/me/mailFolders('inbox')/messages/delta?$deltatoken={len(self.inbox)}",
                },
            )
        if path == "/me/mailFolders('sentitems')/messages":
            return self._json(
                200,
                {
                    "value": [
                        {"id": s["id"], "internetMessageHeaders": s["headers"]} for s in reversed(self.sent)
                    ]
                },
            )
        if path == "/me/sendMail":
            msg = body["message"]
            hdrs = {h["name"]: h["value"] for h in msg.get("internetMessageHeaders", [])}
            self.deliver(msg["subject"], self.account, msg["body"]["content"], headers=hdrs)
            return httpx.Response(202)
        if m := re.fullmatch(r"/me/messages/([^/]+)/createReply", path):
            orig = self._find(m.group(1))
            if orig is None:
                return self._json(404, {"error": {"code": "ErrorItemNotFound", "message": "no"}})
            did = f"draft-{len(self.drafts) + len(self.sent) + 1}"
            self.drafts[did] = {
                "id": did,
                "replyTo": orig.id,
                "comment": body.get("comment", ""),
                "headers": body.get("message", {}).get("internetMessageHeaders", []),
                "conversation": orig.conversation,
            }
            return self._json(201, {"id": did})
        if m := re.fullmatch(r"/me/messages/([^/]+)/send", path):
            d = self.drafts.pop(m.group(1), None)
            if d is None:
                return self._json(404, {"error": {"code": "ErrorItemNotFound", "message": "no draft"}})
            self.sent.append({**d, "id": f"sent-{len(self.sent) + 1}"})
            return httpx.Response(202)
        if m := re.fullmatch(r"/me/messages/([^/]+)/attachments", path):
            return self._json(200, {"value": []})
        if m := re.fullmatch(r"/me/messages/([^/]+)/\$value", path):
            msg = self._find(m.group(1))
            return (
                httpx.Response(200, content=f"Subject: {msg.subject}\r\n\r\n{msg.body}".encode())
                if msg
                else self._json(404, {})
            )
        if m := re.fullmatch(r"/me/messages/([^/]+)", path):
            if request.method == "DELETE":
                self.drafts.pop(m.group(1), None)
                return httpx.Response(204)
            msg = self._find(m.group(1))
            if msg is None:
                return self._json(404, {"error": {"code": "ErrorItemNotFound", "message": "no"}})
            if request.method == "PATCH":
                msg.categories = body.get("categories", msg.categories)
                return self._json(200, {"id": msg.id})
            return self._json(200, self._resource(msg))
        return self._json(501, {"error": {"code": "NotImplementedInFake", "message": path}})

    inbox_history_visible: bool = False

    def _find(self, mid: str) -> Msg | None:
        return next((m for m in self.inbox if m.id == mid), None)

    def _resource(self, m: Msg) -> dict[str, Any]:
        headers = [{"name": k, "value": v} for k, v in m.headers.items()]
        return {
            "id": m.id,
            "internetMessageId": m.internet_id,
            "conversationId": m.conversation,
            "subject": m.subject,
            "from": {"emailAddress": {"address": m.sender, "name": m.sender.split("@")[0].title()}},
            "toRecipients": [{"emailAddress": {"address": m.to}}],
            "ccRecipients": [],
            "receivedDateTime": m.received,
            "body": {"contentType": "text", "content": m.body},
            "internetMessageHeaders": headers,
            "hasAttachments": False,
            "categories": m.categories,
            "isDraft": False,
        }

    def mount(self, router: respx.Router) -> None:
        router.post(url__regex=r"https://login\.microsoftonline\.com/.*/token").mock(side_effect=self.token)
        router.route(url__startswith=BASE).mock(side_effect=self.handle)
