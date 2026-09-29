"""Pure eval metrics: classification quality, calibration and selective classification. No I/O.

Conventions: probabilities are per-case distributions over the deployment's category keys; `None` means a
metric cannot be computed on this data (e.g. no hard-stop cases) and a gate on it fails closed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def softmax(logits: Sequence[float], temperature: float = 1.0) -> list[float]:
    t = max(temperature, 1e-6)
    scaled = [x / t for x in logits]
    m = max(scaled)
    exps = [math.exp(x - m) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def nll(logits: Sequence[Sequence[float]], labels: Sequence[int], temperature: float) -> float:
    total = 0.0
    for row, y in zip(logits, labels, strict=True):
        p = softmax(row, temperature)[y]
        total -= math.log(max(p, 1e-12))
    return total / max(len(labels), 1)


def fit_temperature(
    logits: Sequence[Sequence[float]], labels: Sequence[int], lo: float = 0.06, hi: float = 10.0
) -> float:
    """Temperature scaling (Guo et al. 2017): the T minimising NLL, by a log-space grid then golden section.

    Fitted on the calibration split only; with no calibration data the caller keeps the configured T.
    """
    if not labels:
        raise ValueError("no calibration cases")
    grid = [lo * (hi / lo) ** (i / 60) for i in range(61)]
    best = min(grid, key=lambda t: nll(logits, labels, t))
    i = grid.index(best)
    a, b = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    phi = (math.sqrt(5) - 1) / 2
    c, d = b - phi * (b - a), a + phi * (b - a)
    for _ in range(40):
        if nll(logits, labels, c) < nll(logits, labels, d):
            b = d
        else:
            a = c
        c, d = b - phi * (b - a), a + phi * (b - a)
    return round((a + b) / 2, 4)


def accuracy(y_true: Sequence[str], y_pred: Sequence[str]) -> float | None:
    if not y_true:
        return None
    return sum(t == p for t, p in zip(y_true, y_pred, strict=True)) / len(y_true)


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str]) -> float | None:
    """Unweighted mean F1 over every label seen in either the truth or the predictions."""
    if not y_true:
        return None
    labels = sorted(set(y_true) | set(y_pred))
    scores = []
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(y_true, y_pred, strict=True))
        fp = sum(t != label and p == label for t, p in zip(y_true, y_pred, strict=True))
        fn = sum(t == label and p != label for t, p in zip(y_true, y_pred, strict=True))
        denom = 2 * tp + fp + fn
        scores.append(2 * tp / denom if denom else 0.0)
    return sum(scores) / len(scores)


def recall(expected: Sequence[bool], predicted: Sequence[bool]) -> float | None:
    positives = sum(expected)
    if not positives:
        return None
    return sum(e and p for e, p in zip(expected, predicted, strict=True)) / positives


def expected_calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], bins: int = 10
) -> float | None:
    """ECE with equal-width bins over the top-label confidence."""
    n = len(confidences)
    if not n:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(confidences) if (lo < c <= hi) or (b == 0 and c == 0.0)]
        if not idx:
            continue
        conf = sum(confidences[i] for i in idx) / len(idx)
        acc = sum(bool(correct[i]) for i in idx) / len(idx)
        total += len(idx) / n * abs(acc - conf)
    return total


def conformal_qhat(true_label_probs: Sequence[float], coverage: float) -> float | None:
    """Split-conformal threshold for LAC scores s = 1 - p(true label) at the target coverage 1 - α."""
    n = len(true_label_probs)
    if not n:
        return None
    scores = sorted(1.0 - p for p in true_label_probs)
    k = math.ceil((n + 1) * coverage)
    if k > n:
        return 1.0  # too few calibration cases to certify this coverage: every label stays in the set
    return scores[k - 1]


def prediction_set(probs: dict[str, float], qhat: float | None) -> list[str]:
    """Labels whose probability passes the conformal threshold (every label ≥ 1 - q̂)."""
    if qhat is None:
        return [max(probs, key=probs.__getitem__)]
    return [k for k, p in probs.items() if p >= 1.0 - qhat - 1e-12]


def selective(accepted: Sequence[bool], correct: Sequence[bool]) -> tuple[float | None, float | None]:
    """(selective accuracy, coverage): accuracy on what System 1 accepted, and the share it accepted."""
    n = len(accepted)
    if not n:
        return None, None
    taken = [c for a, c in zip(accepted, correct, strict=True) if a]
    coverage = len(taken) / n
    return (sum(taken) / len(taken) if taken else None), coverage


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]
