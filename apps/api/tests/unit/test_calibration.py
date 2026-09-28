"""Calibration: temperature fitting reduces NLL and ECE; split-conformal sets reach the target coverage."""

from __future__ import annotations

import math
import random

from command_inbox.agents.decision.calibration import (
    calibrate,
    conformal_qhat,
    cumulative_set,
    empirical_coverage,
    expected_calibration_error,
    fit_temperature,
    nll,
    prediction_set,
    softmax,
)


def synthetic(
    n: int, k: int = 5, overconfidence: float = 4.0, seed: int = 1
) -> tuple[list[list[float]], list[int]]:
    """Well-specified logits (noisy evidence for the true class) scaled up: an overconfident model."""
    rng = random.Random(seed)  # noqa: S311 - synthetic data
    rows, labels = [], []
    for _ in range(n):
        y = rng.randrange(k)
        logits = [rng.gauss(0, 1.0) for _ in range(k)]
        logits[y] += 1.5
        rows.append([x * overconfidence for x in logits])
        labels.append(y)
    return rows, labels


def test_softmax_temperature():
    p1 = softmax([2.0, 1.0, 0.0])
    p3 = softmax([2.0, 1.0, 0.0], temperature=3.0)
    assert math.isclose(sum(p1), 1.0) and math.isclose(sum(p3), 1.0)
    assert p3[0] < p1[0] and p3[2] > p1[2]


def test_temperature_fit_reduces_nll_and_ece():
    rows, labels = synthetic(4000)
    fit_rows, fit_y = rows[:1500], labels[:1500]
    test_rows, test_y = rows[1500:], labels[1500:]
    t = fit_temperature(fit_rows, fit_y)
    assert 2.5 < t < 6.0  # recovers the overconfidence factor
    assert nll(test_rows, test_y, t) < nll(test_rows, test_y) * 0.8
    before = expected_calibration_error([softmax(r) for r in test_rows], test_y)
    after = expected_calibration_error([softmax(r, t) for r in test_rows], test_y)
    assert after < before / 2 and after < 0.05


def test_split_conformal_coverage_is_close_to_target():
    rows, labels = synthetic(3000, seed=3)
    probs = [softmax(r, 4.0) for r in rows]
    cal_p, cal_y = probs[:1500], labels[:1500]
    test_p, test_y = probs[1500:], labels[1500:]
    for alpha in (0.05, 0.1, 0.2):
        q = conformal_qhat(cal_p, cal_y, alpha)
        sets = [[i for i, p in enumerate(ps) if 1 - p <= q] for ps in test_p]
        cov = empirical_coverage(sets, test_y)
        assert 1 - alpha - 0.03 <= cov <= 1 - alpha + 0.04, (alpha, cov)


def test_calibrate_end_to_end_reports_on_held_out_split():
    rows, labels = synthetic(2000, seed=5)
    fit = calibrate(rows, labels, alpha=0.1)
    assert fit.n_fit + fit.n_cal + fit.n_test == 2000 and fit.n_test > 0
    assert fit.nll_after < fit.nll_before and fit.ece_after < fit.ece_before
    assert 0.86 <= fit.coverage <= 0.95
    assert fit.mean_set_size >= 1.0


def test_prediction_sets_never_empty_and_ranked():
    dist = {"a": 0.6, "b": 0.3, "c": 0.1}
    assert prediction_set(dist, 0.75) == ["a", "b"]
    assert prediction_set(dist, 0.5) == ["a"]
    assert prediction_set(dist, 0.0) == ["a"]  # nothing passes: the top label stays
    assert cumulative_set(dist, 0.95) == ["a", "b", "c"]
    assert cumulative_set(dist, 0.5) == ["a"]
