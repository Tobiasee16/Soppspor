"""Evaluation metrics for presence-only models.

Hand-written and stdlib-only on purpose. This module is the yardstick every
Phase 0 conclusion rests on, so it should be readable end to end and testable
without fitting anything. A yardstick you have not checked is not a yardstick.

Two metrics, because they fail in different ways:

* ROC AUC treats background points as pseudo-absences. That is not strictly
  correct for presence-only data - a background point may well be occupied -
  but it is the standard comparator and its inflation under random CV is the
  thing we most need to detect.
* The continuous Boyce index needs no absences at all. It asks whether the
  model predicts high suitability where presences actually concentrate,
  relative to how much of the landscape carries each suitability value. For
  presence-only work it is the more honest of the two.
"""
from __future__ import annotations

import math

NAN = float("nan")


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, 1-based, ties shared."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def roc_auc(y_true: list[int], scores: list[float]) -> float:
    """AUC via the Mann-Whitney U identity, with ties counted as half.

    Returns NaN when one class is absent - an undefined AUC must never be
    silently reported as 0.5, which would look like a null result rather than
    a broken fold.
    """
    if len(y_true) != len(scores):
        raise ValueError("y_true and scores differ in length")
    pos = sum(1 for v in y_true if v == 1)
    neg = len(y_true) - pos
    if pos == 0 or neg == 0:
        return NAN
    r = _ranks(scores)
    rank_sum_pos = sum(r[i] for i, v in enumerate(y_true) if v == 1)
    u = rank_sum_pos - pos * (pos + 1) / 2.0
    return u / (pos * neg)


def spearman(a: list[float], b: list[float]) -> float:
    """Tie-aware Spearman correlation (Pearson on average ranks)."""
    if len(a) != len(b):
        raise ValueError("inputs differ in length")
    n = len(a)
    if n < 3:
        return NAN
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return NAN
    return num / (da * db)


def continuous_boyce(
    presence_scores: list[float],
    background_scores: list[float],
    n_windows: int = 101,
    window_width_frac: float = 0.1,
) -> float:
    """Continuous Boyce index (Hirzel et al. 2006).

    Slides a window across the predicted suitability range. In each window,
    F = (share of presences) / (share of background). A good model produces F
    rising monotonically with suitability; the index is the Spearman
    correlation between window position and F.

    +1 = predictions rank presences perfectly. ~0 = no better than random.
    Negative = the model is systematically inverted, which is a real and
    reportable outcome, not a bug to be flipped away.

    Returns NaN rather than a number when there is nothing to measure.
    """
    if not presence_scores or not background_scores:
        return NAN

    lo = min(min(presence_scores), min(background_scores))
    hi = max(max(presence_scores), max(background_scores))
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        return NAN

    width = (hi - lo) * window_width_frac
    if width <= 0:
        return NAN

    centres, ratios = [], []
    n_p, n_b = len(presence_scores), len(background_scores)
    for i in range(n_windows):
        centre = lo + width / 2.0 + i * (hi - lo - width) / max(n_windows - 1, 1)
        a, b = centre - width / 2.0, centre + width / 2.0
        p = sum(1 for s in presence_scores if a <= s <= b) / n_p
        e = sum(1 for s in background_scores if a <= s <= b) / n_b
        if e > 0:
            centres.append(centre)
            ratios.append(p / e)

    if len(centres) < 3:
        return NAN
    return spearman(centres, ratios)


def continuous_boyce_quantile(
    presence_scores: list[float],
    background_scores: list[float],
    n_windows: int = 101,
    window_width_frac: float = 0.1,
    min_background: int = 10,
) -> float:
    """Boyce index with windows placed on background quantiles, not score values.

    Reported alongside `continuous_boyce`, never instead of it. The published
    method slides an equal-width window across the score *range*, which is
    sound when scores occupy that range. Presence-background models trained at
    a ~7% base rate do not: almost all mass sits near zero with a thin right
    tail, so most equal-width windows land in the tail holding a handful of
    background points, and the ratio F = P/E there is decided by whether one
    or two points happened to fall in.

    Measured on synthetic data at real fold sizes, that bias is severe enough
    to invert the meaning of the sign: a model with **no skill at all** returns
    about -0.42 from the published method, where the correct answer is zero.
    This version returns -0.01 on the same data. See
    `scripts/check_boyce.py` and `data/processed/boyce_diagnosis.md` - the
    comparison was made against known answers on synthetic scores, before and
    independently of any real species result.

    Quantile windows compress the top of the scale a little in exchange: a
    strong model scores ~0.83 where the published method gives ~0.91. That is
    a real cost and the reason both are reported.

    Returns NaN when the index is undefined - including under perfect
    separation, where no window holds both a presence and a background point.
    Both implementations agree on that, and it is the honest answer rather
    than a failure.
    """
    pres = sorted(float(s) for s in presence_scores)
    back = sorted(float(s) for s in background_scores)
    if not pres or not back:
        return NAN
    if not (math.isfinite(pres[0]) and math.isfinite(pres[-1])
            and math.isfinite(back[0]) and math.isfinite(back[-1])):
        return NAN

    half = window_width_frac / 2.0
    centres, ratios = [], []
    for i in range(n_windows):
        q = i / max(n_windows - 1, 1)
        lo = _quantile(back, max(0.0, q - half))
        hi = _quantile(back, min(1.0, q + half))
        if hi <= lo:
            continue
        e = sum(1 for s in back if lo <= s <= hi)
        if e < min_background:
            continue
        p = sum(1 for s in pres if lo <= s <= hi)
        centres.append(q)
        ratios.append((p / len(pres)) / (e / len(back)))

    if len(centres) < 3:
        return NAN
    return spearman(centres, ratios)


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted list."""
    if not sorted_values:
        return NAN
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def mean_ignoring_nan(values: list[float]) -> float:
    vals = [v for v in values if not math.isnan(v)]
    return sum(vals) / len(vals) if vals else NAN


def stdev_ignoring_nan(values: list[float]) -> float:
    vals = [v for v in values if not math.isnan(v)]
    if len(vals) < 2:
        return NAN
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))
