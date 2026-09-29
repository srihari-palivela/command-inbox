"""Eval metrics, gates and the default deployment builder (pure code, no database)."""

from __future__ import annotations

import math

import pytest

from command_inbox.agents.config import Gates
from command_inbox.evals import metrics as m
from command_inbox.evals.gates import all_passed, evaluate_gates
from command_inbox.modules.deployments.defaults import FALLBACK_KEY, Setup, build_default_config


def test_accuracy_and_macro_f1():
    y_true = ["a", "a", "b", "b", "c"]
    y_pred = ["a", "b", "b", "b", "a"]
    assert m.accuracy(y_true, y_pred) == pytest.approx(3 / 5)
    # F1: a = 2·1/(2+1+1) = 0.5, b = 2·2/(4+1+0) = 0.8, c = 0
    assert m.macro_f1(y_true, y_pred) == pytest.approx((0.5 + 0.8 + 0.0) / 3)
    assert m.accuracy([], []) is None and m.macro_f1([], []) is None


def test_macro_f1_counts_labels_only_predicted():
    assert m.macro_f1(["a", "a"], ["a", "z"]) == pytest.approx((2 / 3 + 0.0) / 2)


def test_hard_stop_recall_is_none_without_positives():
    assert m.recall([True, True, False], [True, False, True]) == pytest.approx(0.5)
    assert m.recall([False, False], [True, False]) is None


def test_ece():
    assert m.expected_calibration_error([1.0, 1.0], [True, True]) == pytest.approx(0.0)
    # two cases at 0.9 confidence, one right: |0.5 - 0.9| = 0.4
    assert m.expected_calibration_error([0.9, 0.9], [True, False]) == pytest.approx(0.4)
    # separate bins are weighted by their share
    assert m.expected_calibration_error([0.95, 0.55], [True, True]) == pytest.approx(0.5 * 0.05 + 0.5 * 0.45)
    assert m.expected_calibration_error([], []) is None


def test_selective_accuracy_and_coverage():
    acc, cov = m.selective([True, True, False, True], [True, False, False, True])
    assert acc == pytest.approx(2 / 3) and cov == pytest.approx(0.75)
    assert m.selective([False, False], [True, True]) == (None, 0.0)


def test_percentile_nearest_rank():
    assert m.percentile(list(range(1, 101)), 95) == 95
    assert m.percentile([7.0], 95) == 7.0
    assert m.percentile([], 95) is None


def test_softmax_and_temperature_fit():
    p = m.softmax([2.0, 0.0], 1.0)
    assert sum(p) == pytest.approx(1.0) and p[0] == pytest.approx(1 / (1 + math.exp(-2)))
    # Over-confident logits with 30% errors: the fitted temperature softens them (T > 1).
    logits = [[6.0, 0.0]] * 7 + [[6.0, 0.0]] * 3
    labels = [0] * 7 + [1] * 3
    t = m.fit_temperature(logits, labels)
    assert t > 1.0
    assert m.nll(logits, labels, t) < m.nll(logits, labels, 1.0)
    # The optimum puts p(top) near the empirical accuracy.
    assert m.softmax([6.0, 0.0], t)[0] == pytest.approx(0.7, abs=0.02)
    with pytest.raises(ValueError):
        m.fit_temperature([], [])


def test_conformal_threshold_and_sets():
    probs = [0.9] * 18 + [0.6, 0.3]  # n = 20
    qhat = m.conformal_qhat(probs, 0.95)  # k = ceil(21 · 0.95) = 20 → the largest score
    assert qhat == pytest.approx(0.7)
    assert m.prediction_set({"a": 0.8, "b": 0.2}, 0.1) == []  # nothing ≥ 0.9: the engine is unsure
    assert m.prediction_set({"a": 0.95, "b": 0.05}, 0.1) == ["a"]
    assert sorted(m.prediction_set({"a": 0.5, "b": 0.4, "c": 0.1}, 0.7)) == ["a", "b"]
    # Too few calibration cases to certify 95% coverage: every label stays in the set.
    assert m.conformal_qhat([0.99] * 5, 0.95) == 1.0
    assert m.conformal_qhat([], 0.95) is None


