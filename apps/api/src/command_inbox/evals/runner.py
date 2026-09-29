"""Run an eval: score every case of a frozen dataset with one deployment version, compute metrics and gates.

Integrity (docs/architecture/python-platform.md §9):
- A run is bound to the version's config hash and to a hash of the dataset (case ids, splits, inputs and
  labels) taken when it was queued. If either changed before it executes, the run errors instead of
  scoring something else.
- Temperature and the conformal threshold are fitted on the `calibration` split only; every gate is scored
  on the `test` split, so the gate never sees data the calibration was fitted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import delete, select

from command_inbox.agents.config import DeploymentConfig
from command_inbox.core.audit import audit
from command_inbox.core.clock import clock
from command_inbox.core.context import SYSTEM_ACTOR
from command_inbox.core.crypto import canonical_json, sha256
from command_inbox.core.jobs import JobRow
from command_inbox.core.outbox import publish
from command_inbox.db.engine import tenant_tx
from command_inbox.db.models import DeploymentVersion, EvalCase, EvalResult, EvalRun
from command_inbox.evals import metrics as m
from command_inbox.evals.engine import CaseInput, Scorer, fit_temperature, load_scorer
from command_inbox.evals.gates import all_passed, evaluate_gates
from command_inbox.schemas import dto

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    split: str
    input: dict[str, Any]
    expected: dict[str, Any]

    @property
    def category(self) -> str:
        return str(self.expected.get("category", ""))

    @property
    def hard_stop(self) -> bool:
        return bool(self.expected.get("hardStop", False))


@dataclass(slots=True)
class CaseOutcome:
    case: Case
    predicted: str
    confidence: float
    distribution: dict[str, float]
    hard_stops: list[str]
    lane: str
    escalated: bool
    latency_ms: float
    cost_minor: float

    @property
    def correct(self) -> bool:
        return self.predicted == self.case.category


@dataclass(slots=True)
class Outcome:
    engine: str
    metrics: dict[str, Any]
    gates: list[dto.EvalGateDTO]
    passed: bool
    cases: list[CaseOutcome] = field(default_factory=list)


def case_of(row: EvalCase) -> Case:
    return Case(
        id=str(row.id), split=row.split, input=dict(row.input or {}), expected=dict(row.expected or {})
    )


def dataset_snapshot(cases: list[Case]) -> str:
    """Hash of the frozen dataset: every case's id, split, input and label, in id order."""
    body = [
        {"id": c.id, "split": c.split, "input": c.input, "expected": c.expected}
        for c in sorted(cases, key=lambda c: c.id)
    ]
    return sha256(canonical_json(body))


def _lane(config: DeploymentConfig, label: str, confidence: float, escalated: bool, stopped: bool) -> str:
    """Deterministic lane from System 1's answer (the adjudicator is not part of the System 1 eval)."""
    if stopped or escalated:
        return "manual"
    t = config.thresholds
    default = next((c.default_lane for c in config.taxonomy.categories if c.key == label), "manual")
    if default == "auto" and confidence >= t.auto_min_confidence:
        return "auto"
    if default in ("auto", "draft") and confidence >= t.draft_min_confidence:
        return "draft"
    return "manual"


