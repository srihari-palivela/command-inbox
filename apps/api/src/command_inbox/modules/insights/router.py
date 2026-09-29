"""Insights: performance, results, KPIs, alerts; plus the activity rail and the shift summary."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, idempotent, in_tenant
from command_inbox.modules.insights import monitoring, service
from command_inbox.modules.workspace.schemas import MessageOut
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import UUID_RE, KpiBody

router = APIRouter(prefix="/v1", tags=["insights"])
IdParam = Annotated[str, Path(pattern=UUID_RE)]


@router.get("/activity", response_model=list[dto.ActivityDTO])
async def activity(ctx: Ctx = Depends(current_ctx)) -> list[dto.ActivityDTO]:
    return await in_tenant(ctx, lambda tx: service.activity(tx, ctx))


@router.get("/shift", response_model=dto.ShiftDTO)
async def shift(ctx: Ctx = Depends(current_ctx)) -> dto.ShiftDTO:
    return await in_tenant(ctx, lambda tx: service.shift(tx, ctx))


@router.get("/insights/performance", response_model=dto.PerformanceDTO)
async def performance(ctx: Ctx = Depends(current_ctx)) -> dto.PerformanceDTO:
    return await in_tenant(ctx, lambda tx: service.performance(tx, ctx))


@router.get("/insights/results", response_model=dto.ResultsDTO)
async def results(ctx: Ctx = Depends(current_ctx)) -> dto.ResultsDTO:
    return await in_tenant(ctx, lambda tx: service.results(tx, ctx))


@router.post("/kpis", response_model=Ok)
async def create_kpi(
    body: KpiBody, request: Request, response: Response, ctx: Ctx = Depends(current_ctx)
) -> Ok:
    async def run(tx: AsyncSession) -> Ok:
        await service.create_kpi(tx, ctx, body)
        return Ok()

    return await idempotent(request, response, ctx, body.model_dump(mode="json"), run)


@router.delete("/kpis/{kpi_id}", response_model=Ok)
async def delete_kpi(kpi_id: IdParam, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(ctx, lambda tx: service.delete_kpi(tx, ctx, kpi_id))
    return Ok()


@router.post("/alerts/{alert_id}/{mode}", response_model=MessageOut)
async def act_on_alert(
    alert_id: IdParam, mode: Literal["act", "notify"], ctx: Ctx = Depends(current_ctx)
) -> MessageOut:
    message = await in_tenant(ctx, lambda tx: service.act_on_alert(tx, ctx, alert_id, mode))
    return MessageOut(message=message)


@router.get("/insights/monitoring", response_model=dto.MonitoringDTO)
async def monitoring_view(
    days: int = Query(7, ge=1, le=90), ctx: Ctx = Depends(current_ctx)
) -> dto.MonitoringDTO:
    return await in_tenant(ctx, lambda tx: monitoring.monitoring(tx, ctx, days))
