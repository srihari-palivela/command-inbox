"""Fan-out of committed domain events to connected browsers (Server-Sent Events).

Each API process holds one LISTEN connection. NOTIFY is delivered only on commit, so clients never see an
event for a rolled-back change. The same connection wakes the embedded job worker on new jobs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import asyncpg
import structlog

from command_inbox.config import settings
from command_inbox.core.jobs import JOBS_CHANNEL
from command_inbox.core.outbox import EVENTS_CHANNEL

log = structlog.get_logger(__name__)


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._job_listeners: list[Callable[[], None]] = []
        self._conn: asyncpg.Connection | None = None
        self._stopped = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stopped:
            try:
                conn = await asyncpg.connect(_dsn(settings.database_url))
                await conn.add_listener(EVENTS_CHANNEL, self._on_event)
                await conn.add_listener(JOBS_CHANNEL, self._on_job)
                self._conn = conn
                while not self._stopped and not conn.is_closed():  # noqa: ASYNC110 - poll for a dropped connection
                    await asyncio.sleep(1)
            except Exception as err:
                log.warning("event hub connection lost; reconnecting", err=str(err))
                await asyncio.sleep(2)
            finally:
                if self._conn is not None and not self._conn.is_closed():
                    await self._conn.close()
                self._conn = None

    def _on_event(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            evt = json.loads(payload)
        except ValueError:
            return
        if evt.get("topic") == "rbac.updated":
            from command_inbox.rbac.policy import policies

            policies.invalidate(str(evt.get("orgId")))
        for q in list(self._subs.get(str(evt.get("orgId")), ())):
            if q.full():  # a slow client loses its oldest event, not everyone's newest
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(evt)

    def _on_job(self, *_args: object) -> None:
        for fn in self._job_listeners:
            fn()

    def subscribe(self, org_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subs[org_id].add(q)
        return q

    def unsubscribe(self, org_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs[org_id].discard(q)

    def on_job(self, fn: Callable[[], None]) -> None:
        self._job_listeners.append(fn)

    def clients(self) -> int:
        return sum(len(s) for s in self._subs.values())

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()


hub = EventHub()
