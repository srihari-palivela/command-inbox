"""The pilot: stage, gates and sign-off, KPIs, the shadow comparison and the incident log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.pilot import service
from command_inbox.schemas import dto
from command_inbox.schemas.requests import (
    PilotBaselineBody,
    PilotDecisionBody,
    PilotIncidentBody,
    PilotResolveBody,
    PilotSettingsBody,
    PilotStageBody,
)

router = APIRouter(prefix="/v1/pilot", tags=["pilot"])


@router.get("", response_model=dto.PilotDTO)
async def get_pilot(ctx: Ctx = Depends(current_ctx)) -> dto.PilotDTO:
    return await in_tenant(ctx, lambda tx: service.get_pilot(tx, ctx))


@router.post("/requests", response_model=dto.PilotDTO)
async def request_stage(body: PilotStageBody, ctx: Ctx = Depends(current_ctx)) -> dto.PilotDTO:
    return await in_tenant(ctx, lambda tx: service.request_stage(tx, ctx, body))


@router.post("/requests/{request_id}/decision", response_model=dto.PilotDTO)
async def decide(request_id: str, body: PilotDecisionBody, ctx: Ctx = Depends(current_ctx)) -> dto.PilotDTO:
    return await in_tenant(ctx, lambda tx: service.decide(tx, ctx, request_id, body))


@router.delete("/requests/{request_id}", response_model=dto.PilotDTO)
async def withdraw(request_id: str, ctx: Ctx = Depends(current_ctx)) -> dto.PilotDTO:
    return await in_tenant(ctx, lambda tx: service.withdraw(tx, ctx, request_id))


@router.post("/step-back", response_model=dto.PilotDTO)
async def step_back(body: PilotStageBody, ctx: Ctx = Depends(current_ctx)) -> dto.PilotDTO:
    return await in_tenant(ctx, lambda tx: service.step_back(tx, ctx, body))


@router.put("/settings", response_model=dto.PilotDTO)
async def save_settings(body: PilotSettingsBody, ctx: Ctx = Depends(current_ctx)) -> dto.PilotDTO:
    return await in_tenant(ctx, lambda tx: service.save_settings(tx, ctx, body))


@router.post("/baseline", response_model=dto.PilotDTO)
async def record_baseline(body: PilotBaselineBody, ctx: Ctx = Depends(current_ctx)) -> dto.PilotDTO:
    return await in_tenant(ctx, lambda tx: service.record_baseline(tx, ctx, body))


@router.get("/shadow", response_model=dto.ShadowReportDTO)
async def shadow_report(
    days: int = Query(30, ge=1, le=180), ctx: Ctx = Depends(current_ctx)
) -> dto.ShadowReportDTO:
    return await in_tenant(ctx, lambda tx: service.shadow_report(tx, ctx, days))


@router.get("/incidents", response_model=list[dto.PilotIncidentDTO])
async def incidents(ctx: Ctx = Depends(current_ctx)) -> list[dto.PilotIncidentDTO]:
    return await in_tenant(ctx, lambda tx: service.incidents(tx, ctx))


@router.post("/incidents", response_model=list[dto.PilotIncidentDTO])
async def log_incident(
    body: PilotIncidentBody, ctx: Ctx = Depends(current_ctx)
) -> list[dto.PilotIncidentDTO]:
    return await in_tenant(ctx, lambda tx: service.log_incident(tx, ctx, body))


@router.post("/incidents/{incident_id}/resolve", response_model=list[dto.PilotIncidentDTO])
async def resolve_incident(
    incident_id: str, body: PilotResolveBody, ctx: Ctx = Depends(current_ctx)
) -> list[dto.PilotIncidentDTO]:
    return await in_tenant(ctx, lambda tx: service.resolve_incident(tx, ctx, incident_id, body))
