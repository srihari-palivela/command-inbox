"""Knowledge documents: upload → scan → parse → chunk → embed → approve → retrievable.

- Uploads are sealed with the tenant's key and processed by the `knowledge_ingest` job. A file that fails
  the virus scan is never parsed and its content is discarded.
- A document is `pending` until someone with approve clearance for its department approves it; only then
  can a draft cite it. Approving a new version retires the version it replaces.
- Expired documents become `stale` (hourly sweep) and stop being retrievable.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.config import settings
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import SYSTEM_ACTOR, Ctx, actor_of
from command_inbox.core.errors import conflict, not_found, unprocessable
from command_inbox.core.jobs import JobRow, enqueue
from command_inbox.db.engine import global_tx, tenant_tx
from command_inbox.db.models import Department, KnowledgeChunk, KnowledgeDoc, User
from command_inbox.knowledge import av
from command_inbox.knowledge.chunk import chunk
from command_inbox.knowledge.embed import embedder
from command_inbox.knowledge.parse import Block, kind_of
from command_inbox.knowledge.retrieve import retrieve
from command_inbox.knowledge.sandbox import ParseFailed, parse_isolated
from command_inbox.platform.keys import tenant_decrypt, tenant_encrypt
from command_inbox.rbac.clearance import clearance_of, require_clearance
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto

APPROVE = 3


def _aad(doc_id: str) -> str:
    return f"knowledge|{doc_id}"


async def _can_approve(tx: AsyncSession, ctx: Ctx, department_id: str | None) -> bool:
    if department_id is None:
        return ctx.role == "admin"
    return await clearance_of(tx, ctx.org_id, ctx.user.id, department_id) >= APPROVE


async def _dto(
    tx: AsyncSession, ctx: Ctx, d: KnowledgeDoc, names: dict[str, str], people: dict[str, str]
) -> dto.KnowledgeDocumentDTO:
    return dto.KnowledgeDocumentDTO(
        id=d.id,
        title=d.title,
        filename=d.filename,
        version=d.version,
        replaces_id=d.replaces_id,
        status=d.status,  # type: ignore[arg-type]
        parse_status=d.parse_status,  # type: ignore[arg-type]
        parse_error=d.parse_error,
        av_status=d.av_status,  # type: ignore[arg-type]
        department_id=d.department_id,
        department=names.get(str(d.department_id)) if d.department_id else None,
        size=d.size,
        chunk_count=d.chunk_count,
        effective_from=iso_ms(d.effective_from) if d.effective_from else None,
        expires_at=iso_ms(d.expires_at) if d.expires_at else None,
        uploaded_by=people.get(str(d.uploaded_by)) if d.uploaded_by else None,
        approved_by=people.get(str(d.approved_by)) if d.approved_by else None,
        approved_at=iso_ms(d.approved_at) if d.approved_at else None,
        created_at=iso_ms(d.created_at),
        can_approve=d.status == "pending"
        and d.parse_status == "ready"
        and await _can_approve(tx, ctx, d.department_id),
    )


async def _lookups(
    tx: AsyncSession, org_id: str, docs: list[KnowledgeDoc]
) -> tuple[dict[str, str], dict[str, str]]:
    names = {
        str(r.id): r.name
        for r in (
            await tx.execute(select(Department.id, Department.name).where(Department.org_id == org_id))
        ).all()
    }
    ids = {str(x) for d in docs for x in (d.uploaded_by, d.approved_by) if x}
    people = (
        {
            str(r.id): r.name
            for r in (await tx.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
        }
        if ids
        else {}
    )
    return names, people


async def list_documents(tx: AsyncSession, ctx: Ctx) -> list[dto.KnowledgeDocumentDTO]:
    require(ctx, "setup.view", "see knowledge")
    docs = (
        (
            await tx.execute(
                select(KnowledgeDoc)
                .where(KnowledgeDoc.org_id == ctx.org_id)
                .order_by(KnowledgeDoc.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    names, people = await _lookups(tx, ctx.org_id, list(docs))
    return [await _dto(tx, ctx, d, names, people) for d in docs]


async def _get(tx: AsyncSession, ctx: Ctx, doc_id: str, lock: bool = False) -> KnowledgeDoc:
    q = select(KnowledgeDoc).where(KnowledgeDoc.org_id == ctx.org_id, KnowledgeDoc.id == doc_id)
    d = (await tx.execute(q.with_for_update() if lock else q)).scalar_one_or_none()
    if d is None:
        raise not_found("Document")
    return d


async def get_document(tx: AsyncSession, ctx: Ctx, doc_id: str) -> dto.KnowledgeDocumentDetailDTO:
    require(ctx, "setup.view", "see knowledge")
    d = await _get(tx, ctx, doc_id)
    chunks = (
        (
            await tx.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.org_id == ctx.org_id, KnowledgeChunk.doc_id == d.id)
                .order_by(KnowledgeChunk.ordinal)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    names, people = await _lookups(tx, ctx.org_id, [d])
    base = await _dto(tx, ctx, d, names, people)
    return dto.KnowledgeDocumentDetailDTO(
        **base.model_dump(),
        chunks=[
            dto.KnowledgeChunkDTO(
                id=c.id, ordinal=c.ordinal, section=c.section_path, page=c.page, text=c.text_, tokens=c.tokens
            )
            for c in chunks
        ],
    )


async def upload(
    tx: AsyncSession,
    ctx: Ctx,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    title: str | None,
    department_id: str | None,
    effective_from: datetime | None,
    expires_at: datetime | None,
    replaces_id: str | None,
) -> dto.KnowledgeDocumentDTO:
    require(ctx, "setup.edit", "upload knowledge")
    if kind_of(filename, content_type) is None:
        raise unprocessable(
            "unsupported_type", "Upload a PDF, Word (.docx), Excel (.xlsx), HTML, Markdown or text file."
        )
    if len(data) > settings.knowledge_max_upload_mb * 1024 * 1024:
        raise unprocessable(
            "too_large", f"Files up to {settings.knowledge_max_upload_mb} MB can be uploaded."
        )
    if not data:
        raise unprocessable("empty_file", "The file is empty.")
    if expires_at and effective_from and expires_at <= effective_from:
        raise unprocessable("bad_dates", "The expiry date must be after the effective date.")
    checksum = hashlib.sha256(data).hexdigest()
    if (
        await tx.execute(
            select(KnowledgeDoc.id).where(
                KnowledgeDoc.org_id == ctx.org_id,
                KnowledgeDoc.checksum == checksum,
                KnowledgeDoc.status.in_(("pending", "approved")),
            )
        )
    ).first():
        raise conflict("duplicate", "This exact file is already in the knowledge base.")
    version = 1
    if (
        department_id
        and not (
            await tx.execute(
                select(Department.id).where(Department.org_id == ctx.org_id, Department.id == department_id)
            )
        ).first()
    ):
        raise not_found("Department")
    if replaces_id:
        prev = await _get(tx, ctx, replaces_id)
        version = prev.version + 1
        department_id = department_id or prev.department_id
        title = title or prev.title
    owner = ""
    if department_id:
        owner = (await tx.execute(select(Department.name).where(Department.id == department_id))).scalar_one()
    d = KnowledgeDoc(
        org_id=ctx.org_id,
        title=(title or filename.rsplit(".", 1)[0]).strip()[:200],
        section="",
        owner=owner,
        status="pending",
        department_id=department_id,
        version=version,
        replaces_id=replaces_id,
        checksum=checksum,
        filename=filename[:255],
        content_type=content_type[:100],
        size=len(data),
        parse_status="queued",
        effective_from=effective_from,
        expires_at=expires_at,
        uploaded_by=ctx.user.id,
    )
    tx.add(d)
    await tx.flush()
    import base64

    d.blob_sealed = await tenant_encrypt(tx, ctx.org_id, base64.b64encode(data).decode(), aad=_aad(d.id))
    await enqueue(tx, ctx.org_id, "knowledge_ingest", {"docId": d.id}, dedupe_key=f"knowledge-ingest:{d.id}")
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="knowledge.uploaded",
        entity="knowledge_doc",
        entity_id=d.id,
        summary=f"{ctx.user.name} uploaded {d.title} (v{version})",
        data={"filename": filename, "size": len(data), "checksum": checksum, "replaces": replaces_id},
    )
    names, people = await _lookups(tx, ctx.org_id, [d])
    return await _dto(tx, ctx, d, names, people)


async def index_blocks(
    tx: AsyncSession, org_id: str, doc: KnowledgeDoc, blocks: list[Block], *, now: datetime | None = None
) -> int:
    """Chunk and embed a document's text, replacing any previous chunks. Returns the chunk count.

    Chunk ids derive from the document and position, so re-indexing the same text yields the same ids."""
    chunks = chunk(blocks)
    vectors = (
        await embedder().embed([f"{doc.title}\n{c.section}\n{c.text}" for c in chunks]) if chunks else []
    )
    await tx.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.org_id == org_id, KnowledgeChunk.doc_id == doc.id)
    )
    for c, v in zip(chunks, vectors, strict=True):
        tx.add(
            KnowledgeChunk(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowledge-chunk:{doc.id}:{c.ordinal}")),
                created_at=now or clock.now(),
                org_id=org_id,
                doc_id=doc.id,
                ordinal=c.ordinal,
                section_path=c.section,
                page=c.page,
                text_=c.text,
                tokens=c.tokens,
                embedding=v,
            )
        )
    doc.chunk_count = len(chunks)
    doc.body = "\n\n".join(b.text for b in blocks)[:4000]
    await tx.flush()
    return len(chunks)


async def run_knowledge_ingest(job: JobRow) -> None:
    import base64

    org_id, doc_id = job.org_id, str(job.payload["docId"])
    async with tenant_tx(org_id) as tx:
        d = (
            await tx.execute(
                select(KnowledgeDoc).where(KnowledgeDoc.org_id == org_id, KnowledgeDoc.id == doc_id)
            )
        ).scalar_one_or_none()
        if d is None or d.parse_status not in ("queued", "scanning", "parsing") or not d.blob_sealed:
            return
        data = base64.b64decode(await tenant_decrypt(tx, org_id, d.blob_sealed, aad=_aad(d.id)))
        kind = kind_of(d.filename, d.content_type) or "txt"
        d.parse_status = "scanning"
    status, detail = await av.scan(data)
    if status == "infected":
        async with tenant_tx(org_id) as tx:
            await tx.execute(
                update(KnowledgeDoc)
                .where(KnowledgeDoc.id == doc_id)
                .values(
                    av_status="infected",
                    parse_status="infected",
                    parse_error=f"Virus found: {detail}",
                    blob_sealed=None,
                    status="rejected",
                )
            )
            await audit(
                tx,
                org_id,
                actor=SYSTEM_ACTOR,
                action="knowledge.infected",
                entity="knowledge_doc",
                entity_id=doc_id,
                summary=f"A virus was found in an uploaded document; it was discarded ({detail})",
                data={"signature": detail},
            )
        return
    if status == "error":
        raise RuntimeError(detail)  # retried: an unreachable scanner never lets a file through
    try:
        blocks = await parse_isolated(kind, data)
        if not blocks:
            raise ParseFailed(
                "No text was found. Scanned documents need a text layer (OCR is not available yet)."
            )
    except ParseFailed as err:
        async with tenant_tx(org_id) as tx:
            await tx.execute(
                update(KnowledgeDoc)
                .where(KnowledgeDoc.id == doc_id)
                .values(parse_status="failed", parse_error=str(err)[:500], av_status=status)
            )
        return
    async with tenant_tx(org_id) as tx:
        d = (
            await tx.execute(
                select(KnowledgeDoc)
                .where(KnowledgeDoc.org_id == org_id, KnowledgeDoc.id == doc_id)
                .with_for_update()
            )
        ).scalar_one()
        n = await index_blocks(tx, org_id, d, blocks)
        d.parse_status, d.parse_error, d.av_status = "ready", "", status
        await audit(
            tx,
            org_id,
            actor=SYSTEM_ACTOR,
            action="knowledge.parsed",
            entity="knowledge_doc",
            entity_id=doc_id,
            summary=f"{d.title} was read into {n} passages and waits for approval",
            data={"chunks": n, "av": status},
        )


async def approve(tx: AsyncSession, ctx: Ctx, doc_id: str) -> dto.KnowledgeDocumentDTO:
    d = await _get(tx, ctx, doc_id, lock=True)
    if d.department_id:
        await require_clearance(tx, ctx, d.department_id, APPROVE, "approve knowledge")
    elif ctx.role != "admin":
        require(ctx, "workspace.manage", "approve organisation-wide knowledge")
    if d.status != "pending":
        raise conflict("not_pending", f"This document is {d.status}.")
    if d.parse_status != "ready":
        raise conflict("not_ready", "The document has not been read successfully yet.")
    now = clock.now()
    d.status, d.approved_by, d.approved_at, d.verified_at = "approved", ctx.user.id, now, now
    retired = None
    if d.replaces_id:
        prev = (
            await tx.execute(
                select(KnowledgeDoc)
                .where(KnowledgeDoc.org_id == ctx.org_id, KnowledgeDoc.id == d.replaces_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if prev is not None and prev.status == "approved":
            prev.status, retired = "retired", prev.id
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="knowledge.approved",
        entity="knowledge_doc",
        entity_id=d.id,
        summary=f"{ctx.user.name} approved {d.title} v{d.version} for citation"
        + (" (replacing the previous version)" if retired else ""),
        data={"version": d.version, "retired": retired, "checksum": d.checksum},
    )
    names, people = await _lookups(tx, ctx.org_id, [d])
    return await _dto(tx, ctx, d, names, people)


async def _set_status(
    tx: AsyncSession, ctx: Ctx, doc_id: str, status: str, reason: str, allowed: tuple[str, ...]
) -> dto.KnowledgeDocumentDTO:
    d = await _get(tx, ctx, doc_id, lock=True)
    if d.department_id:
        await require_clearance(tx, ctx, d.department_id, APPROVE, f"{status.rstrip('d')} knowledge")
    elif ctx.role != "admin":
        require(ctx, "workspace.manage", "change organisation-wide knowledge")
    if d.status not in allowed:
        raise conflict("invalid_status", f"This document is {d.status}.")
    d.status = status
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action=f"knowledge.{status}",
        entity="knowledge_doc",
        entity_id=d.id,
        summary=f"{ctx.user.name} {status} {d.title} v{d.version}" + (f": {reason}" if reason else ""),
        data={"reason": reason},
    )
    names, people = await _lookups(tx, ctx.org_id, [d])
    return await _dto(tx, ctx, d, names, people)


async def reject(tx: AsyncSession, ctx: Ctx, doc_id: str, reason: str) -> dto.KnowledgeDocumentDTO:
    return await _set_status(tx, ctx, doc_id, "rejected", reason, ("pending",))


async def retire(tx: AsyncSession, ctx: Ctx, doc_id: str, reason: str) -> dto.KnowledgeDocumentDTO:
    return await _set_status(tx, ctx, doc_id, "retired", reason, ("approved", "stale"))


async def search(tx: AsyncSession, ctx: Ctx, query: str, department_id: str | None) -> dto.KnowledgeSearchDTO:
    require(ctx, "setup.view", "search knowledge")
    hits = await retrieve(tx, ctx.org_id, query, department_id=department_id, k=8)
    return dto.KnowledgeSearchDTO(
        query=query,
        hits=[
            dto.KnowledgeHitDTO(
                chunk_id=h.chunk_id,
                doc_id=h.doc_id,
                title=h.title,
                section=h.section,
                page=h.page,
                text=h.text,
                score=round(h.score, 5),
                similarity=round(h.similarity, 4),
                text_match=h.text_hit,
            )
            for h in hits
        ],
    )


async def expire_sweep() -> int:
    """Approved documents past their expiry date become stale (never cited again until renewed)."""
    from sqlalchemy import text as sql

    async with global_tx() as g:
        orgs = [
            str(r[0])
            for r in (await g.execute(sql("select id from orgs where status not in ('archived')"))).all()
        ]
    n = 0
    for org_id in orgs:
        async with tenant_tx(org_id) as tx:
            rows = (
                await tx.execute(
                    update(KnowledgeDoc)
                    .where(
                        KnowledgeDoc.org_id == org_id,
                        KnowledgeDoc.status == "approved",
                        KnowledgeDoc.expires_at <= func.now(),
                    )
                    .values(status="stale")
                    .returning(KnowledgeDoc.id, KnowledgeDoc.title)
                )
            ).all()
            for doc_id, title in rows:
                await audit(
                    tx,
                    org_id,
                    actor=SYSTEM_ACTOR,
                    action="knowledge.expired",
                    entity="knowledge_doc",
                    entity_id=doc_id,
                    summary=f"{title} passed its expiry date and is no longer cited",
                )
            n += len(rows)
    return n
