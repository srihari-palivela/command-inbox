"""Learning and team notifications."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, idempotent, in_tenant
from command_inbox.modules.learning import service
from command_inbox.modules.learning.schemas import CourseResult, Recipients
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import UUID_RE, CourseCompleteBody, MarkReadBody, NotificationBody

router = APIRouter(prefix="/v1", tags=["learning"])
IdParam = Annotated[str, Path(pattern=UUID_RE)]


@router.get("/learning", response_model=dto.LearningDTO)
async def learning(ctx: Ctx = Depends(current_ctx)) -> dto.LearningDTO:
    return await in_tenant(ctx, lambda tx: service.learning(tx, ctx))


@router.post("/notifications", response_model=Recipients)
async def send_notification(
    body: NotificationBody, request: Request, response: Response, ctx: Ctx = Depends(current_ctx)
) -> Recipients:
    async def run(tx: AsyncSession) -> Recipients:
        return Recipients(recipients=await service.send_notification(tx, ctx, body))

    return await idempotent(request, response, ctx, body.model_dump(mode="json"), run)


@router.post("/notifications/read", response_model=Ok)
async def mark_read(body: MarkReadBody, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(ctx, lambda tx: service.mark_read(tx, ctx, body.ids, bool(body.all)))
    return Ok()


@router.post("/courses/{course_id}/complete", response_model=CourseResult)
async def complete_course(
    course_id: IdParam, body: CourseCompleteBody, ctx: Ctx = Depends(current_ctx)
) -> CourseResult:
    score, total = await in_tenant(ctx, lambda tx: service.complete_course(tx, ctx, course_id, body.answers))
    return CourseResult(score=score, total=total)
