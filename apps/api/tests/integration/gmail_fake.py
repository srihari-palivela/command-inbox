"""A small, stateful Gmail API for tests (respx): inbox and sent messages as real MIME, history, drafts,
labels and watch."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import respx

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass
class Item:
    id: str
    thread: str
    raw: bytes
    labels: list[str]


@dataclass
class FakeGmail:
    account: str
    messages: list[Item] = field(default_factory=list)
    history: list[tuple[int, str]] = field(default_factory=list)  # (historyId, messageId)
    drafts: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    history_id: int = 1000
    watches: int = 0
    expire_history: bool = False
    counter: int = 0

    def deliver(
        self,
        subject: str,
        sender: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
        thread: str | None = None,
    ) -> Item:
        self.counter += 1
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = self.account
        msg["Subject"] = subject
        msg["Message-ID"] = f"<g{self.counter}@customer.test>"
        msg["Date"] = "Tue, 29 Sep 2026 10:00:00 +0000"
        for k, v in (headers or {}).items():
            msg[k] = v
        msg.set_content(body)
        item = Item(
            id=f"g{self.counter}", thread=thread or f"t{self.counter}", raw=bytes(msg), labels=["INBOX"]
        )
        self.messages.append(item)
        self.history_id += 1
        self.history.append((self.history_id, item.id))
        return item

    def sent(self) -> list[Item]:
        return [m for m in self.messages if "SENT" in m.labels]

    def find(self, mid: str) -> Item | None:
        return next((m for m in self.messages if m.id == mid), None)

    def _json(self, status: int, body: Any = None) -> httpx.Response:
        return httpx.Response(status, json=body)

    def token(self, request: httpx.Request) -> httpx.Response:
        return self._json(200, {"access_token": "g-at", "expires_in": 3600})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        body = json.loads(request.content) if request.content else {}
        if path == "/profile":
            return self._json(200, {"emailAddress": self.account, "historyId": str(self.history_id)})
        if path == "/watch":
            self.watches += 1
            return self._json(200, {"historyId": str(self.history_id), "expiration": "1893456000000"})
        if path == "/stop":
            return httpx.Response(204)
        if path == "/history":
            if self.expire_history:
                self.expire_history = False
                return self._json(404, {"error": {"code": 404, "message": "Requested entity was not found."}})
            start = int(q["startHistoryId"])
            added = [
                {"messagesAdded": [{"message": {"id": mid}}]} for hid, mid in self.history if hid > start
            ]
            return self._json(200, {"history": added, "historyId": str(self.history_id)})
        if path == "/messages" and request.method == "GET":
            query = q.get("q", "")
            if m := re.search(r"rfc822msgid:(\S+)", query):
                wanted = f"<{m.group(1)}>"
                hits = [
                    x
                    for x in self.sent()
                    if message_from_bytes(x.raw, policy=policy.default)["Message-ID"] == wanted
                ]
            else:
                hits = [x for x in self.messages if "INBOX" in x.labels]
            return self._json(
                200, {"messages": [{"id": x.id, "threadId": x.thread} for x in hits]} if hits else {}
            )
        if path == "/messages/send":
            self._store_sent(b64d(body["raw"]), body.get("threadId"), inbox=True)
            return self._json(200, {"id": "sent"})
        if m := re.fullmatch(r"/messages/([^/]+)/modify", path):
            item = self.find(m.group(1))
            item.labels = sorted(set(item.labels) | set(body.get("addLabelIds", [])))
            return self._json(200, {"id": item.id})
        if m := re.fullmatch(r"/messages/([^/]+)", path):
            item = self.find(m.group(1))
            if item is None:
                return self._json(404, {"error": {"code": 404}})
            return self._json(
                200, {"id": item.id, "threadId": item.thread, "labelIds": item.labels, "raw": b64e(item.raw)}
            )
        if path == "/drafts" and request.method == "POST":
            did = f"d{len(self.drafts) + 1}"
            self.drafts[did] = body["message"]
            return self._json(200, {"id": did})
        if path == "/drafts/send":
            d = self.drafts.pop(body["id"], None)
            if d is None:
                return self._json(404, {"error": {"code": 404}})
            self._store_sent(b64d(d["raw"]), d.get("threadId"))
            return self._json(200, {"id": "ok"})
        if m := re.fullmatch(r"/drafts/([^/]+)", path):
            self.drafts.pop(m.group(1), None)
            return httpx.Response(204)
        if path == "/labels":
            if request.method == "POST":
                lid = f"Label_{len(self.labels) + 1}"
                self.labels[body["name"]] = lid
                return self._json(200, {"id": lid, "name": body["name"]})
            return self._json(200, {"labels": [{"id": v, "name": k} for k, v in self.labels.items()]})
        return self._json(501, {"error": {"message": f"not in fake: {path}"}})

    def _store_sent(self, raw: bytes, thread: str | None, inbox: bool = False) -> None:
        self.counter += 1
        item = Item(
            id=f"g{self.counter}",
            thread=thread or f"t{self.counter}",
            raw=raw,
            labels=["SENT"] + (["INBOX"] if inbox else []),
        )
        self.messages.append(item)
        if inbox:
            self.history_id += 1
            self.history.append((self.history_id, item.id))

    def mount(self, router: respx.Router) -> None:
        router.post("https://oauth2.googleapis.com/token").mock(side_effect=self.token)
        router.route(url__startswith=API).mock(side_effect=self.handle)
