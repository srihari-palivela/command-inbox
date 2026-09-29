"""HTTP routes for knowledge documents and the retrieval playground."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from command_inbox.config import settings
from command_inbox.core.context import Ctx
from command_inbox.core.errors import unprocessable
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.knowledge import service
from command_inbox.schemas import dto
from command_inbox.schemas.requests import UUID_RE, KnowledgeReviewBody, KnowledgeSearchBody

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
DocId = Annotated[str, Path(pattern=UUID_RE)]


@router.get("/documents", response_model=list[dto.KnowledgeDocumentDTO])
async def documents(ctx: Ctx = Depends(current_ctx)) -> list[dto.KnowledgeDocumentDTO]:
    return await in_tenant(ctx, lambda tx: service.list_documents(tx, ctx))


@router.post("/documents", response_model=dto.KnowledgeDocumentDTO)
async def upload(
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=200)] = None,
    department_id: Annotated[str | None, Form(alias="departmentId", pattern=UUID_RE)] = None,
    effective_from: Annotated[datetime | None, Form(alias="effectiveFrom")] = None,
    expires_at: Annotated[datetime | None, Form(alias="expiresAt")] = None,
    replaces_id: Annotated[str | None, Form(alias="replacesId", pattern=UUID_RE)] = None,
    ctx: Ctx = Depends(current_ctx),
) -> dto.KnowledgeDocumentDTO:
    limit = settings.knowledge_max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise unprocessable(
            "too_large", f"Files up to {settings.knowledge_max_upload_mb} MB can be uploaded."
        )
    return await in_tenant(
        ctx,
        lambda tx: service.upload(
            tx,
            ctx,
            filename=file.filename or "document",
            content_type=file.content_type or "",
            data=data,
            title=title,
            department_id=department_id,
            effective_from=effective_from,
            expires_at=expires_at,
            replaces_id=replaces_id,
        ),
    )


@router.get("/documents/{id}", response_model=dto.KnowledgeDocumentDetailDTO)
async def document(id: DocId, ctx: Ctx = Depends(current_ctx)) -> dto.KnowledgeDocumentDetailDTO:
    return await in_tenant(ctx, lambda tx: service.get_document(tx, ctx, id))


@router.post("/documents/{id}/approve", response_model=dto.KnowledgeDocumentDTO)
async def approve(id: DocId, ctx: Ctx = Depends(current_ctx)) -> dto.KnowledgeDocumentDTO:
    return await in_tenant(ctx, lambda tx: service.approve(tx, ctx, id))


@router.post("/documents/{id}/reject", response_model=dto.KnowledgeDocumentDTO)
async def reject(
    id: DocId, body: KnowledgeReviewBody, ctx: Ctx = Depends(current_ctx)
) -> dto.KnowledgeDocumentDTO:
    return await in_tenant(ctx, lambda tx: service.reject(tx, ctx, id, body.reason))


@router.post("/documents/{id}/retire", response_model=dto.KnowledgeDocumentDTO)
async def retire(
    id: DocId, body: KnowledgeReviewBody, ctx: Ctx = Depends(current_ctx)
) -> dto.KnowledgeDocumentDTO:
    return await in_tenant(ctx, lambda tx: service.retire(tx, ctx, id, body.reason))


@router.post("/search", response_model=dto.KnowledgeSearchDTO)
async def search(body: KnowledgeSearchBody, ctx: Ctx = Depends(current_ctx)) -> dto.KnowledgeSearchDTO:
    return await in_tenant(ctx, lambda tx: service.search(tx, ctx, body.query, body.department_id))
