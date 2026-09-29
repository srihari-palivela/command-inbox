"""The job worker process (also embedded in the API in development)."""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime

import structlog
from sqlalchemy import select

from command_inbox.config import settings
from command_inbox.core.clock import clock
from command_inbox.core.events import hub
from command_inbox.core.jobs import Worker, enqueue
from command_inbox.core.telemetry import configure_logging, configure_tracing
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Org

log = structlog.get_logger(__name__)


def build_worker() -> Worker:
    """Register every job handler. Handlers live with their modules and are imported lazily."""
    from command_inbox.worker_registry import install_failure_handlers, register_all

    return install_failure_handlers(register_all(Worker()))


async def schedule_tick(now: datetime | None = None, state: dict[str, int] | None = None) -> None:
    """One scheduler tick: enqueue each workspace's recurring jobs, deduplicated per time window, so any
    number of workers (and a restart mid-window) enqueue each exactly once."""
    now = now or clock.now()
    state = state if state is not None else {}
    ts = int(now.timestamp())
    minute, hour, day = ts // 60, ts // 3600, ts // 86400
    async with global_tx() as g:
        orgs = (
            await g.execute(
                select(Org.id, Org.siem_url).where(Org.status.not_in(("archived", "draft", "provisioning")))
            )
        ).all()
    org_ids = [o for o, _ in orgs]
    streaming = {o for o, url in orgs if url}
    for org_id in org_ids:
        async with tenant_tx(org_id) as tx:
            await enqueue(tx, org_id, "sla_sweep", dedupe_key=f"sla_sweep:{minute}", max_attempts=1)
            await enqueue(tx, org_id, "metrics_rollup", dedupe_key=f"metrics_rollup:{hour}", max_attempts=2)
            await enqueue(tx, org_id, "retention_sweep", dedupe_key=f"retention_sweep:{day}", max_attempts=3)
            if org_id in streaming:
                await enqueue(tx, org_id, "siem_push", dedupe_key=f"siem_push:{minute}", max_attempts=1)
    from command_inbox.mail.sync import schedule_mail

    await schedule_mail()
    if state.get("hour") != hour:  # hourly, whichever minute the first tick of the hour lands on
        state["hour"] = hour
        from command_inbox.knowledge.service import expire_sweep

        await expire_sweep()


async def start_scheduler() -> None:
    """Recurring work: one tick a minute."""
    state: dict[str, int] = {}
    while True:
        try:
            await schedule_tick(state=state)
        except Exception as err:
            log.warning("scheduler tick failed", err=str(err))
        await asyncio.sleep(60)


async def _main() -> None:
    configure_logging()
    configure_tracing(service_name=settings.otel_service_name.replace("-api", "-worker"))
    worker = build_worker()
    if settings.worker_metrics_port:
        from prometheus_client import start_http_server

        start_http_server(settings.worker_metrics_port)  # /metrics for Prometheus (jobs, sends, ingest lag)
    await hub.start()
    hub.on_job(worker.poke)
    worker.start()
    scheduler = asyncio.create_task(start_scheduler())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    scheduler.cancel()
    await worker.stop()
    await hub.stop()


def run() -> None:
    asyncio.run(_main())
