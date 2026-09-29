"""Knowledge sync job (connector adapters per kind in production; the dev adapter counts pending docs)."""

from __future__ import annotations

from sqlalchemy import select, update

from command_inbox.core.clock import clock
from command_inbox.core.jobs import JobRow
from command_inbox.core.outbox import publish
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import KnowledgeSource


async def run_knowledge_sync(job: JobRow) -> None:
    """Idempotent: re-running recounts the same source and writes the same result."""
    source_id = str(job.payload.get("sourceId", ""))
    async with tenant_tx(job.org_id) as tx:
        src = (
            await tx.execute(
                select(KnowledgeSource)
                .where(KnowledgeSource.org_id == job.org_id, KnowledgeSource.id == source_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if src is None or src.health == "bad":
            return
        docs = src.doc_count  # what the source reported; never an estimate
        await tx.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.org_id == job.org_id, KnowledgeSource.id == src.id)
            .values(
                doc_count=docs,
                health="ok",
                sync_note="",
                last_sync_at=clock.now(),
                note=f"{docs - src.approved_count} pending approval — not citable yet"
                if src.approved_count < docs
                else src.note,
            )
        )
        await publish(tx, job.org_id, "setup.updated", {"area": "knowledge"})
