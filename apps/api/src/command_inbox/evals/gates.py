"""Compare a run's metrics with the version's gates. A metric that could not be computed fails its gate."""

from __future__ import annotations

from typing import Any, Literal

from command_inbox.agents.config import Gates
from command_inbox.schemas import dto

Op = Literal["gte", "lte"]


def _specs(g: Gates) -> list[tuple[str, str, str, Op, float]]:
    return [
        ("hard_stop_recall", "Hard-stop recall", "hardStopRecall", "gte", g.hard_stop_recall),
        ("accuracy", "Category accuracy", "accuracy", "gte", g.accuracy),
        ("macro_f1", "Macro-F1", "macroF1", "gte", g.macro_f1),
        ("ece", "Expected calibration error", "ece", "lte", g.ece_max),
        ("selective_accuracy", "Selective accuracy", "selectiveAccuracy", "gte", g.selective_accuracy),
        ("coverage", "System 1 coverage", "coverage", "gte", g.min_coverage),
        (
            "lane_safety",
            "Auto lane on hard-stop mail",
            "laneSafetyViolations",
            "lte",
            g.auto_lane_on_hard_stop_max,
        ),
    ]


def evaluate_gates(gates: Gates, metrics: dict[str, Any]) -> list[dto.EvalGateDTO]:
    out = []
    for key, label, metric, op, threshold in _specs(gates):
        value = metrics.get(metric)
        if value is None:
            passed = False
        elif op == "gte":
            passed = value >= threshold - 1e-9
        else:
            passed = value <= threshold + 1e-9
        out.append(
            dto.EvalGateDTO(
                key=key,
                label=label,
                metric=metric,
                op=op,
                threshold=threshold,
                value=None if value is None else round(float(value), 4),
                passed=passed,
            )
        )
    return out


def all_passed(gates: list[dto.EvalGateDTO]) -> bool:
    return bool(gates) and all(g.passed for g in gates)