async def evaluate(config: DeploymentConfig, cases: list[Case], scorer: Scorer | None = None) -> Outcome:
    scorer = scorer or load_scorer(config)
    keys = [c.key for c in config.taxonomy.categories]
    raw: list[tuple[Case, list[float], list[str], float, float]] = []
    for case in cases:
        s = await scorer.score(
            CaseInput(
                subject=str(case.input.get("subject", "")),
                body=str(case.input.get("body", "")),
                from_email=case.input.get("fromEmail"),
            )
        )
        floor = min(s.logits.values(), default=0.0) - 10.0
        raw.append((case, [s.logits.get(k, floor) for k in keys], s.hard_stops, s.latency_ms, s.cost_minor))

    calib = [
        (logits, keys.index(c.category))
        for c, logits, *_ in raw
        if c.split == "calibration" and c.category in keys
    ]
    temperature = config.models.temperature
    if calib:
        temperature = fit_temperature([x for x, _ in calib], [y for _, y in calib])
    qhat = config.models.conformal_qhat
    if calib:
        true_probs = [m.softmax(x, temperature)[y] for x, y in calib]
        qhat = m.conformal_qhat(true_probs, config.thresholds.conformal_coverage)

    outcomes: list[CaseOutcome] = []
    for case, logits, stops, latency, cost in raw:
        probs = m.softmax(logits, temperature)
        dist = dict(zip(keys, probs, strict=True))
        label = max(dist, key=dist.__getitem__)
        confidence = dist[label]
        pset = m.prediction_set(dist, qhat)
        escalated = confidence < config.thresholds.escalate_below or (qhat is not None and len(pset) != 1)
        outcomes.append(
            CaseOutcome(
                case=case,
                predicted=label,
                confidence=confidence,
                distribution=dist,
                hard_stops=stops,
                lane=_lane(config, label, confidence, escalated, bool(stops)),
                escalated=escalated,
                latency_ms=latency,
                cost_minor=cost,
            )
        )

    test = [o for o in outcomes if o.case.split == "test"]
    y_true = [o.case.category for o in test]
    y_pred = [o.predicted for o in test]
    correct = [o.correct for o in test]
    confs = [o.confidence for o in test]
    uncalibrated = [
        max(m.softmax(logits, config.models.temperature)) for c, logits, *_ in raw if c.split == "test"
    ]
    sel_acc, coverage = m.selective([not o.escalated for o in test], correct)
    in_set = (
        [o.case.category in m.prediction_set(o.distribution, qhat) for o in test] if qhat is not None else []
    )
    latencies = [o.latency_ms for o in outcomes]
    metrics = dto.EvalMetricsDTO(
        cases=len(test),
        calibration_cases=len(calib),
        accuracy=_r(m.accuracy(y_true, y_pred)),
        macro_f1=_r(m.macro_f1(y_true, y_pred)),
        hard_stop_recall=_r(m.recall([o.case.hard_stop for o in test], [bool(o.hard_stops) for o in test])),
        ece=_r(m.expected_calibration_error(confs, correct)),
        ece_uncalibrated=_r(m.expected_calibration_error(uncalibrated, correct)),
        selective_accuracy=_r(sel_acc),
        coverage=_r(coverage),
        conformal_coverage=_r(sum(in_set) / len(in_set)) if in_set else None,
        lane_safety_violations=sum(1 for o in test if o.case.hard_stop and o.lane == "auto"),
        escalation_rate=_r(1 - coverage) if coverage is not None else None,
        p95_latency_ms=_r(m.percentile(latencies, 95)),
        cost_per_thousand_mails_minor=_r(sum(o.cost_minor for o in outcomes) / len(outcomes) * 1000)
        if outcomes
        else None,
        temperature=round(temperature, 4),
        conformal_qhat=_r(qhat),
    ).model_dump(mode="json", by_alias=True)
    gates = evaluate_gates(config.gates, metrics)
    return Outcome(engine=scorer.name, metrics=metrics, gates=gates, passed=all_passed(gates), cases=outcomes)


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)


# ── Persistence ─────────────────────────────────────────────────────────────────────────────────────


async def load_cases(tx: Any, org_id: str, dataset_id: str) -> list[Case]:
    rows = (
        (
            await tx.execute(
                select(EvalCase)
                .where(
                    EvalCase.org_id == org_id,
                    EvalCase.dataset_id == dataset_id,
                    EvalCase.archived_at.is_(None),
                )
                .order_by(EvalCase.created_at, EvalCase.id)
            )
        )
        .scalars()
        .all()
    )
    return [case_of(r) for r in rows]


