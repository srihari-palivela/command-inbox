"""The connector interface and the values that cross it.

A connector speaks to one mailbox with one credential. It never decides anything: the pipeline decides what
a message becomes, and a person approves every reply. The interface hides the credential type, so moving from
delegated OAuth (v1) to app-only access (v2) adds a credential provider and changes nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

INTENT_HEADER = "x-ci-intent"  # on every message we send: loop guard and send idempotency
TEST_HEADER = "x-ci-test"  # on test-mail round trips


class ConnectorError(Exception):
    """A provider call failed in a way the caller should record."""


class ReauthRequired(ConnectorError):
    """The credential is gone (revoked, expired, password reset): a person must reconnect the mailbox."""


class PermissionDenied(ConnectorError):
    """The credential works but may not do this (missing scope, blocked by policy)."""


class Throttled(ConnectorError):
    def __init__(self, retry_after: float, message: str = "throttled") -> None:
        super().__init__(message)
        self.retry_after = retry_after


class CursorExpired(ConnectorError):
    """The provider forgot our sync position (Graph 410 / syncStateNotFound, Gmail history 404)."""


class StreamGone(ConnectorError):
    """The subscription or watch no longer exists; recreate it."""


class NotFound(ConnectorError):
    """The message or draft does not exist (any more)."""


@dataclass(frozen=True, slots=True)
class Address:
    email: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class AttachmentMeta:
    id: str
    name: str
    content_type: str
    size: int
    is_inline: bool


@dataclass(slots=True)
class RawMessage:
    provider_id: str
    internet_message_id: str | None
    conversation_id: str | None
    subject: str
    sender: Address
    to: list[Address]
    cc: list[Address]
    received_at: datetime | None
    body_text: str
    # Header names lower-cased; a header may repeat (Received, Authentication-Results).
    headers: dict[str, list[str]] = field(default_factory=dict)
    mime: bytes | None = None
    attachments: list[AttachmentMeta] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def header(self, name: str) -> str | None:
        values = self.headers.get(name.lower())
        return values[0] if values else None


@dataclass(frozen=True, slots=True)
class Stream:
    id: str
    secret: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Delta:
    ids: list[str]
    cursor: str


class MailboxConnector(Protocol):
    provider: str

    async def account(self) -> str: ...

    async def start_stream(self, *, notify_url: str, lifecycle_url: str) -> Stream: ...

    async def renew_stream(self, stream_id: str) -> datetime: ...

    async def stop_stream(self, stream_id: str) -> None: ...

    async def catch_up(self, cursor: str | None, *, since: datetime) -> Delta:
        """New message ids since `cursor` (or since `since` when there is no cursor), and the next cursor."""
        ...

    async def fetch(self, provider_id: str) -> RawMessage: ...

    async def create_reply(self, reply_to_id: str, body: str, *, intent_id: str) -> str:
        """An unsent reply in the original's thread, with our intent header; returns the draft id."""
        ...

    async def send_draft(self, draft_id: str) -> None: ...

    async def find_sent(self, intent_id: str) -> str | None:
        """The provider id of a sent message carrying this intent, if one exists (retry after a crash)."""
        ...

    async def delete_draft(self, draft_id: str) -> None: ...

    async def label(self, provider_id: str, labels: list[str]) -> None: ...

    async def send_test(self, subject: str, body: str, nonce: str) -> None: ...
