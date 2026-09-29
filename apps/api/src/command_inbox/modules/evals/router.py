"""HTTP routes for evals: `/v1/evals/...`."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response

from command_inbox.core.context import Ctx
from command_inbox.core.errors import not_found
from command_inbox.core.http import current_ctx, idempotent, in_tenant
from command_inbox.modules.evals import service
from command_inbox.modules.evals.schemas import (
    EvalCaseBody,
    EvalCasesBody,
    EvalDatasetBody,
    EvalDatasetPatchBody,
    StartEvalRunBody,
)
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok

router = APIRouter(prefix="/v1/evals", tags=["evals"])


def _id(value: str | None, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise not_found(what) from None


def _opt(value: str | None, what: str) -> str | None:
    return _id(value, what) if value else None


@router.get("/datasets", response_model=list[dto.EvalDatasetDTO])
async def list_datasets(
    deployment_id: str | None = Query(None, alias="deploymentId"), ctx: Ctx = Depends(current_ctx)
) -> list[dto.EvalDatasetDTO]:
    dep = _opt(deployment_id, "Deployment")
    return await in_tenant(ctx, lambda tx: service.list_datasets(tx, ctx, dep))


@router.post("/datasets", response_model=dto.EvalDatasetDTO)
async def create_dataset(body: EvalDatasetBody, ctx: Ctx = Depends(current_ctx)) -> dto.EvalDatasetDTO:
    return await in_tenant(ctx, lambda tx: service.create_dataset(tx, ctx, body))


@router.get("/datasets/{dataset_id}", response_model=dto.EvalDatasetDTO)
async def get_dataset(dataset_id: str, ctx: Ctx = Depends(current_ctx)) -> dto.EvalDatasetDTO:
    return await in_tenant(ctx, lambda tx: service.get_dataset(tx, ctx, _id(dataset_id, "Eval dataset")))


@router.patch("/datasets/{dataset_id}", response_model=dto.EvalDatasetDTO)
async def update_dataset(
    dataset_id: str, body: EvalDatasetPatchBody, ctx: Ctx = Depends(current_ctx)
) -> dto.EvalDatasetDTO:
    return await in_tenant(
        ctx, lambda tx: service.update_dataset(tx, ctx, _id(dataset_id, "Eval dataset"), body)
    )


@router.delete("/datasets/{dataset_id}", response_model=Ok)
async def delete_dataset(dataset_id: str, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(ctx, lambda tx: service.delete_dataset(tx, ctx, _id(dataset_id, "Eval dataset")))
    return Ok()


@router.get("/datasets/{dataset_id}/cases", response_model=list[dto.EvalCaseDTO])
async def list_cases(dataset_id: str, ctx: Ctx = Depends(current_ctx)) -> list[dto.EvalCaseDTO]:
    return await in_tenant(ctx, lambda tx: service.list_cases(tx, ctx, _id(dataset_id, "Eval dataset")))


@router.post("/datasets/{dataset_id}/cases", response_model=list[dto.EvalCaseDTO])
async def add_cases(
    dataset_id: str, body: EvalCasesBody, ctx: Ctx = Depends(current_ctx)
) -> list[dto.EvalCaseDTO]:
    return await in_tenant(ctx, lambda tx: service.add_cases(tx, ctx, _id(dataset_id, "Eval dataset"), body))


@router.put("/cases/{case_id}", response_model=dto.EvalCaseDTO)
async def update_case(case_id: str, body: EvalCaseBody, ctx: Ctx = Depends(current_ctx)) -> dto.EvalCaseDTO:
    return await in_tenant(ctx, lambda tx: service.update_case(tx, ctx, _id(case_id, "Eval case"), body))


@router.delete("/cases/{case_id}", response_model=Ok)
async def archive_case(case_id: str, ctx: Ctx = Depends(current_ctx)) -> Ok:
    await in_tenant(ctx, lambda tx: service.archive_case(tx, ctx, _id(case_id, "Eval case")))
    return Ok()


@router.get("/runs", response_model=list[dto.EvalRunDTO])
async def list_runs(
    deployment_id: str | None = Query(None, alias="deploymentId"),
    version_id: str | None = Query(None, alias="versionId"),
    ctx: Ctx = Depends(current_ctx),
) -> list[dto.EvalRunDTO]:
    dep, ver = _opt(deployment_id, "Deployment"), _opt(version_id, "Deployment version")
    return await in_tenant(ctx, lambda tx: service.list_runs(tx, ctx, dep, ver))


@router.post("/runs", response_model=dto.EvalRunDTO)
async def start_run(
    body: StartEvalRunBody, request: Request, response: Response, ctx: Ctx = Depends(current_ctx)
) -> dto.EvalRunDTO:
    return await idempotent(
        request, response, ctx, body.model_dump(mode="json"), lambda tx: service.start_run(tx, ctx, body)
    )


@router.get("/runs/{run_id}", response_model=dto.EvalRunDTO)
async def get_run(run_id: str, ctx: Ctx = Depends(current_ctx)) -> dto.EvalRunDTO:
    return await in_tenant(ctx, lambda tx: service.get_run(tx, ctx, _id(run_id, "Eval run")))


@router.get("/runs/{run_id}/results", response_model=list[dto.EvalResultDTO])
async def run_results(run_id: str, ctx: Ctx = Depends(current_ctx)) -> list[dto.EvalResultDTO]:
    return await in_tenant(ctx, lambda tx: service.run_results(tx, ctx, _id(run_id, "Eval run")))
