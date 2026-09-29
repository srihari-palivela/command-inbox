"""Postgres-backed job queue.

Jobs are claimed with `FOR UPDATE SKIP LOCKED`, so any number of workers run side by side. Handlers must
be idempotent: a crash after the effect but before `done` re-runs the job. A job is enqueued inside the
caller's transaction, so it exists if and only if the change commits.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog
from opentelemetry import trace
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.clock import clock
from command_inbox.db.engine import global_tx
from command_inbox.db.models import Job

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

JOBS_CHANNEL = "ci_jobs"
JobKind = Literal[
    "triage",
    "execute_action",
    "send_draft",
    "send_reply",
    "escalate_checker",
    "rerank",
    "sla_sweep",
    "knowledge_sync",
    "call_progress",
    "eval_run",
    "retention_sweep",
    "send_email",
    "provision_tenant",
    "mail_connect",
    "mail_sync",
    "mail_renew",
    "mail_send",
    "mail_test",
    "knowledge_ingest",
]


@dataclass(frozen=True, slots=True)
class JobRow:
    id: int
    org_id: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


JobHandler = Callable[[JobRow], Awaitable[None]]


async def enqueue(
    tx: AsyncSession,
    org_id: str,
    kind: JobKind,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> None:
    await tx.execute(
        insert(Job)
        .values(
            org_id=org_id,
            kind=kind,
            payload=payload or {},
            run_at=run_at or clock.now(),
            dedupe_key=dedupe_key,
            max_attempts=max_attempts,
        )
        .on_conflict_do_nothing()
    )
    await tx.execute(text("select pg_notify(:ch, :kind)"), {"ch": JOBS_CHANNEL, "kind": kind})


async def cancel_job(tx: AsyncSession, org_id: str, dedupe_key: str) -> bool:
    """Cancel a queued job (e.g. an undo inside the window). True if one was cancelled."""
    res = await tx.execute(
        update(Job)
        .where(Job.org_id == org_id, Job.dedupe_key == dedupe_key, Job.state == "queued")
        .values(state="cancelled")
        .returning(Job.id)
    )
    return res.first() is not None


class Worker:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self.id = f"{socket.gethostname()}:{os.getpid()}:{random.randrange(16**5):05x}"  # noqa: S311
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._on_final_failure: JobHandler | None = None
        self._last_reap = 0.0

    def on_final_failure(self, handler: JobHandler) -> Worker:
        """Called when a job exhausts its attempts (e.g. triage → hand the ticket to a person)."""
        self._on_final_failure = handler
        return self

    def register(self, kind: str, handler: JobHandler) -> Worker:
        self._handlers[kind] = handler
        return self

    async def reap(self, lease_seconds: int = 300) -> int:
        """Requeue jobs whose worker died mid-run (lease expired); fail them once attempts are used up."""
        async with global_tx() as tx:
            res = await tx.execute(
                text("""
                update jobs set state = case when attempts >= max_attempts then 'failed' else 'queued' end,
                       locked_by = null, last_error = coalesce(last_error, 'lease expired')
                 where state = 'running' and locked_at < now() - make_interval(secs => :lease)
                returning id, kind, state, org_id, payload"""),
                {"lease": lease_seconds},
            )
            rows = res.all()
        for r in rows:
            log.warning("job lease expired", job_id=r.id, kind=r.kind, now=r.state)
            if r.state == "failed" and self._on_final_failure:
                await self._on_final_failure(JobRow(int(r.id), str(r.org_id), r.kind, r.payload or {}, 0, 0))
        return len(rows)

    async def tick(self) -> bool:
        """Claim and run one due job. False when nothing was due."""
        async with global_tx() as tx:
            row = (
                await tx.execute(
                    text("""
                update jobs set state = 'running', locked_by = :me, locked_at = now(), attempts = attempts + 1
                 where id = (select id from jobs where state = 'queued' and run_at <= :now
                             order by run_at, id for update skip locked limit 1)
                returning id, org_id, kind, payload, attempts, max_attempts"""),
                    {"me": self.id, "now": clock.now()},
                )
            ).first()
        if row is None:
            return False
        job = JobRow(
            int(row.id), str(row.org_id), row.kind, row.payload or {}, row.attempts, row.max_attempts
        )
        handler = self._handlers.get(job.kind)
        with tracer.start_as_current_span(
            f"job {job.kind}",
            attributes={
                "job.id": job.id,
                "job.kind": job.kind,
                "job.attempt": job.attempts,
                "tenant.id": job.org_id,
            },
        ) as span:
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for job kind {job.kind}")
                await handler(job)
                async with global_tx() as tx:
                    await tx.execute(
                        update(Job).where(Job.id == job.id).values(state="done", last_error=None)
                    )
            except Exception as err:
                span.record_exception(err)
                final = job.attempts >= job.max_attempts
                backoff = min(300.0, 2.0**job.attempts) * (0.75 + random.random() * 0.5)  # noqa: S311
                log.warning(
                    "job failed",
                    job_id=job.id,
                    kind=job.kind,
                    attempt=job.attempts,
                    final=final,
                    err=str(err),
                )
                async with global_tx() as tx:
                    await tx.execute(
                        update(Job)
                        .where(Job.id == job.id)
                        .values(
                            state="failed" if final else "queued",
                            last_error=str(err)[:2000],
                            run_at=clock.now() + timedelta(seconds=backoff),
                            locked_by=None,
                        )
                    )
                if final and self._on_final_failure:
                    try:
                        await self._on_final_failure(job)
                    except Exception:
                        log.exception("final-failure handler failed", job_id=job.id)
        return True

    async def drain(self, limit: int = 500) -> int:
        """Run everything currently due (tests and the embedded dev worker)."""
        n = 0
        while n < limit and await self.tick():
            n += 1
        return n

    def poke(self) -> None:
        """Wake immediately (on NOTIFY ci_jobs) instead of waiting for the next poll."""
        self._wake.set()

    async def _loop(self, poll: float) -> None:
        import time

        while self._running:
            if time.monotonic() - self._last_reap > 60:
                self._last_reap = time.monotonic()
                try:
                    await self.reap()
                except Exception:
                    log.exception("lease reaper failed")
            try:
                did = await self.tick()
            except Exception:
                log.exception("worker tick crashed")
                did = False
            if did:
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=poll)

    def start(self, poll: float = 0.5) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(poll))
        log.info("job worker started", worker=self.id)

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._task:
            await self._task