def test_gates_compare_and_fail_closed():
    g = Gates()
    good = {
        "hardStopRecall": 1.0,
        "accuracy": 0.95,
        "macroF1": 0.9,
        "ece": 0.01,
        "selectiveAccuracy": 1.0,
        "coverage": 0.8,
        "laneSafetyViolations": 0,
    }
    gates = evaluate_gates(g, good)
    assert all_passed(gates) and len(gates) == 7
    bad = evaluate_gates(g, {**good, "hardStopRecall": None, "laneSafetyViolations": 1, "ece": 0.2})
    failed = {x.key for x in bad if not x.passed}
    assert failed == {"hard_stop_recall", "lane_safety", "ece"}
    assert not all_passed(bad)


def test_default_config_from_setup():
    setup = Setup(
        org_name="Bank",
        confidence_bar=0.78,
        departments={"d1": "Retail", "d2": "Cards"},
        query_types=[
            {"name": "Statement re-issue", "default_lane": "auto", "department_id": "d1"},
            {"name": "Statement re-issue", "default_lane": "draft", "department_id": "d2"},  # duplicate name
            {"name": "Locker rent waiver", "default_lane": "manual", "department_id": None},
        ],
        bucket_rules=[
            {
                "description": "Dispute form",
                "kind": "Deterministic",
                "pattern": {"any": ["dispute form"], "queryType": "Locker rent waiver"},
            },
            {"description": "Customer threatens to go to the press", "kind": "Hard stop", "pattern": None},
            {"description": "Corporate CIF", "kind": "Deterministic", "pattern": None},
        ],
        priority_rules=[
            {"key": "p1", "description": "Regulator named", "target": "P1 · Critical", "hard": True}
        ],
    )
    config, notes = build_default_config(setup)
    keys = [c.key for c in config.taxonomy.categories]
    assert keys == ["statement_re_issue", "statement_re_issue_2", "locker_rent_waiver", FALLBACK_KEY]
    assert config.taxonomy.categories[2].department == "Unassigned"
    assert config.rules.bucket_overrides[0].value == "dispute form"
    assert config.rules.bucket_overrides[0].category == "locker_rent_waiver"
    assert "customer_threatens_to_go_to_the_press" in {h.key for h in config.rules.hard_stops}
    assert {"regulator_named", "legal_notice", "suspected_fraud", "vulnerable_customer"} <= {
        h.key for h in config.rules.hard_stops
    }
    assert config.rules.priority[0].target == "p1" and config.rules.priority[0].hard
    assert config.thresholds.auto_min_confidence == pytest.approx(0.78)
    assert notes == []
    # Stable: the same setup hashes the same.
    assert build_default_config(setup)[0].config_hash() == config.config_hash()


def test_default_config_for_a_tenant_with_one_query_type_is_still_valid():
    config, _ = build_default_config(Setup(org_name="Tiny", query_types=[{"name": "General enquiry"}]))
    assert [c.key for c in config.taxonomy.categories] == ["general_enquiry", FALLBACK_KEY]


async def test_keyword_scorer_fallback_scores_a_dataset_offline():
    """The heuristic path works without the agents runtime (and never lets a hard stop reach Auto)."""
    from command_inbox.agents.config import DeploymentConfig
    from command_inbox.evals.engine import KeywordScorer
    from command_inbox.evals.runner import Case, evaluate
    from tests.integration.admin_support import desk_config, passing_cases

    config = DeploymentConfig.model_validate(desk_config())
    cases = [
        Case(id=str(i), split=c["split"], input=c["input"], expected=c["expected"])
        for i, c in enumerate(passing_cases())
    ]
    outcome = await evaluate(config, cases, KeywordScorer(config))
    assert outcome.engine == "keyword"
    assert outcome.metrics["accuracy"] == 1.0 and outcome.metrics["hardStopRecall"] == 1.0
    assert outcome.metrics["laneSafetyViolations"] == 0
    assert outcome.passed, [g for g in outcome.gates if not g.passed]
