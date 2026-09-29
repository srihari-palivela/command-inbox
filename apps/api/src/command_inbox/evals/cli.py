"""Offline eval runs (CI, release checks) without the worker.

    command-inbox-evals run --deployment KEY [--dataset NAME] [--org SLUG] [--version N]
    command-inbox-evals retrieval --org SLUG --file cases.jsonl [--k 5] [--min-recall 0.85]

Scores the deployment's draft (or, with none, its active) version on the dataset, stores the run exactly as
the worker would (so a passing run counts for publishing), prints metrics and gates, and exits 1 if any
gate failed or the run errored.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from command_inbox.agents.config import DeploymentConfig
from command_inbox.core.audit import audit
from command_inbox.core.context import SYSTEM_ACTOR
from command_inbox.db.engine import dispose, global_tx, tenant_tx
from command_inbox.db.models import Deployment, DeploymentVersion, EvalDataset, EvalRun, Org
from command_inbox.evals.runner import dataset_snapshot, execute_run, load_cases


class CliError(Exception):
    pass


async def _resolve_org(slug: str | None, key: str) -> Org:
    async with global_tx() as g:
        orgs = list((await g.execute(select(Org).order_by(Org.created_at))).scalars().all())
    if slug:
        match = [o for o in orgs if o.slug == slug]
        if not match:
            raise CliError(f"no workspace with slug {slug!r}")
        return match[0]
    found = []
    for o in orgs:
        async with tenant_tx(o.id) as tx:
            if (await tx.execute(select(Deployment.id).where(Deployment.key == key))).first():
                found.append(o)
    if len(found) != 1:
        names = ", ".join(o.slug for o in found) or "none"
        raise CliError(f"deployment {key!r} is in {len(found)} workspaces ({names}); pass --org")
    return found[0]


async def run(key: str, dataset: str | None, org_slug: str | None, version: int | None) -> int:
    org = await _resolve_org(org_slug, key)
    async with tenant_tx(org.id) as tx:
        dep = (
            await tx.execute(select(Deployment).where(Deployment.org_id == org.id, Deployment.key == key))
        ).scalar_one_or_none()
        if dep is None:
            raise CliError(f"no deployment {key!r} in {org.slug}")
        versions = (
            (await tx.execute(select(DeploymentVersion).where(DeploymentVersion.deployment_id == dep.id)))
            .scalars()
            .all()
        )
        if version is not None:
            v = next((x for x in versions if x.version == version), None)
        else:
            v = next((x for x in versions if x.state == "draft"), None) or next(
                (x for x in versions if x.id == dep.active_version_id), None
            )
        if v is None:
            raise CliError("no such version")
        q = select(EvalDataset).where(EvalDataset.org_id == org.id, EvalDataset.deployment_id == dep.id)
        if dataset:
            q = q.where(EvalDataset.name == dataset)
        ds = (await tx.execute(q.order_by(EvalDataset.created_at).limit(1))).scalar_one_or_none()
        if ds is None:
            raise CliError(f"no eval dataset {'named ' + repr(dataset) if dataset else ''} for {key!r}")
        cases = await load_cases(tx, org.id, ds.id)
        tests = sum(c.split == "test" for c in cases)
        if not tests:
            raise CliError("the dataset has no test cases")
        r = EvalRun(
            org_id=org.id,
            dataset_id=ds.id,
            deployment_version_id=v.id,
            state="queued",
            config_hash=DeploymentConfig.model_validate(v.config).config_hash(),
            dataset_snapshot=dataset_snapshot(cases),
            split={"calibration": len(cases) - tests, "test": tests},
        )
        tx.add(r)
        await tx.flush()
        await audit(
            tx,
            org.id,
            actor=SYSTEM_ACTOR,
            action="eval.run_started",
            entity="eval_run",
            entity_id=r.id,
            summary=f"Offline eval of {dep.name} v{v.version} on {ds.name} ({len(cases)} cases)",
            data={"versionId": v.id, "datasetId": ds.id, "configHash": r.config_hash, "cli": True},
        )
        run_id, label = r.id, f"{org.slug}/{key} v{v.version} on {ds.name}"

    state = await execute_run(org.id, run_id)
    async with tenant_tx(org.id) as tx:
        r = (await tx.execute(select(EvalRun).where(EvalRun.id == run_id))).scalar_one()
    print(f"{label}: {state} (engine {r.engine or '-'}, run {run_id})")
    for name, value in (r.metrics or {}).items():
        print(f"  {name:<28} {value}")
    for g in r.gates or []:
        mark = "PASS" if g["passed"] else "FAIL"
        op = ">=" if g["op"] == "gte" else "<="
        print(f"  [{mark}] {g['label']:<32} {g['value']} {op} {g['threshold']}")
    if r.error:
        print(f"  error: {r.error}")
    return 0 if state == "passed" else 1


async def retrieval(org_slug: str, cases: list[dict], k: int, min_recall: float) -> int:  # type: ignore[type-arg]
    """Retrieval recall on labelled cases: {"question": ..., "expected": ["Document title", ...]}."""
    from command_inbox.knowledge.evaluate import evaluate

    async with global_tx() as g:
        org = (await g.execute(select(Org).where(Org.slug == org_slug))).scalar_one_or_none()
    if org is None:
        raise CliError(f"no workspace {org_slug!r}")
    async with tenant_tx(org.id) as tx:
        report = await evaluate(tx, org.id, cases, k=k)
    print(f"{org_slug}: recall@{k} {report.recall}  MRR {report.mrr}  ({len(report.cases)} questions)")
    for miss in report.misses():
        print(f"  MISS {miss.question!r}: expected {miss.expected}, got {miss.top}")
    passed = report.recall >= min_recall
    print(f"  [{'PASS' if passed else 'FAIL'}] recall@{k} {report.recall} >= {min_recall}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="command-inbox-evals", description="Run deployment evals offline.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="score a deployment version on an eval dataset")
    p.add_argument("--deployment", required=True, help="deployment key, e.g. default")
    p.add_argument("--dataset", help="dataset name (default: the deployment's first dataset)")
    p.add_argument("--org", help="workspace slug (needed when the key exists in several workspaces)")
    p.add_argument("--version", type=int, help="version number (default: the draft, else the active version)")
    r = sub.add_parser("retrieval", help="retrieval recall@k on a labelled question set (JSONL)")
    r.add_argument("--org", required=True, help="workspace slug")
    r.add_argument("--file", required=True, help='JSONL: {"question": ..., "expected": ["Document title"]}')
    r.add_argument("--k", type=int, default=5)
    r.add_argument("--min-recall", type=float, default=0.85)
    args = parser.parse_args(argv)
    cases: list[dict] = []  # type: ignore[type-arg]
    if args.command == "retrieval":
        import json
        from pathlib import Path

        lines = Path(args.file).read_text(encoding="utf-8").splitlines()
        cases = [json.loads(line) for line in lines if line.strip()]

    async def go() -> int:
        try:
            if args.command == "retrieval":
                return await retrieval(args.org, cases, args.k, args.min_recall)
            return await run(args.deployment, args.dataset, args.org, args.version)
        except CliError as err:
            print(f"command-inbox-evals: {err}", file=sys.stderr)
            return 2
        finally:
            await dispose()

    return asyncio.run(go())


if __name__ == "__main__":
    sys.exit(main())
