"""Calibration for the decision engine: temperature scaling, split-conformal prediction sets, ECE.

Per deployment: split the labelled mail into fit / calibration / test. Fit one temperature T on the fit split
by minimising negative log-likelihood (Guo et al. 2017), compute the conformal threshold q̂ on the calibration
split (LAC score 1 − p(true label), Sadinle et al.; the MAPIE "lac" method), and report ECE and empirical
coverage on the test split only — the gate never sees data the parameters were fitted on.

Pure Python: the label sets are small (tens of options, hundreds of mails), so no numerical stack is needed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

T_MIN, T_MAX = 0.05, 10.0


def softmax(logits: Sequence[float], temperature: float = 1.0) -> list[float]:
    t = max(temperature, 1e-6)
    scaled = [x / t for x in logits]
    m = max(scaled)
    exps = [math.exp(x - m) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def softmax_dict(logits: Mapping[str, float], temperature: float = 1.0) -> dict[str, float]:
    keys = list(logits)
    return dict(zip(keys, softmax([logits[k] for k in keys], temperature), strict=True))


def nll(logits_rows: Sequence[Sequence[float]], labels: Sequence[int], temperature: float = 1.0) -> float:
    """Mean negative log-likelihood of the true labels."""
    total = 0.0
    for row, y in zip(logits_rows, labels, strict=True):
        total -= math.log(max(softmax(row, temperature)[y], 1e-12))
    return total / max(len(labels), 1)


def fit_temperature(
    logits_rows: Sequence[Sequence[float]], labels: Sequence[int], lo: float = T_MIN, hi: float = T_MAX
) -> float:
    """One-dimensional NLL minimisation over log T (golden-section search; NLL(T) is unimodal here)."""
    if not labels:
        return 1.0
    a, b = math.log(lo), math.log(hi)
    g = (math.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = nll(logits_rows, labels, math.exp(c)), nll(logits_rows, labels, math.exp(d))
    for _ in range(80):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = nll(logits_rows, labels, math.exp(c))
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = nll(logits_rows, labels, math.exp(d))
        if b - a < 1e-6:
            break
    return round(math.exp((a + b) / 2), 4)


def expected_calibration_error(
    probs_rows: Sequence[Sequence[float]], labels: Sequence[int], n_bins: int = 15
) -> float:
    """Top-label ECE with equal-width confidence bins."""
    n = len(labels)
    if n == 0:
        return 0.0
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for probs, y in zip(probs_rows, labels, strict=True):
        top = max(range(len(probs)), key=lambda i: probs[i])
        conf = probs[top]
        bins[min(int(conf * n_bins), n_bins - 1)].append((conf, top == y))
    ece = 0.0
    for b in bins:
        if b:
            acc = sum(1 for _, ok in b if ok) / len(b)
            avg = sum(c for c, _ in b) / len(b)
            ece += len(b) / n * abs(acc - avg)
    return ece


def conformal_qhat(probs_rows: Sequence[Sequence[float]], labels: Sequence[int], alpha: float) -> float:
    """Split-conformal threshold on the LAC score s = 1 − p(true); finite-sample corrected quantile."""
    n = len(labels)
    if n == 0:
        return 1.0
    scores = sorted(1.0 - probs[y] for probs, y in zip(probs_rows, labels, strict=True))
    k = math.ceil((n + 1) * (1 - alpha))
    return 1.0 if k > n else scores[k - 1]


def prediction_set(distribution: Mapping[str, float], qhat: float) -> list[str]:
    """Every label whose score 1 − p is within q̂, most likely first; never empty."""
    ranked = sorted(distribution, key=lambda k: -distribution[k])
    chosen = [k for k in ranked if 1.0 - distribution[k] <= qhat + 1e-12]
    return chosen or ranked[:1]


def cumulative_set(distribution: Mapping[str, float], coverage: float) -> list[str]:
    """Uncalibrated fallback: the smallest top-k set whose mass reaches the coverage target."""
    ranked = sorted(distribution, key=lambda k: -distribution[k])
    out: list[str] = []
    mass = 0.0
    for k in ranked:
        out.append(k)
        mass += distribution[k]
        if mass >= coverage - 1e-9:
            break
    return out


def empirical_coverage(sets: Sequence[Sequence[int]], labels: Sequence[int]) -> float:
    if not labels:
        return 0.0
    return sum(1 for s, y in zip(sets, labels, strict=True) if y in s) / len(labels)


def split_indices(
    n: int, fractions: tuple[float, float, float] = (0.4, 0.3, 0.3), seed: int = 7
) -> tuple[list[int], list[int], list[int]]:
    """Deterministic shuffled fit / calibration / test split."""
    idx = list(range(n))
    random.Random(seed).shuffle(idx)  # noqa: S311 - reproducible data split, not security
    a = int(n * fractions[0])
    b = a + int(n * fractions[1])
    return idx[:a], idx[a:b], idx[b:]


@dataclass(frozen=True, slots=True)
class CalibrationFit:
    temperature: float
    qhat: float
    alpha: float
    nll_before: float
    nll_after: float
    ece_before: float
    ece_after: float
    coverage: float  # empirical, on the test split
    mean_set_size: float
    n_fit: int
    n_cal: int
    n_test: int


def calibrate(
    logits_rows: Sequence[Sequence[float]], labels: Sequence[int], alpha: float = 0.05, seed: int = 7
) -> CalibrationFit:
    """Fit T on the fit split, q̂ on the calibration split, report on the held-out test split."""
    fit_i, cal_i, test_i = split_indices(len(labels), seed=seed)

    def pick(ix: list[int]) -> tuple[list[Sequence[float]], list[int]]:
        return [logits_rows[i] for i in ix], [labels[i] for i in ix]

    fx, fy = pick(fit_i)
    cx, cy = pick(cal_i)
    tx, ty = pick(test_i)
    t = fit_temperature(fx, fy)
    qhat = conformal_qhat([softmax(r, t) for r in cx], cy, alpha)
    test_before = [softmax(r) for r in tx]
    test_after = [softmax(r, t) for r in tx]
    sets = [[i for i, p in enumerate(probs) if 1.0 - p <= qhat + 1e-12] for probs in test_after]
    return CalibrationFit(
        temperature=t,
        qhat=qhat,
        alpha=alpha,
        nll_before=nll(tx, ty),
        nll_after=nll(tx, ty, t),
        ece_before=expected_calibration_error(test_before, ty),
        ece_after=expected_calibration_error(test_after, ty),
        coverage=empirical_coverage(sets, ty),
        mean_set_size=sum(len(s) for s in sets) / max(len(sets), 1),
        n_fit=len(fy),
        n_cal=len(cy),
        n_test=len(ty),
    )
