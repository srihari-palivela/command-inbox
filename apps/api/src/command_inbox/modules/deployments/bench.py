"""The test bench: run a pasted mail through a version (and, side by side, another) without saving anything.

It runs the real flow with the workspace's real policy, knowledge and model providers, so what it shows is
what triage would do. Nothing is written: no ticket, draft, gap or trace. Model spend is real, so it is
recorded against the monthly budget like any other run, and each run is audited (without the mail text).
Personal data is masked before any model sees it and stays masked in what the bench returns.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.budget import record_spend
from command_inbox.agents.config import DeploymentConfig
from command_inbox.agents.decision import ResilientEngine, engine_from_settings
from command_inbox.agents.flow import RunDeps, compile_flow, run_flow
from command_inbox.agents.runner import Workspace, _retriever, build_categories, load_workspace
from command_inbox.agents.specs import agent_specs
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import not_found
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import Deployment, DeploymentVersion
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import BenchBody


async def _versions(
    tx: AsyncSession, ctx: Ctx, deployment_id: str, version_id: str, compare: str | None
) -> list[DeploymentVersion]:
    d = (
        await tx.execute(
            select(Deployment).where(Deployment.org_id == ctx.org_id, Deployment.id == deployment_id)
        )
    ).scalar_one_or_none()
    if d is None:
        raise not_found("Deployment")
    ids = [version_id]
    if compare == "active":
        if d.active_version_id and d.active_version_id != version_id:
            ids.append(d.active_version_id)
    elif compare and compare != version_id:
        ids.append(compare)
    rows = {
        v.id: v
        for v in (
            await tx.execute(
                select(DeploymentVersion).where(
                    DeploymentVersion.org_id == ctx.org_id,
                    DeploymentVersion.deployment_id == d.id,
                    DeploymentVersion.id.in_(ids),
                )
            )
        ).scalars()
    }
    if version_id not in rows or len(rows) != len(ids):
        raise not_found("Deployment version")
    return [rows[i] for i in ids]


async def run_bench(ctx: Ctx, deployment_id: str, version_id: str, body: BenchBody) -> dto.BenchResultDTO:
    require(ctx, "deployment.edit", "use the test bench")
    async with tenant_tx(ctx.org_id) as tx:
        versions = await _versions(tx, ctx, deployment_id, version_id, body.compare_with)
        w = await load_workspace(tx, ctx.org_id)
    runs = [await _run(ctx, w, v, body) for v in versions]
    async with tenant_tx(ctx.org_id) as tx:
        await audit(
            tx,
            ctx.org_id,
            actor=actor_of(ctx),
            action="deployment.bench_run",
            entity="deployment_version",
            entity_id=version_id,
            summary=f"{ctx.user.name} ran a test mail through " + " and ".join(f"v{r.version}" for r in runs),
            data={
                "versions": [r.version_id for r in runs],
                "lanes": [r.lane for r in runs],
                "costMinor": sum(r.cost_minor for r in runs),
            },
        )
    return dto.BenchResultDTO(runs=runs)


async def _run(ctx: Ctx, w: Workspace, v: DeploymentVersion, body: BenchBody) -> dto.BenchRunDTO:
    config = DeploymentConfig.model_validate(v.config)
    now = clock.now()
    deps = RunDeps(
        config=config,
        deployment=f"bench@v{v.version}",
        engine=ResilientEngine(engine_from_settings()),
        providers=w.router(config),
        ticket_id="bench",
        org_id=ctx.org_id,
        subject=body.subject,
        messages=[("Customer", body.body)],
        sender_email=body.from_email or "customer@example.invalid",
        customer_name="the customer",
        customer_facts=None,
        segment=body.segment,
        ticket_priority="P3",
        received_at=now,
        now=now,
        prior_contacts=0,
        prior_same_topic=0,
        categories=build_categories(config, w.depts, w.qts, {}),
        templates=w.templates,
        dial=w.dial,
        docs=w.docs,
        agents=agent_specs(config, w.catalogue),
        sender_verified=True,
        retrieve=_retriever(ctx.org_id),
        sla_rules=w.sla_rules,
    )
    started = time.perf_counter()
    state: dict[str, Any] = dict(await run_flow(compile_flow(config, v.id), deps))
    elapsed = int((time.perf_counter() - started) * 1000)
    await record_spend(ctx.org_id, deps.providers)

    meta = deps.meta(state.get("category"))
    draft = state.get("draft")
    grounding = list(state.get("grounding") or [])
    brief = state.get("brief")
    extraction = state.get("extraction")
    spans = list(state.get("spans") or [])
    guard = state.get("guard") or {}
    return dto.BenchRunDTO(
        version_id=v.id,
        version=v.version,
        state=v.state,  # type: ignore[arg-type]
        lane=state.get("lane") or "manual",  # type: ignore[arg-type]
        lane_note=state.get("lane_note") or "",
        category=meta.bucket if meta else None,
        confidence=round(float(state.get("confidence", 0.0)), 4),
        hard_stop=guard.get("stop"),
        draft=dto.BenchRunDTODraft(
            body=draft.body,
            coverage=draft.coverage,
            citations=[
                dto.BenchRunDTODraftCitations(n=n, title=g.title, section=g.section)
                for n, g in enumerate(grounding, start=1)
                if n in draft.citations
            ],
            flagged=list(draft.flagged),
        )
        if draft is not None
        else None,
        brief=dto.BenchRunDTOBrief(summary=brief.summary) if brief is not None else None,
        fields=[
            dto.BenchRunDTOFields(label=f.label, value=f.value, inferred=f.inferred)
            for f in extraction.fields
        ]
        if extraction is not None
        else [],
        spans=[
            dto.BenchSpanDTO(
                agent=s["agent"],
                model=s["model"],
                action=s["action"],
                output=s["output"],
                latency_ms=s["latency_ms"],
                tokens=s["tokens"],
                cost_minor=s["cost_minor"],
                status=s["status"],  # type: ignore[arg-type]
            )
            for s in spans
        ],
        cost_minor=sum(s["cost_minor"] or 0 for s in spans),
        latency_ms=elapsed,
        degraded=list(deps.providers.reasons)
        + (["decision engine unavailable"] if deps.engine.degraded else []),
    )
