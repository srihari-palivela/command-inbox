"""Eval use cases: datasets and cases (evals.view to read, evals.run to change), and starting runs.

Cases are archived rather than deleted so a finished run keeps its evidence; the dataset snapshot hash
covers only the live (non-archived) cases, so archiving or editing a case changes the snapshot.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.config import DeploymentConfig
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import conflict, not_found
from command_inbox.core.jobs import enqueue
from command_inbox.core.outbox import publish
from command_inbox.db.models import Deployment, DeploymentVersion, EvalCase, EvalDataset, EvalResult, EvalRun
from command_inbox.evals.runner import dataset_snapshot, load_cases
from command_inbox.modules.deployments.service import user_refs
from command_inbox.modules.evals.schemas import (
    EvalCaseBody,
    EvalCasesBody,
    EvalDatasetBody,
    EvalDatasetPatchBody,
    StartEvalRunBody,
)
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto

# ── Datasets ──────────────────────────────────────────────────────────────────────────────────────────


async def _dataset(tx: AsyncSession, ctx: Ctx, dataset_id: str) -> EvalDataset:
    d = (
        await tx.execute(
            select(EvalDataset).where(EvalDataset.org_id == ctx.org_id, EvalDataset.id == dataset_id)
        )
    ).scalar_one_or_none()
    if d is None:
        raise not_found("Eval dataset")
    return d


async def _dataset_dto(tx: AsyncSession, d: EvalDataset) -> dto.EvalDatasetDTO:
    cases = await load_cases(tx, d.org_id, d.id)
    return dto.EvalDatasetDTO(
        id=d.id,
        deployment_id=d.deployment_id,
        name=d.name,
        description=d.description,
        cases=len(cases),
        splits=dto.EvalDatasetDTOSplits(
            calibration=sum(c.split == "calibration" for c in cases),
            test=sum(c.split == "test" for c in cases),
        ),
        snapshot=dataset_snapshot(cases),
        created_at=iso_ms(d.created_at),
    )


async def list_datasets(tx: AsyncSession, ctx: Ctx, deployment_id: str | None) -> list[dto.EvalDatasetDTO]:
    require(ctx, "evals.view", "see eval results")
    q = select(EvalDataset).where(EvalDataset.org_id == ctx.org_id)
    if deployment_id:
        q = q.where(EvalDataset.deployment_id == deployment_id)
    rows = (await tx.execute(q.order_by(EvalDataset.created_at))).scalars().all()
    return [await _dataset_dto(tx, d) for d in rows]


async def get_dataset(tx: AsyncSession, ctx: Ctx, dataset_id: str) -> dto.EvalDatasetDTO:
    require(ctx, "evals.view", "see eval results")
    return await _dataset_dto(tx, await _dataset(tx, ctx, dataset_id))


async def create_dataset(tx: AsyncSession, ctx: Ctx, body: EvalDatasetBody) -> dto.EvalDatasetDTO:
    require(ctx, "evals.run", "manage eval datasets")
    dep = (
        await tx.execute(
            select(Deployment).where(Deployment.org_id == ctx.org_id, Deployment.id == body.deployment_id)
        )
    ).scalar_one_or_none()
    if dep is None:
        raise not_found("Deployment")
    taken = (
        await tx.execute(
            select(EvalDataset.id).where(
                EvalDataset.org_id == ctx.org_id,
                EvalDataset.deployment_id == dep.id,
                func.lower(EvalDataset.name) == body.name.lower(),
            )
        )
    ).first()
    if taken:
        raise conflict("name_taken", f"This deployment already has a dataset called {body.name!r}.")
    d = EvalDataset(org_id=ctx.org_id, deployment_id=dep.id, name=body.name, description=body.description)
    tx.add(d)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.dataset_created",
        entity="eval_dataset",
        entity_id=d.id,
        summary=f"{ctx.user.name} created the eval dataset {body.name} for {dep.name}",
        data={"deploymentId": dep.id},
    )
    await publish(tx, ctx.org_id, "eval.updated", {"datasetId": d.id})
    await tx.refresh(d)
    return await _dataset_dto(tx, d)


async def update_dataset(
    tx: AsyncSession, ctx: Ctx, dataset_id: str, body: EvalDatasetPatchBody
) -> dto.EvalDatasetDTO:
    require(ctx, "evals.run", "manage eval datasets")
    d = await _dataset(tx, ctx, dataset_id)
    if body.name is not None:
        d.name = body.name
    if body.description is not None:
        d.description = body.description
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.dataset_updated",
        entity="eval_dataset",
        entity_id=d.id,
        summary=f"{ctx.user.name} updated the eval dataset {d.name}",
    )
    await publish(tx, ctx.org_id, "eval.updated", {"datasetId": d.id})
    return await _dataset_dto(tx, d)


async def delete_dataset(tx: AsyncSession, ctx: Ctx, dataset_id: str) -> None:
    require(ctx, "evals.run", "manage eval datasets")
    d = await _dataset(tx, ctx, dataset_id)
    used = (
        await tx.execute(
            select(EvalRun.id).where(EvalRun.org_id == ctx.org_id, EvalRun.dataset_id == d.id).limit(1)
        )
    ).first()
    if used:
        raise conflict(
            "dataset_in_use", "Eval runs were scored on this dataset, so it is kept as their evidence."
        )
    await tx.delete(d)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.dataset_deleted",
        entity="eval_dataset",
        entity_id=dataset_id,
        summary=f"{ctx.user.name} deleted the eval dataset {d.name}",
    )
    await publish(tx, ctx.org_id, "eval.updated", {"datasetId": dataset_id})


# ── Cases ─────────────────────────────────────────────────────────────────────────────────────────────


def _case_dto(row: EvalCase) -> dto.EvalCaseDTO:
    i, e = row.input or {}, row.expected or {}
    return dto.EvalCaseDTO(
        id=row.id,
        dataset_id=row.dataset_id,
        input=dto.EvalCaseDTOInput(
            subject=str(i.get("subject", "")), body=str(i.get("body", "")), from_email=i.get("fromEmail")
        ),
        expected=dto.EvalCaseDTOExpected(
            category=str(e.get("category", "")), hard_stop=bool(e.get("hardStop"))
        ),
        split=row.split,  # type: ignore[arg-type]
        tags=list(row.tags or []),
        source=row.source,
        created_at=iso_ms(row.created_at),
    )


def _case_fields(body: EvalCaseBody) -> dict[str, Any]:
    return {
        "input": body.input.model_dump(mode="json", by_alias=True),
        "expected": body.expected.model_dump(mode="json", by_alias=True),
        "split": body.split,
        "tags": body.tags,
    }


async def list_cases(tx: AsyncSession, ctx: Ctx, dataset_id: str) -> list[dto.EvalCaseDTO]:
    require(ctx, "evals.view", "see eval results")
    d = await _dataset(tx, ctx, dataset_id)
    rows = (
        (
            await tx.execute(
                select(EvalCase)
                .where(
                    EvalCase.org_id == ctx.org_id, EvalCase.dataset_id == d.id, EvalCase.archived_at.is_(None)
                )
                .order_by(EvalCase.created_at, EvalCase.id)
            )
        )
        .scalars()
        .all()
    )
    return [_case_dto(r) for r in rows]


async def add_cases(
    tx: AsyncSession, ctx: Ctx, dataset_id: str, body: EvalCasesBody
) -> list[dto.EvalCaseDTO]:
    require(ctx, "evals.run", "manage eval datasets")
    d = await _dataset(tx, ctx, dataset_id)
    rows = [
        EvalCase(org_id=ctx.org_id, dataset_id=d.id, source="manual", **_case_fields(c)) for c in body.cases
    ]
    tx.add_all(rows)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.cases_added",
        entity="eval_dataset",
        entity_id=d.id,
        summary=f"{ctx.user.name} added {len(rows)} case(s) to {d.name}",
        data={"count": len(rows)},
    )
    await publish(tx, ctx.org_id, "eval.updated", {"datasetId": d.id})
    for r in rows:
        await tx.refresh(r)
    return [_case_dto(r) for r in rows]


async def _case(tx: AsyncSession, ctx: Ctx, case_id: str) -> EvalCase:
    row = (
        await tx.execute(
            select(EvalCase).where(
                EvalCase.org_id == ctx.org_id, EvalCase.id == case_id, EvalCase.archived_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Eval case")
    return row


async def update_case(tx: AsyncSession, ctx: Ctx, case_id: str, body: EvalCaseBody) -> dto.EvalCaseDTO:
    require(ctx, "evals.run", "manage eval datasets")
    row = await _case(tx, ctx, case_id)
    for k, v in _case_fields(body).items():
        setattr(row, k, v)
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.case_updated",
        entity="eval_case",
        entity_id=row.id,
        summary=f"{ctx.user.name} relabelled an eval case as {body.expected.category}",
        data={"datasetId": row.dataset_id, "expected": row.expected, "split": row.split},
    )
    await publish(tx, ctx.org_id, "eval.updated", {"datasetId": row.dataset_id})
    return _case_dto(row)


async def archive_case(tx: AsyncSession, ctx: Ctx, case_id: str) -> None:
    require(ctx, "evals.run", "manage eval datasets")
    row = await _case(tx, ctx, case_id)
    await tx.execute(update(EvalCase).where(EvalCase.id == row.id).values(archived_at=clock.now()))
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.case_archived",
        entity="eval_case",
        entity_id=row.id,
        summary=f"{ctx.user.name} removed an eval case",
        data={"datasetId": row.dataset_id},
    )
    await publish(tx, ctx.org_id, "eval.updated", {"datasetId": row.dataset_id})


# ── Runs ──────────────────────────────────────────────────────────────────────────────────────────────


async def _run_dtos(tx: AsyncSession, runs: list[EvalRun]) -> list[dto.EvalRunDTO]:
    if not runs:
        return []
    versions = {
        v.id: v
        for v in (
            await tx.execute(
                select(DeploymentVersion).where(
                    DeploymentVersion.id.in_({r.deployment_version_id for r in runs})
                )
            )
        ).scalars()
    }
    names = dict(
        (
            await tx.execute(
                select(EvalDataset.id, EvalDataset.name).where(
                    EvalDataset.id.in_({r.dataset_id for r in runs})
                )
            )
        )
        .tuples()
        .all()
    )
    users = await user_refs(tx, {r.created_by for r in runs})
    out = []
    for r in runs:
        v = versions[r.deployment_version_id]
        split = r.split or {}
        out.append(
            dto.EvalRunDTO(
                id=r.id,
                dataset_id=r.dataset_id,
                dataset_name=names.get(r.dataset_id, ""),
                deployment_id=v.deployment_id,
                deployment_version_id=v.id,
                version=v.version,
                state=r.state,  # type: ignore[arg-type]
                engine=r.engine,
                config_hash=r.config_hash,
                dataset_snapshot=r.dataset_snapshot,
                current=bool(r.config_hash) and r.config_hash == v.config_hash,
                provider=r.provider,  # type: ignore[arg-type]
                split=dto.EvalRunDTOSplit(calibration=split.get("calibration", 0), test=split.get("test", 0)),
                metrics=dto.EvalMetricsDTO.model_validate(r.metrics) if r.metrics else None,
                gates=[dto.EvalGateDTO.model_validate(g) for g in r.gates or []],
                passed={"passed": True, "failed": False}.get(r.state),
                created_by=users.get(str(r.created_by)) if r.created_by else None,
                created_at=iso_ms(r.created_at),
                started_at=iso_ms(r.started_at) if r.started_at else None,
                finished_at=iso_ms(r.finished_at) if r.finished_at else None,
                error=r.error,
            )
        )
    return out


async def list_runs(
    tx: AsyncSession, ctx: Ctx, deployment_id: str | None, version_id: str | None
) -> list[dto.EvalRunDTO]:
    require(ctx, "evals.view", "see eval results")
    q = select(EvalRun).where(EvalRun.org_id == ctx.org_id)
    if version_id:
        q = q.where(EvalRun.deployment_version_id == version_id)
    if deployment_id:
        q = q.join(DeploymentVersion, DeploymentVersion.id == EvalRun.deployment_version_id).where(
            DeploymentVersion.deployment_id == deployment_id
        )
    runs = list((await tx.execute(q.order_by(EvalRun.created_at.desc()).limit(200))).scalars().all())
    return await _run_dtos(tx, runs)


async def get_run(tx: AsyncSession, ctx: Ctx, run_id: str) -> dto.EvalRunDTO:
    require(ctx, "evals.view", "see eval results")
    r = (
        await tx.execute(select(EvalRun).where(EvalRun.org_id == ctx.org_id, EvalRun.id == run_id))
    ).scalar_one_or_none()
    if r is None:
        raise not_found("Eval run")
    return (await _run_dtos(tx, [r]))[0]


async def run_results(tx: AsyncSession, ctx: Ctx, run_id: str) -> list[dto.EvalResultDTO]:
    require(ctx, "evals.view", "see eval results")
    await get_run(tx, ctx, run_id)
    rows = (
        await tx.execute(
            select(EvalResult, EvalCase.expected)
            .join(EvalCase, EvalCase.id == EvalResult.case_id)
            .where(EvalResult.org_id == ctx.org_id, EvalResult.run_id == run_id)
            .order_by(EvalResult.split, EvalCase.created_at)
        )
    ).all()
    out = []
    for res, expected in rows:
        o = res.output or {}
        out.append(
            dto.EvalResultDTO(
                id=res.id,
                case_id=res.case_id,
                split=res.split,  # type: ignore[arg-type]
                expected=str((expected or {}).get("category", "")),
                predicted=str(o.get("predicted", "")),
                confidence=float(o.get("confidence", 0.0)),
                correct=bool((res.scores or {}).get("correct")),
                hard_stop_expected=bool((expected or {}).get("hardStop")),
                hard_stop_predicted=bool(o.get("hardStops")),
                lane=o.get("lane", "manual"),
                escalated=bool(o.get("escalated")),
                latency_ms=res.latency_ms,
            )
        )
    return out


async def start_run(tx: AsyncSession, ctx: Ctx, body: StartEvalRunBody) -> dto.EvalRunDTO:
    """Freeze the dataset and the version's config hash, then queue the run for the worker."""
    require(ctx, "evals.run", "run evals")
    v = (
        await tx.execute(
            select(DeploymentVersion).where(
                DeploymentVersion.org_id == ctx.org_id, DeploymentVersion.id == body.deployment_version_id
            )
        )
    ).scalar_one_or_none()
    if v is None:
        raise not_found("Deployment version")
    d = await _dataset(tx, ctx, body.dataset_id)
    if d.deployment_id not in (None, v.deployment_id):
        raise conflict("dataset_mismatch", "This dataset belongs to another deployment.")
    cases = await load_cases(tx, ctx.org_id, d.id)
    tests = sum(c.split == "test" for c in cases)
    if not tests:
        raise conflict("empty_dataset", "The dataset needs at least one case in the test split.")
    config_hash = DeploymentConfig.model_validate(v.config).config_hash()
    if config_hash != v.config_hash:
        raise conflict(
            "config_hash_mismatch", "This version's configuration does not match its recorded hash."
        )
    run = EvalRun(
        org_id=ctx.org_id,
        dataset_id=d.id,
        deployment_version_id=v.id,
        state="queued",
        config_hash=config_hash,
        dataset_snapshot=dataset_snapshot(cases),
        split={"calibration": len(cases) - tests, "test": tests},
        created_by=ctx.user.id,
        provider=body.provider,
    )
    tx.add(run)
    await tx.flush()
    await enqueue(
        tx, ctx.org_id, "eval_run", {"runId": run.id}, dedupe_key=f"eval_run:{run.id}", max_attempts=3
    )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.run_started",
        entity="eval_run",
        entity_id=run.id,
        summary=f"{ctx.user.name} started an eval of v{v.version} on {d.name} ({len(cases)} cases)",
        data={
            "versionId": v.id,
            "datasetId": d.id,
            "configHash": config_hash,
            "datasetSnapshot": run.dataset_snapshot,
        },
    )
    await publish(tx, ctx.org_id, "eval.updated", {"runId": run.id, "state": "queued"})
    await tx.refresh(run)
    return (await _run_dtos(tx, [run]))[0]