async def execute_run(org_id: str, run_id: str) -> str | None:
    """Score a queued run end to end. Idempotent: a finished run is left alone. Returns the final state."""
    async with tenant_tx(org_id) as tx:
        run = (
            await tx.execute(
                select(EvalRun).where(EvalRun.org_id == org_id, EvalRun.id == run_id).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None or run.state not in ("queued", "running"):
            return run.state if run else None
        version = (
            await tx.execute(
                select(DeploymentVersion).where(
                    DeploymentVersion.org_id == org_id, DeploymentVersion.id == run.deployment_version_id
                )
            )
        ).scalar_one()
        cases = await load_cases(tx, org_id, run.dataset_id)
        config = DeploymentConfig.model_validate(version.config)
        problem = None
        if dataset_snapshot(cases) != run.dataset_snapshot:
            problem = "The dataset changed after this run was queued; start a new run."
        elif config.config_hash() != run.config_hash:
            problem = "The version's configuration changed after this run was queued; start a new run."
        if problem:
            await _finish(tx, run, state="error", error=problem)
            return "error"
        run.state, run.started_at = "running", clock.now()

    outcome = await evaluate(config, cases)

    async with tenant_tx(org_id) as tx:
        run = (
            await tx.execute(
                select(EvalRun).where(EvalRun.org_id == org_id, EvalRun.id == run_id).with_for_update()
            )
        ).scalar_one()
        if run.state != "running":
            return run.state
        await tx.execute(delete(EvalResult).where(EvalResult.org_id == org_id, EvalResult.run_id == run_id))
        for o in outcome.cases:
            tx.add(
                EvalResult(
                    org_id=org_id,
                    run_id=run_id,
                    case_id=o.case.id,
                    split=o.case.split,
                    output={
                        "predicted": o.predicted,
                        "confidence": round(o.confidence, 4),
                        "distribution": {
                            k: round(v, 4)
                            for k, v in sorted(o.distribution.items(), key=lambda kv: -kv[1])[:5]
                        },
                        "hardStops": o.hard_stops,
                        "lane": o.lane,
                        "escalated": o.escalated,
                    },
                    scores={"correct": o.correct, "hardStopExpected": o.case.hard_stop},
                    passed=o.correct and (bool(o.hard_stops) or not o.case.hard_stop),
                    latency_ms=round(o.latency_ms),
                )
            )
        run.engine = outcome.engine
        await _finish(
            tx,
            run,
            state="passed" if outcome.passed else "failed",
            metrics=outcome.metrics,
            gates=[g.model_dump(mode="json", by_alias=True) for g in outcome.gates],
        )
        return run.state


async def _finish(
    tx: Any,
    run: EvalRun,
    *,
    state: str,
    error: str | None = None,
    metrics: dict[str, Any] | None = None,
    gates: list[dict[str, Any]] | None = None,
) -> None:
    run.state, run.error, run.finished_at = state, error, clock.now()
    if metrics is not None:
        run.metrics = metrics
    if gates is not None:
        run.gates = gates
    await tx.flush()
    failed = [g["label"] for g in gates or [] if not g["passed"]]
    summary = {
        "passed": "Eval run passed every gate",
        "failed": f"Eval run failed {len(failed)} gate(s): {', '.join(failed)}",
        "error": f"Eval run could not complete: {error}",
    }[state]
    await audit(
        tx,
        run.org_id,
        actor=SYSTEM_ACTOR,
        action="eval.run_finished",
        entity="eval_run",
        entity_id=run.id,
        summary=summary[:500],
        data={"state": state, "configHash": run.config_hash, "datasetSnapshot": run.dataset_snapshot},
    )
    await publish(tx, run.org_id, "eval.updated", {"runId": run.id, "state": state})


async def run_eval_job(job: JobRow) -> None:
    """Worker handler for `eval_run` jobs (payload: {"runId": ...})."""
    run_id = str(job.payload["runId"])
    try:
        state = await execute_run(job.org_id, run_id)
    except Exception as err:
        if job.attempts < job.max_attempts:
            raise  # retried with backoff (e.g. the model server was briefly down)
        log.exception("eval run failed", run_id=run_id)
        async with tenant_tx(job.org_id) as tx:
            run = (
                await tx.execute(
                    select(EvalRun)
                    .where(EvalRun.org_id == job.org_id, EvalRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is not None and run.state in ("queued", "running"):
                await _finish(tx, run, state="error", error=str(err)[:500])
        return
    log.info("eval run finished", run_id=run_id, state=state)
