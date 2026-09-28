"""The job worker process (also embedded in the API in development)."""

from __future__ import annotations

import asyncio
import signal

import structlog
from sqlalchemy import select, text

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


async def start_scheduler() -> None:
    """Recurring work, deduplicated per window so any number of workers enqueue it once."""
    while True:
        try:
            async with global_tx() as g:
                org_ids = (await g.execute(select(Org.id))).scalars().all()
            window = int(clock.now().timestamp() // 300)
            for org_id in org_ids:
                async with tenant_tx(org_id) as tx:
                    await enqueue(tx, org_id, "rerank", dedupe_key=f"rerank:{window}", max_attempts=1)
            async with global_tx() as g:
                await g.execute(text("select 1"))
        except Exception as err:
            log.warning("scheduler tick failed", err=str(err))
        await asyncio.sleep(60)


async def _main() -> None:
    configure_logging()
    configure_tracing(service_name=settings.otel_service_name.replace("-api", "-worker"))
    worker = build_worker()
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
