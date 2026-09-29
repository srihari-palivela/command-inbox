"""HTTP routes for teams, query types and reply-time targets."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, in_tenant
from command_inbox.modules.taxonomy import service, sla
from command_inbox.schemas import dto
from command_inbox.schemas.requests import UUID_RE, DepartmentBody, QueryTypeBody, SlaPoliciesBody

router = APIRouter(prefix="/v1/taxonomy", tags=["taxonomy"])
Id = Annotated[str, Path(pattern=UUID_RE)]


@router.get("/admin", response_model=dto.TaxonomyAdminDTO)
async def admin_view(ctx: Ctx = Depends(current_ctx)) -> dto.TaxonomyAdminDTO:
    return await in_tenant(ctx, lambda tx: service.admin_view(tx, ctx))


@router.post("/departments", response_model=dto.TaxonomyAdminDTO)
async def create_department(body: DepartmentBody, ctx: Ctx = Depends(current_ctx)) -> dto.TaxonomyAdminDTO:
    return await in_tenant(ctx, lambda tx: service.create_department(tx, ctx, body))


@router.put("/departments/{id}", response_model=dto.TaxonomyAdminDTO)
async def update_department(
    id: Id, body: DepartmentBody, ctx: Ctx = Depends(current_ctx)
) -> dto.TaxonomyAdminDTO:
    return await in_tenant(ctx, lambda tx: service.update_department(tx, ctx, id, body))


@router.delete("/departments/{id}", response_model=dto.TaxonomyAdminDTO)
async def delete_department(id: Id, ctx: Ctx = Depends(current_ctx)) -> dto.TaxonomyAdminDTO:
    return await in_tenant(ctx, lambda tx: service.delete_department(tx, ctx, id))


@router.post("/query-types", response_model=dto.TaxonomyAdminDTO)
async def create_query_type(body: QueryTypeBody, ctx: Ctx = Depends(current_ctx)) -> dto.TaxonomyAdminDTO:
    return await in_tenant(ctx, lambda tx: service.create_query_type(tx, ctx, body))


@router.put("/query-types/{id}", response_model=dto.TaxonomyAdminDTO)
async def update_query_type(
    id: Id, body: QueryTypeBody, ctx: Ctx = Depends(current_ctx)
) -> dto.TaxonomyAdminDTO:
    return await in_tenant(ctx, lambda tx: service.update_query_type(tx, ctx, id, body))


@router.delete("/query-types/{id}", response_model=dto.TaxonomyAdminDTO)
async def delete_query_type(id: Id, ctx: Ctx = Depends(current_ctx)) -> dto.TaxonomyAdminDTO:
    return await in_tenant(ctx, lambda tx: service.delete_query_type(tx, ctx, id))


@router.get("/sla-policies", response_model=dto.SlaPoliciesDTO)
async def sla_policies(ctx: Ctx = Depends(current_ctx)) -> dto.SlaPoliciesDTO:
    return await in_tenant(ctx, lambda tx: sla.get_policies(tx, ctx))


@router.put("/sla-policies", response_model=dto.SlaPoliciesDTO)
async def replace_sla_policies(body: SlaPoliciesBody, ctx: Ctx = Depends(current_ctx)) -> dto.SlaPoliciesDTO:
    return await in_tenant(ctx, lambda tx: sla.replace_policies(tx, ctx, body))
