"""Cross-validation harness. Written before the first model, deliberately.

The harness takes any `fit_predict(X_train, y_train, X_test) -> scores`
callable, so it can be exercised against synthetic models with known behaviour
long before a real one exists. That is the point: we want to know the yardstick
works before we care about the answer.

Two schemes are provided. They are not equals:

* `spatial_block_cv` withholds whole regions. This is the only scheme whose
  numbers may be reported as results.
* `random_cv_untrustworthy` withholds random points. Its numbers are
  contaminated by spatial autocorrelation and it exists solely as a contrast -
  the size of the gap between the two is itself diagnostic. Every result it
  produces carries `trustworthy=False` and prints a banner saying so. There is
  deliberately no way to turn that off.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .blocks import assign_blocks, fold_adequacy_warnings, make_folds
from .metrics import (
    continuous_boyce,
    continuous_boyce_quantile,
    mean_ignoring_nan,
    roc_auc,
    stdev_ignoring_nan,
)

FitPredict = Callable[[list, list, list], list]


@dataclass
class FoldResult:
    fold: int
    n_train: int
    n_test: int
    n_train_presence: int
    n_test_presence: int
    auc: float
    boyce: float
    # Reported alongside `boyce`, never instead of it. The published equal-width
    # index is biased to about -0.42 for a skill-free model on scores with this
    # skew; see scripts/check_boyce.py. Carrying both is what stops either
    # number being read alone.
    boyce_q: float


@dataclass
class CVResult:
    scheme: str
    trustworthy: bool
    folds: list[FoldResult]
    n_folds: int
    block_size_m: float | None = None
    buffer_m: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def mean_auc(self) -> float:
        return mean_ignoring_nan([f.auc for f in self.folds])

    @property
    def sd_auc(self) -> float:
        return stdev_ignoring_nan([f.auc for f in self.folds])

    @property
    def mean_boyce(self) -> float:
        return mean_ignoring_nan([f.boyce for f in self.folds])

    @property
    def sd_boyce(self) -> float:
        return stdev_ignoring_nan([f.boyce for f in self.folds])

    @property
    def mean_boyce_q(self) -> float:
        return mean_ignoring_nan([f.boyce_q for f in self.folds])

    @property
    def sd_boyce_q(self) -> float:
        return stdev_ignoring_nan([f.boyce_q for f in self.folds])

    def report(self) -> str:
        lines = []
        if not self.trustworthy:
            lines += [
                "!" * 72,
                "!! UNTRUSTWORTHY - RANDOM CROSS-VALIDATION ON AUTOCORRELATED DATA",
                "!! These numbers are inflated by spatial autocorrelation and must",
                "!! NOT be reported as a result. Shown only as a contrast against",
                "!! the spatially blocked figures.",
                "!" * 72,
            ]
        head = self.scheme
        if self.block_size_m:
            buf = f", {self.buffer_m:.0f} m buffer" if self.buffer_m else ", no buffer"
            head += f" ({self.block_size_m:.0f} m blocks, {self.n_folds} folds{buf})"
        lines.append(head)
        lines.append(f"  AUC   {self.mean_auc:.3f} +/- {self.sd_auc:.3f}")
        lines.append(f"  Boyce {self.mean_boyce:.3f} +/- {self.sd_boyce:.3f} "
                     f"(published, zero point biased low)")
        lines.append(f"  Boyce {self.mean_boyce_q:.3f} +/- {self.sd_boyce_q:.3f} "
                     f"(quantile windows)")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)


def _evaluate_fold(fold: int, y_tr, y_te, scores) -> FoldResult:
    pres = [s for s, label in zip(scores, y_te) if label == 1]
    back = [s for s, label in zip(scores, y_te) if label != 1]
    return FoldResult(
        fold=fold,
        n_train=len(y_tr),
        n_test=len(y_te),
        n_train_presence=sum(1 for v in y_tr if v == 1),
        n_test_presence=len(pres),
        auc=roc_auc(list(y_te), list(scores)),
        boyce=continuous_boyce(pres, back),
        boyce_q=continuous_boyce_quantile(pres, back),
    )


def _excluded_by_buffer(
    coords: list[tuple[float, float]],
    train_idx: list[int],
    test_idx: list[int],
    buffer_m: float,
) -> set[int]:
    """Training indices lying within buffer_m of any test point.

    Blocking alone does not stop leakage: a test block sits directly against
    training blocks, so a cluster straddling that edge is partly in both sets
    and points metres apart end up on opposite sides of the split. Measured on
    synthetic data with no ecology whatsoever, unbuffered 7.5 km blocking still
    returned AUC 0.73 - see scripts/harness_selftest.py.

    Uses a grid hash at the buffer scale, so each training point is compared
    only against test points in the nine surrounding cells.
    """
    if buffer_m <= 0:
        return set()
    cell = buffer_m
    grid: dict[tuple[int, int], list[int]] = {}
    for i in test_idx:
        e, n = coords[i]
        grid.setdefault((int(e // cell), int(n // cell)), []).append(i)

    excluded = set()
    for i in train_idx:
        e, n = coords[i]
        gx, gy = int(e // cell), int(n // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):  # noqa: B007
                    te, tn = coords[j]
                    if (e - te) ** 2 + (n - tn) ** 2 <= buffer_m * buffer_m:
                        excluded.add(i)
                        break
                if i in excluded:
                    break
            if i in excluded:
                break
    return excluded


def _run(
    fold_of_point: Sequence[int],
    n_folds: int,
    X,
    y,
    fit_predict,
    coords: list[tuple[float, float]] | None = None,
    buffer_m: float = 0.0,
) -> tuple[list[FoldResult], int]:
    results = []
    total_buffered = 0
    for f in range(n_folds):
        tr = [i for i, g in enumerate(fold_of_point) if g != f]
        te = [i for i, g in enumerate(fold_of_point) if g == f]
        if buffer_m > 0 and coords is not None:
            dropped = _excluded_by_buffer(coords, tr, te, buffer_m)
            total_buffered += len(dropped)
            tr = [i for i in tr if i not in dropped]
        if not te or not tr:
            continue
        X_tr = [X[i] for i in tr]
        y_tr = [y[i] for i in tr]
        X_te = [X[i] for i in te]
        y_te = [y[i] for i in te]
        scores = fit_predict(X_tr, y_tr, X_te)
        if len(scores) != len(y_te):
            raise ValueError(
                f"fit_predict returned {len(scores)} scores for {len(y_te)} test rows"
            )
        results.append(_evaluate_fold(f, y_tr, y_te, scores))
    return results, total_buffered


def spatial_block_cv(
    coords: list[tuple[float, float]],
    y: list[int],
    X: list,
    fit_predict: FitPredict,
    block_size_m: float,
    n_folds: int = 5,
    seed: int = 0,
    min_presences: int = 10,
    buffer_m: float = 0.0,
) -> CVResult:
    """The only scheme whose output may be reported as a result.

    `buffer_m` drops training points within that distance of any test point.
    Blocking alone leaves edge leakage; see `_excluded_by_buffer`. A buffer of
    zero is permitted for sensitivity analysis but warns, because the
    unbuffered number is optimistic by a margin we have measured.
    """
    if not (5000 <= block_size_m <= 10000):
        # Not fatal - sensitivity analysis needs to range wider - but it must
        # never pass silently, because the mandated range is 5-10 km.
        extra = [
            f"block size {block_size_m:.0f} m is outside the mandated 5-10 km range; "
            f"report this alongside the number or use it only for sensitivity analysis"
        ]
    else:
        extra = []

    if buffer_m <= 0:
        extra.append(
            "no exclusion buffer: on synthetic data with no ecology, unbuffered "
            "7.5 km blocking still returned AUC 0.73 from edge leakage alone"
        )

    blocks = assign_blocks(coords, block_size_m)
    fa = make_folds(blocks, y, n_folds=n_folds, block_size_m=block_size_m, seed=seed)
    folds, n_buffered = _run(
        fa.fold_of_point, n_folds, X, y, fit_predict, coords=coords, buffer_m=buffer_m
    )
    if n_buffered:
        extra.append(
            f"{n_buffered} training rows dropped across folds by the {buffer_m:.0f} m buffer"
        )
    return CVResult(
        scheme="spatial block CV",
        trustworthy=True,
        folds=folds,
        n_folds=n_folds,
        block_size_m=block_size_m,
        buffer_m=buffer_m,
        warnings=extra + fold_adequacy_warnings(fa, min_presences=min_presences),
    )


def random_cv_untrustworthy(
    coords: list[tuple[float, float]],
    y: list[int],
    X: list,
    fit_predict: FitPredict,
    n_folds: int = 5,
    seed: int = 0,
) -> CVResult:
    """Random k-fold. Contaminated by autocorrelation. Contrast only.

    `coords` is unused and required anyway, so that calling this never looks
    like a lighter-weight alternative to the blocked version.
    """
    rng = random.Random(seed)
    idx = list(range(len(y)))
    rng.shuffle(idx)
    fold_of_point = [0] * len(y)
    for rank, i in enumerate(idx):
        fold_of_point[i] = rank % n_folds
    folds, _ = _run(fold_of_point, n_folds, X, y, fit_predict)
    return CVResult(
        scheme="random CV (UNTRUSTWORTHY)",
        trustworthy=False,
        folds=folds,
        n_folds=n_folds,
        warnings=["random splits on spatially autocorrelated data; inflated by construction"],
    )


def compare_schemes(
    coords: list[tuple[float, float]],
    y: list[int],
    X: list,
    fit_predict: FitPredict,
    block_size_m: float,
    n_folds: int = 5,
    seed: int = 0,
    buffer_m: float = 0.0,
) -> tuple[CVResult, CVResult]:
    """Run both schemes. The gap between them is the leakage estimate.

    CLAUDE.md hard constraint 2: never present a metric without its spatial-CV
    counterpart. Returning them as a pair makes the honest comparison the
    path of least resistance.
    """
    blocked = spatial_block_cv(
        coords, y, X, fit_predict, block_size_m, n_folds, seed, buffer_m=buffer_m
    )
    naive = random_cv_untrustworthy(coords, y, X, fit_predict, n_folds, seed)
    return blocked, naive


def inflation_report(blocked: CVResult, naive: CVResult) -> str:
    gap = naive.mean_auc - blocked.mean_auc
    lines = [
        blocked.report(),
        "",
        naive.report(),
        "",
        f"AUC inflation from random splitting: {gap:+.3f}",
    ]
    if gap > 0.05:
        lines.append(
            "  -> Large gap. This is what hard spatial autocorrelation looks like, and"
        )
        lines.append(
            "     it is exactly why the random number must never be quoted."
        )
    return "\n".join(lines)


def buffer_cost(
    coords: list[tuple[float, float]],
    y: list[int],
    fold_of_point: Sequence[int],
    n_folds: int,
    buffer_m: float,
) -> dict:
    """What a given exclusion buffer costs, without fitting anything.

    The buffer trades leakage against sample size and both sides have to be
    reported. Returns totals summed across folds plus the worst single fold,
    because a mean that looks affordable can still hide one fold trained on
    almost nothing.
    """
    rows_dropped = 0
    presences_dropped = 0
    train_presences = []
    for f in range(n_folds):
        tr = [i for i, g in enumerate(fold_of_point) if g != f]
        te = [i for i, g in enumerate(fold_of_point) if g == f]
        dropped = _excluded_by_buffer(coords, tr, te, buffer_m)
        rows_dropped += len(dropped)
        presences_dropped += sum(1 for i in dropped if y[i] == 1)
        kept = [i for i in tr if i not in dropped]
        train_presences.append(sum(1 for i in kept if y[i] == 1))
    total_presence = sum(1 for v in y if v == 1)
    return {
        "buffer_m": buffer_m,
        "rows_dropped": rows_dropped,
        "presences_dropped": presences_dropped,
        "min_train_presences": min(train_presences) if train_presences else 0,
        "mean_train_presences": (
            sum(train_presences) / len(train_presences) if train_presences else 0.0
        ),
        "presence_retention": (
            1.0 - presences_dropped / (total_presence * n_folds)
            if total_presence and n_folds
            else float("nan")
        ),
    }


@dataclass
class TransferResult:
    """One model, fitted on the training extent and scored once elsewhere.

    Deliberately not a `CVResult`. There are no folds here, so there is no
    fold-to-fold spread, and reporting "+/- 0.000" for a single evaluation
    would assert a precision that does not exist. The uncertainty comes from a
    bootstrap over the test points instead, which is the honest interval for
    one split.
    """
    scheme: str
    trustworthy: bool
    auc: float
    boyce: float
    boyce_q: float
    auc_lo: float
    auc_hi: float
    n_train: int
    n_test: int
    n_train_presence: int
    n_test_presence: int
    n_buffered: int
    buffer_m: float
    warnings: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            self.scheme,
            f"  AUC   {self.auc:.3f}  (bootstrap 95% CI {self.auc_lo:.3f}-{self.auc_hi:.3f})",
            f"  Boyce {self.boyce:+.3f} (published)  "
            f"{self.boyce_q:+.3f} (quantile windows)",
            f"  train {self.n_train_presence} presences / {self.n_train} rows;  "
            f"test {self.n_test_presence} presences / {self.n_test} rows",
        ]
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)


def _bootstrap_auc_ci(y_te: list[int], scores: list[float], n_boot: int = 1000,
                      seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap interval for a single AUC.

    Presences and background are resampled separately, each with replacement
    and at their observed size. Resampling the pooled set instead would let the
    presence count wander between replicates and would widen the interval for a
    reason that has nothing to do with the model.

    This interval covers sampling variation in the test points only. It says
    nothing about whether those points are a fair sample of the extent, which
    on effort-biased occurrence data is the larger worry and is not something a
    bootstrap can reach.
    """
    pres = [s for s, label in zip(scores, y_te) if label == 1]
    back = [s for s, label in zip(scores, y_te) if label != 1]
    if not pres or not back:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    draws = []
    for _ in range(n_boot):
        p = [pres[rng.randrange(len(pres))] for _ in range(len(pres))]
        b = [back[rng.randrange(len(back))] for _ in range(len(back))]
        draws.append(roc_auc([1] * len(p) + [0] * len(b), p + b))
    draws = sorted(v for v in draws if not (v != v))     # drop NaN
    if not draws:
        return (float("nan"), float("nan"))
    lo = draws[max(0, int(alpha / 2 * len(draws)) - 1)]
    hi = draws[min(len(draws) - 1, int((1 - alpha / 2) * len(draws)))]
    return (lo, hi)


