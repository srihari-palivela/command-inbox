"""AI setup: boards, agents, feedback, the action library and autonomy dial, rules, knowledge, taxonomy."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.context import Ctx
from command_inbox.core.http import current_ctx, idempotent, in_tenant
from command_inbox.modules.setup import agents_boards as ab
from command_inbox.modules.setup import policy_knowledge as pk
from command_inbox.modules.setup.schemas import (
    AgentsOverviewOut,
    AgentVersionCreated,
    BoardCreated,
    BoardOut,
    OwnerChanged,
    SourceConnected,
)
from command_inbox.modules.workspace.schemas import MessageOut
from command_inbox.schemas import dto
from command_inbox.schemas.base import Ok
from command_inbox.schemas.requests import (
    UUID_RE,
    ActionTemplateBody,
    AgentBoardsBody,
    AgentBody,
    AgentVersionBody,
    BoardBody,
    DecideBody,
    DialBody,
    KnowledgeSourceBody,
    OwnerBody,
    RuleToggleBody,
)

router = APIRouter(prefix="/v1", tags=["setup"])
IdParam = Annotated[str, Path(pattern=UUID_RE)]
CurrentCtx = Annotated[Ctx, Depends(current_ctx)]


def _json(body: object) -> object:
    return body.model_dump(mode="json") if hasattr(body, "model_dump") else body


# ── Boards ────────────────────────────────────────────────────────────────────
@router.get("/boards", response_model=list[BoardOut])
async def list_boards(ctx: CurrentCtx) -> list[BoardOut]:
    return await in_tenant(ctx, lambda tx: ab.list_boards(tx, ctx))


@router.post("/boards", response_model=BoardCreated)
async def create_board(
    body: BoardBody, request: Request, response: Response, ctx: CurrentCtx
) -> BoardCreated:
    async def run(tx: AsyncSession) -> BoardCreated:
        board, mailbox_id = await ab.create_board(tx, ctx, body)
        return BoardCreated(board=board, authorize_url=ab.authorize_url(ctx, body.provider, mailbox_id))

    return await idempotent(request, response, ctx, _json(body), run)


# ── Agents & feedback ─────────────────────────────────────────────────────────
@router.get("/agents", response_model=AgentsOverviewOut)
async def agents(ctx: CurrentCtx) -> AgentsOverviewOut:
    return await in_tenant(ctx, lambda tx: ab.agents_overview(tx, ctx))


@router.post("/agents", response_model=Ok)
async def create_agent(body: AgentBody, request: Request, response: Response, ctx: CurrentCtx) -> Ok:
    async def run(tx: AsyncSession) -> Ok:
        await ab.create_agent(tx, ctx, body)
        return Ok()

    return await idempotent(request, response, ctx, _json(body), run)


@router.post("/agents/{agent_id}/versions", response_model=AgentVersionCreated)
async def new_agent_version(
    agent_id: IdParam, body: AgentVersionBody, ctx: CurrentCtx
) -> AgentVersionCreated:
    version = await in_tenant(
        ctx, lambda tx: ab.new_agent_version(tx, ctx, agent_id, body.prompt, body.model)
    )
    return AgentVersionCreated(version=version)


@router.put("/agents/{agent_id}/boards", response_model=Ok)
async def set_agent_boards(agent_id: IdParam, body: AgentBoardsBody, ctx: CurrentCtx) -> Ok:
    await in_tenant(ctx, lambda tx: ab.set_agent_boards(tx, ctx, agent_id, body.board_ids))
    return Ok()


@router.post("/feedback/{feedback_id}/queue", response_model=Ok)
async def queue_tuning(feedback_id: IdParam, ctx: CurrentCtx) -> Ok:
    await in_tenant(ctx, lambda tx: ab.queue_tuning(tx, ctx, feedback_id))
    return Ok()


# ── Actions & the autonomy dial ───────────────────────────────────────────────
@router.get("/actions", response_model=dto.ActionsDTO)
async def actions(ctx: CurrentCtx) -> dto.ActionsDTO:
    return await in_tenant(ctx, lambda tx: pk.actions_overview(tx, ctx))


@router.put("/actions/dial", response_model=Ok)
async def set_dial(body: DialBody, ctx: CurrentCtx) -> Ok:
    await in_tenant(ctx, lambda tx: pk.set_dial(tx, ctx, body.cell, body.level))
    return Ok()


@router.post("/actions/templates", response_model=dto.ActionTemplateDTO)
async def create_action_template(
    body: ActionTemplateBody, request: Request, response: Response, ctx: CurrentCtx
) -> dto.ActionTemplateDTO:
    return await idempotent(
        request, response, ctx, _json(body), lambda tx: pk.create_action_template(tx, ctx, body)
    )


# ── Rules & policies ─────────────────────────────────────────────────────────
@router.get("/policies", response_model=dto.PoliciesDTO)
async def policies(ctx: CurrentCtx) -> dto.PoliciesDTO:
    return await in_tenant(ctx, lambda tx: pk.policies(tx, ctx))


@router.patch("/policies/priority-rules/{rule_id}", response_model=Ok)
async def toggle_priority_rule(rule_id: IdParam, body: RuleToggleBody, ctx: CurrentCtx) -> Ok:
    await in_tenant(ctx, lambda tx: pk.toggle_priority_rule(tx, ctx, rule_id, body.enabled))
    return Ok()


@router.post("/policies/proposed/{rule_id}/decide", response_model=Ok)
async def decide_proposed_rule(rule_id: IdParam, body: DecideBody, ctx: CurrentCtx) -> Ok:
    await in_tenant(ctx, lambda tx: pk.decide_proposed_rule(tx, ctx, rule_id, body.approve))
    return Ok()


# ── Knowledge ────────────────────────────────────────────────────────────────
@router.get("/knowledge", response_model=dto.KnowledgeDTO)
async def knowledge(ctx: CurrentCtx) -> dto.KnowledgeDTO:
    return await in_tenant(ctx, lambda tx: pk.knowledge(tx, ctx))


@router.post("/knowledge/sources", response_model=SourceConnected)
async def connect_source(
    body: KnowledgeSourceBody, request: Request, response: Response, ctx: CurrentCtx
) -> SourceConnected:
    async def run(tx: AsyncSession) -> SourceConnected:
        source_id, name = await pk.connect_source(tx, ctx, body)
        return SourceConnected(id=source_id, name=name)

    return await idempotent(request, response, ctx, _json(body), run)


@router.post("/knowledge/sources/{source_id}/sync", response_model=MessageOut)
async def sync_source(source_id: IdParam, ctx: CurrentCtx) -> MessageOut:
    return MessageOut(message=await in_tenant(ctx, lambda tx: pk.sync_source(tx, ctx, source_id)))


@router.post("/knowledge/gaps/{gap_id}/act", response_model=MessageOut)
async def act_on_gap(gap_id: IdParam, ctx: CurrentCtx) -> MessageOut:
    return MessageOut(message=await in_tenant(ctx, lambda tx: pk.act_on_gap(tx, ctx, gap_id)))


# ── Taxonomy ─────────────────────────────────────────────────────────────────
@router.get("/taxonomy", response_model=dto.TaxonomyDTO)
async def taxonomy(ctx: CurrentCtx) -> dto.TaxonomyDTO:
    return await in_tenant(ctx, lambda tx: pk.taxonomy(tx, ctx))


@router.put("/taxonomy/departments/{department_id}/owner", response_model=OwnerChanged)
async def set_department_owner(department_id: IdParam, body: OwnerBody, ctx: CurrentCtx) -> OwnerChanged:
    owner = await in_tenant(ctx, lambda tx: pk.set_department_owner(tx, ctx, department_id, body.user_id))
    return OwnerChanged(owner=owner)
