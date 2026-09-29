"""Boards (a connected mailbox and its queue), AI agents, their versions, and tuning feedback."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import conflict, not_found
from command_inbox.core.outbox import publish
from command_inbox.db.engine import rows
from command_inbox.db.models import (
    Agent,
    AgentBoard,
    AgentEval,
    AgentVersion,
    Board,
    DailyMetric,
    Department,
    Feedback,
    Mailbox,
)
from command_inbox.modules.setup.schemas import AgentOut, AgentsOverviewOut, BoardOut
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import AgentBody, BoardBody

ROLE_OF = {
    "bucketing": "bucketer",
    "extraction": "extractor",
    "drafting": "drafter",
    "summarisation": "summariser",
    "policy_guard": "guard",
}


@cache
def catalog() -> dict[str, Any]:
    """Agent templates and the model menu are deployment data, not code."""
    return json.loads((Path(__file__).parent / "agent_catalog.json").read_text(encoding="utf-8"))


def _cost_per_1k(model: str) -> int:
    for m in catalog()["models"]:
        if m["family"] in model:
            return int(m["costPer1kMinor"])
    return int(catalog()["defaultCostPer1kMinor"])


def initials_of(name: str) -> str:
    """Same as the contracts package: letters of each word, at most two."""
    parts = [p for p in re.split(r"[\s.]+", re.sub(r"[^A-Za-z .]", "", name)) if p]
    return "".join(p[0].upper() for p in parts)[:2]


def _base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


# ── Boards ────────────────────────────────────────────────────────────────────
async def list_boards(tx: AsyncSession, ctx: Ctx) -> list[BoardOut]:
    boards = (
        (
            await tx.execute(
                select(Board).where(Board.org_id == ctx.org_id).order_by(Board.sort, Board.created_at)
            )
        )
        .scalars()
        .all()
    )
    mailboxes = {
        m.id: m for m in (await tx.execute(select(Mailbox).where(Mailbox.org_id == ctx.org_id))).scalars()
    }
    links = (
        await tx.execute(
            select(AgentBoard.board_id, Agent.id, Agent.name)
            .join(Agent, Agent.id == AgentBoard.agent_id)
            .where(AgentBoard.org_id == ctx.org_id)
            .order_by(Agent.sort)
        )
    ).all()
    open_by = {
        r["board_id"]: r["n"]
        for r in await rows(
            tx,
            """select board_id::text as board_id, count(*)::int n from tickets
                where org_id = :org and status not in ('resolved','closed') group by board_id""",
            {"org": ctx.org_id},
        )
    }
    out = []
    for b in boards:
        mb = mailboxes.get(b.mailbox_id) if b.mailbox_id else None
        out.append(
            BoardOut(
                id=b.id,
                key=b.key,
                name=b.name,
                source=mb.address if mb else "",
                provider=mb.provider if mb else "dev",
                volume24h=mb.volume_24h if mb else 0,
                open=open_by.get(b.id, 0),
                auto_rate_pct=b.auto_rate_pct,
                state=b.state,
                team=b.team,
                agents=[dto.BoardDTOAgents(id=aid, name=name) for bid, aid, name in links if bid == b.id],
            )
        )
    return out


async def create_board(tx: AsyncSession, ctx: Ctx, body: BoardBody) -> tuple[BoardOut, str]:
    """A board from a newly connected mailbox. It starts read-only in observe mode: the AI shadows the queue
    and scores nothing until an admin promotes the board."""
    require(ctx, "setup.edit", "create a board")
    dup = (
        await tx.execute(
            select(Mailbox.id).where(Mailbox.org_id == ctx.org_id, Mailbox.address == body.mailbox)
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise conflict("mailbox_exists", f"{body.mailbox} is already connected.")
    if body.department_id is not None:
        dept = (
            await tx.execute(
                select(Department.id).where(
                    Department.org_id == ctx.org_id, Department.id == body.department_id
                )
            )
        ).scalar_one_or_none()
        if dept is None:
            raise not_found("Team")
    mb = Mailbox(
        org_id=ctx.org_id,
        address=body.mailbox,
        provider=body.provider,
        department_id=body.department_id,
        team_label="",
        permissions=["read"],
        state="observe",
        volume_24h=0,
        sort=100,
    )
    try:
        # Addresses are unique across tenants; another workspace's mailbox is invisible here (RLS), so
        # the unique index is the check. A savepoint keeps the transaction usable to report it.
        async with tx.begin_nested():
            tx.add(mb)
            await tx.flush()
    except IntegrityError as err:
        raise conflict("mailbox_exists", f"{body.mailbox} is already connected.") from err
    slug = re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", body.name.lower()))[:24] or "board"
    stamp = _base36(int(clock.now().timestamp() * 1000))[-4:]
    board = Board(
        org_id=ctx.org_id,
        key=f"{slug}-{stamp}",
        name=body.name,
        mailbox_id=mb.id,
        team="Not staffed yet",
        state="observe",
        sort=100,
    )
    tx.add(board)
    await tx.flush()
    guard = (
        await tx.execute(select(Agent.id).where(Agent.org_id == ctx.org_id, Agent.role == "guard").limit(1))
    ).scalar_one_or_none()
    if guard is not None:
        tx.add(AgentBoard(org_id=ctx.org_id, agent_id=guard, board_id=board.id))
        await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="board.created",
        entity="board",
        entity_id=board.id,
        summary=f"{body.name} created · reading {body.mailbox} over {body.provider}",
        data={"provider": body.provider},
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "boards"})
    created = next(b for b in await list_boards(tx, ctx) if b.id == board.id)
    return created, mb.id


def authorize_url(ctx: Ctx, provider: str, mailbox_id: str) -> str | None:
    """With mailbox OAuth configured, the browser is sent to the provider to grant read access."""
    if provider not in ("microsoft", "google"):
        return None
    from command_inbox.modules.intake import oauth  # the mailbox OAuth flow lives with intake

    return oauth.authorize_url(ctx, provider, mailbox_id) if oauth.is_configured(provider) else None


# ── Agents ────────────────────────────────────────────────────────────────────
async def _calibration(tx: AsyncSession, org_id: str) -> dict[str, list[dto.CalibrationBandDTO]]:
    found = await rows(
        tx,
        """
        select agent_name, least(floor(confidence * 10), 9)::int as band, count(*)::int n,
               avg(case when correct then 1.0 when correct = false then 0.0 end)::float as observed
          from prediction_outcomes
         where org_id = :org and correct is not null
         group by agent_name, band
         order by agent_name, band""",
        {"org": org_id},
    )
    out: dict[str, list[dto.CalibrationBandDTO]] = {}
    for r in found:
        band = r["band"]
        if band < 4:
            continue
        out.setdefault(r["agent_name"], []).append(
            dto.CalibrationBandDTO(
                band=f"{band / 10:.1f}–{(band + 1) / 10:.1f}",
                predicted=(band + 0.5) / 10,
                observed=r["observed"],
                n=r["n"],
            )
        )
    return out


async def agents_overview(tx: AsyncSession, ctx: Ctx) -> AgentsOverviewOut:
    require(ctx, "setup.view", "view AI agents")
    agents = (
        (
            await tx.execute(
                select(Agent).where(Agent.org_id == ctx.org_id).order_by(Agent.sort, Agent.created_at)
            )
        )
        .scalars()
        .all()
    )
    ids = [a.id for a in agents]
    boards: list[Any] = []
    evals: list[AgentEval] = []
    versions: list[AgentVersion] = []
    if ids:
        boards = list(
            (
                await tx.execute(
                    select(AgentBoard.agent_id, Board.id, Board.name)
                    .join(Board, Board.id == AgentBoard.board_id)
                    .where(AgentBoard.org_id == ctx.org_id, AgentBoard.agent_id.in_(ids))
                    .order_by(Board.sort)
                )
            ).all()
        )
        evals = list(
            (
                await tx.execute(
                    select(AgentEval)
                    .where(AgentEval.org_id == ctx.org_id, AgentEval.agent_id.in_(ids))
                    .order_by(AgentEval.sort)
                )
            )
            .scalars()
            .all()
        )
        versions = list(
            (
                await tx.execute(
                    select(AgentVersion)
                    .where(AgentVersion.org_id == ctx.org_id, AgentVersion.agent_id.in_(ids))
                    .order_by(AgentVersion.version.desc())
                )
            )
            .scalars()
            .all()
        )
    calib = await _calibration(tx, ctx.org_id)
    now = clock.now()
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    [spend] = await rows(
        tx,
        """select coalesce(sum(cost_minor), 0)::int as minor from triage_runs
            where org_id = :org and created_at >= :since""",
        {"org": ctx.org_id, "since": month_start},
    )
    feedback = (
        (
            await tx.execute(
                select(Feedback)
                .where(Feedback.org_id == ctx.org_id)
                .order_by(Feedback.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    # Spend recorded by billing before this deployment's own runs (imported monthly), plus live model spend.
    imported = (
        await tx.execute(
            select(DailyMetric.value)
            .where(DailyMetric.org_id == ctx.org_id, DailyMetric.metric == "spend.imported_minor")
            .order_by(DailyMetric.day.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return AgentsOverviewOut(
        agents=[
            AgentOut(
                id=a.id,
                name=a.name,
                abbr=a.abbr,
                template=a.template,
                model=a.model,
                state=a.state,
                version=a.version,
                prompt=a.prompt,
                eval_score=a.eval_score,
                cost_per1k_minor=a.cost_per_1k_minor,
                boards=[dto.AgentDTOBoards(id=bid, name=name) for aid, bid, name in boards if aid == a.id],
                evals=[
                    dto.AgentEvalDTO(label=e.label, value=e.value, tone=e.tone)
                    for e in evals
                    if e.agent_id == a.id
                ],
                calibration=calib.get(a.name, []),
                versions=[
                    dto.AgentDTOVersions(
                        version=v.version,
                        model=v.model,
                        created_at=iso_ms(v.created_at),
                        created_by=v.created_by,
                        eval_status=v.eval_status,
                    )
                    for v in versions
                    if v.agent_id == a.id
                ],
            )
            for a in agents
        ],
        spend_month_minor=int((imported or 0) + 0.5) + spend["minor"],
        feedback=[
            dto.FeedbackDTO(
                id=f.id,
                agent_name=f.agent_name,
                ticket_number=f.ticket_number,
                kind=f.kind,
                text=f.text_,
                fix=f.fix,
                status=f.status,
                at=iso_ms(f.created_at),
            )
            for f in feedback
        ],
        templates=[dto.AgentTemplateDTO.model_validate(t) for t in catalog()["templates"]],
        models=[dto.AgentsOverviewDTOModels(id=m["id"], note=m["note"]) for m in catalog()["models"]],
    )


async def _owned_boards(tx: AsyncSession, ctx: Ctx, board_ids: set[str]) -> list[Board]:
    found = (
        (await tx.execute(select(Board).where(Board.org_id == ctx.org_id, Board.id.in_(board_ids))))
        .scalars()
        .all()
    )
    if len(found) != len(board_ids):
        raise not_found("Board")
    return list(found)


async def create_agent(tx: AsyncSession, ctx: Ctx, body: AgentBody) -> None:
    require(ctx, "setup.edit", "add an agent")
    dup = (
        await tx.execute(select(Agent.id).where(Agent.org_id == ctx.org_id, Agent.name == body.name))
    ).scalar_one_or_none()
    if dup is not None:
        raise conflict("agent_exists", f"An agent called {body.name} already exists.")
    boards = await _owned_boards(tx, ctx, set(body.board_ids))
    boards.sort(key=lambda b: body.board_ids.index(b.id))
    agent = Agent(
        org_id=ctx.org_id,
        name=body.name,
        abbr=initials_of(body.name) or "NA",
        template=body.template,
        model=body.model,
        state="observe",
        version=1,
        prompt=body.prompt,
        eval_score=None,
        cost_per_1k_minor=_cost_per_1k(body.model),
        role=ROLE_OF[body.template],
        sort=100,
    )
    tx.add(agent)
    await tx.flush()
    tx.add_all(
        [
            AgentVersion(
                org_id=ctx.org_id,
                agent_id=agent.id,
                version=1,
                prompt=body.prompt,
                model=body.model,
                eval_status="queued",
                created_by=ctx.user.name,
            ),
            *[AgentBoard(org_id=ctx.org_id, agent_id=agent.id, board_id=b.id) for b in boards],
            AgentEval(
                org_id=ctx.org_id,
                agent_id=agent.id,
                label="Golden set run",
                value="queued",
                tone="neutral",
                sort=0,
            ),
            AgentEval(
                org_id=ctx.org_id,
                agent_id=agent.id,
                label="Live traffic",
                value="observe only",
                tone="neutral",
                sort=1,
            ),
        ]
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="agent.created",
        entity="agent",
        entity_id=agent.id,
        summary=f"{body.name} created in observe mode ({body.model}) on {', '.join(b.name for b in boards)}",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "agents"})


async def _agent(tx: AsyncSession, ctx: Ctx, agent_id: str) -> Agent:
    a = (
        await tx.execute(select(Agent).where(Agent.org_id == ctx.org_id, Agent.id == agent_id))
    ).scalar_one_or_none()
    if a is None:
        raise not_found("Agent")
    return a


async def new_agent_version(tx: AsyncSession, ctx: Ctx, agent_id: str, prompt: str, model: str | None) -> int:
    """A prompt or model change ships as a new version pending evals; the live version keeps running."""
    require(ctx, "setup.edit", "edit an agent prompt")
    a = await _agent(tx, ctx, agent_id)
    latest = (
        await tx.execute(
            select(func.max(AgentVersion.version)).where(
                AgentVersion.org_id == ctx.org_id, AgentVersion.agent_id == agent_id
            )
        )
    ).scalar_one_or_none()
    version = max(latest if latest is not None else a.version, a.version) + 1
    tx.add(
        AgentVersion(
            org_id=ctx.org_id,
            agent_id=agent_id,
            version=version,
            prompt=prompt,
            model=model or a.model,
            eval_status="pending evals",
            created_by=ctx.user.name,
        )
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="agent.version_created",
        entity="agent",
        entity_id=agent_id,
        summary=f"{a.name} v{version} created — pending golden-set evals",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "agents"})
    return version


async def set_agent_boards(tx: AsyncSession, ctx: Ctx, agent_id: str, board_ids: list[str]) -> None:
    require(ctx, "setup.edit", "attach an agent to a board")
    a = await _agent(tx, ctx, agent_id)
    wanted = set(board_ids)
    if wanted:
        await _owned_boards(tx, ctx, wanted)
    await tx.execute(
        delete(AgentBoard).where(AgentBoard.org_id == ctx.org_id, AgentBoard.agent_id == agent_id)
    )
    tx.add_all([AgentBoard(org_id=ctx.org_id, agent_id=agent_id, board_id=b) for b in wanted])
    await tx.flush()
    n = len(wanted)
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="agent.boards_changed",
        entity="agent",
        entity_id=agent_id,
        summary=f"{a.name} now on {n} board{'' if n == 1 else 's'}",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "agents"})


async def queue_tuning(tx: AsyncSession, ctx: Ctx, feedback_id: str) -> None:
    require(ctx, "insights.view", "queue agent tuning")
    f = (
        await tx.execute(
            update(Feedback)
            .where(Feedback.org_id == ctx.org_id, Feedback.id == feedback_id)
            .values(status="queued")
            .returning(Feedback)
        )
    ).scalar_one_or_none()
    if f is None:
        raise not_found("Feedback")
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="feedback.queued",
        entity="feedback",
        entity_id=f.id,
        summary=f"{'Prompt' if f.fix == 'prompt' else 'Context'} tuning queued for {f.agent_name}; "
        "case added to the golden set",
    )
    await publish(tx, ctx.org_id, "setup.updated", {"area": "feedback"})