def held_out_extent(
    coords: list[tuple[float, float]],
    y: list[int],
    X: list,
    fit_predict: FitPredict,
    in_test: Sequence[bool],
    buffer_m: float = 0.0,
    min_test_presences: int = 20,
    seed: int = 0,
) -> TransferResult:
    """Fit on everything outside the held-out extent, score once inside it.

    This is the transferability test CLAUDE.md actually asks for: a model
    fitted regionally, evaluated on a smaller area it never saw. It is not a
    cross-validation and must not be presented as one - there is a single test
    set and a single number.

    The held-out extent is removed from training *and* so is everything within
    `buffer_m` of a test point. Without that ring the test area's immediate
    surroundings sit in training, and on data whose spatial dependence declines
    smoothly to 30 km without a floor (see data/processed/autocorrelation_range.md)
    that is a straightforward leak across the boundary.
    """
    if len(in_test) != len(y):
        raise ValueError(f"in_test has {len(in_test)} entries for {len(y)} rows")

    te = [i for i, flag in enumerate(in_test) if flag]
    tr = [i for i, flag in enumerate(in_test) if not flag]
    if not te:
        raise ValueError("held-out extent contains no points")
    if not tr:
        raise ValueError("nothing left to train on once the extent is held out")

    warnings: list[str] = []
    n_buffered = 0
    if buffer_m > 0:
        dropped = _excluded_by_buffer(coords, tr, te, buffer_m)
        n_buffered = len(dropped)
        tr = [i for i in tr if i not in dropped]
        if not tr:
            raise ValueError(f"the {buffer_m:.0f} m buffer removed the entire training set")
    else:
        warnings.append(
            "no exclusion buffer around the held-out extent: training points "
            "immediately outside the boundary are metres from test points"
        )

    n_test_presence = sum(1 for i in te if y[i] == 1)
    n_train_presence = sum(1 for i in tr if y[i] == 1)
    if n_test_presence < min_test_presences:
        warnings.append(
            f"only {n_test_presence} presences in the held-out extent; a single "
            f"AUC on this few is a wide interval, not a measurement"
        )
    if n_test_presence == 0 or n_test_presence == len(te):
        warnings.append("the held-out extent has only one class; AUC is undefined")

    scores = fit_predict([X[i] for i in tr], [y[i] for i in tr], [X[i] for i in te])
    if len(scores) != len(te):
        raise ValueError(f"fit_predict returned {len(scores)} scores for {len(te)} test rows")

    y_te = [y[i] for i in te]
    pres = [s for s, label in zip(scores, y_te) if label == 1]
    back = [s for s, label in zip(scores, y_te) if label != 1]
    lo, hi = _bootstrap_auc_ci(y_te, list(scores), seed=seed)

    return TransferResult(
        scheme="held-out extent (transferability)",
        trustworthy=True,
        auc=roc_auc(y_te, list(scores)),
        boyce=continuous_boyce(pres, back),
        boyce_q=continuous_boyce_quantile(pres, back),
        auc_lo=lo,
        auc_hi=hi,
        n_train=len(tr),
        n_test=len(te),
        n_train_presence=n_train_presence,
        n_test_presence=n_test_presence,
        n_buffered=n_buffered,
        buffer_m=buffer_m,
        warnings=warnings,
    )
