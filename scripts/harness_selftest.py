"""Does the validation harness actually catch the thing it exists to catch?

Run before trusting any real result. Two synthetic scenarios with known truth:

  A. NO ECOLOGY. Presences are spatially clustered but depend on nothing we
     could ever measure. The model is given coordinates only, so the best it
     can do is memorise locations. Random CV should look impressive. Spatial
     block CV should collapse to roughly 0.5.

  B. REAL SIGNAL. Presences depend on a genuine environmental variable, and
     the model is given that variable. Both schemes should score well.

A is the guard against fooling ourselves. B is the guard against the opposite
mistake - a validation scheme so severe that nothing survives it, which would
make a real signal look like no signal.

If A does not collapse under blocking, the blocking is broken. If B collapses,
the blocking is too aggressive for the autocorrelation range and the result
would be a false negative.

    python scripts/harness_selftest.py
"""
from __future__ import annotations

import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from src.geometry import Boundary
from src.validate.autocorrelation import (
    UniformSampler,
    decay_range,
    pair_correlation,
)
from src.validate.harness import compare_schemes, inflation_report, spatial_block_cv
from src.validate.metrics import roc_auc

EXTENT_M = 100_000.0   # 100 km square, comparable to Agder
BLOCK_M = 7_500.0      # the configured default
N_PRESENCE = 400       # comparable to the real thinned counts
N_BACKGROUND = 4_000


def knn_model(k: int = 25):
    """Nearest-neighbour classifier over whatever features it is handed.

    Deliberately over-flexible. A model like this memorises rather than
    generalises, which is precisely the behaviour spatial blocking is supposed
    to expose when the features are coordinates.
    """

    def fit_predict(X_tr, y_tr, X_te):
        out = []
        for xt in X_te:
            best = []
            for xr, yr in zip(X_tr, y_tr):
                d = math.dist(xt, xr)
                if len(best) < k:
                    best.append((d, yr))
                    best.sort()
                elif d < best[-1][0]:
                    best[-1] = (d, yr)
                    best.sort()
            out.append(sum(v for _, v in best) / len(best))
        return out

    return fit_predict


def env_field(x: float, y: float) -> float:
    """A smooth environmental surface with an ~8 km wavelength.

    Short enough that every 7.5 km block spans most of the gradient, which is
    what lets a genuine relationship survive blocking. Real terrain wetness
    behaves roughly like this; real climate does not, and that difference is
    why blocked CV is harder on climate-driven models.
    """
    return (
        math.sin(x / 1300.0) * math.cos(y / 1500.0)
        + 0.5 * math.sin((x + y) / 900.0)
    )


def scenario_no_ecology(seed: int = 0):
    """Presences clustered around arbitrary centres. Features: coordinates."""
    rng = random.Random(seed)
    centres = [(rng.uniform(0, EXTENT_M), rng.uniform(0, EXTENT_M)) for _ in range(40)]
    coords, y = [], []
    for _ in range(N_PRESENCE):
        cx, cy = rng.choice(centres)
        coords.append((rng.gauss(cx, 1200.0), rng.gauss(cy, 1200.0)))
        y.append(1)
    for _ in range(N_BACKGROUND):
        coords.append((rng.uniform(0, EXTENT_M), rng.uniform(0, EXTENT_M)))
        y.append(0)
    X = [[c[0], c[1]] for c in coords]   # coordinates as the only features
    return coords, y, X


def scenario_real_signal(seed: int = 0):
    """Presences driven by the environmental field. Features: that field."""
    rng = random.Random(seed + 100)
    coords, y = [], []
    made = 0
    while made < N_PRESENCE:
        x, yy = rng.uniform(0, EXTENT_M), rng.uniform(0, EXTENT_M)
        p = 1.0 / (1.0 + math.exp(-6.0 * env_field(x, yy)))
        if rng.random() < p:
            coords.append((x, yy))
            y.append(1)
            made += 1
    for _ in range(N_BACKGROUND):
        coords.append((rng.uniform(0, EXTENT_M), rng.uniform(0, EXTENT_M)))
        y.append(0)
    X = [[env_field(*c)] for c in coords]
    return coords, y, X


BUFFERS = [0.0, 1000.0, 2000.0, 4000.0, 6000.0]
# The buffer used for scenario B. Note this is the buffer appropriate to *this
# synthetic scenario*, whose clumps are 1.2 km sd. The real configured buffer
# lives in config/study_area.yaml and is chosen on a cost trade-off, because
# the real records have no autocorrelation range to derive one from - see
# data/processed/autocorrelation_range.md.
BUFFER_CHOSEN = 4000.0
# Leakage at or below this counts as eliminated.
LEAKAGE_TOLERANCE = 0.02


def main():
    model = knn_model()
    verdicts = []

    print("=" * 72)
    print("A. NO ECOLOGY - presences clustered, model sees coordinates only")
    print("   The true AUC is 0.5 by construction. Anything above that is leakage.")
    print("=" * 72)
    coords, y, X = scenario_no_ecology()
    _, naive_a = compare_schemes(coords, y, X, model, BLOCK_M, n_folds=5, seed=0)
    print(f"  random CV (UNTRUSTWORTHY, shown as contrast): AUC {naive_a.mean_auc:.3f}")
    print()
    print("  buffer    blocked AUC   leakage above 0.5")
    print("  " + "-" * 44)
    blocked_by_buffer = {}
    for buf in BUFFERS:
        r = spatial_block_cv(coords, y, X, model, BLOCK_M, n_folds=5, seed=0, buffer_m=buf)
        blocked_by_buffer[buf] = r.mean_auc
        print(f"  {buf:6.0f} m   {r.mean_auc:9.3f}   {r.mean_auc - 0.5:+.3f}")

    chosen = BUFFER_CHOSEN
    caught = blocked_by_buffer[chosen] < 0.60 and naive_a.mean_auc > 0.70
    verdicts.append(
        (f"A: at a {chosen:.0f} m buffer, blocking exposes location memorisation", caught)
    )

    print()
    print("=" * 72)
    print("B. REAL SIGNAL - presences driven by an environmental variable")
    print("   Expect: signal survives blocking; otherwise we would call a real")
    print("   result a null one.")
    print("=" * 72)
    coords, y, X = scenario_real_signal()

    # The ceiling matters. Presence is probabilistic, so even a model with
    # perfect knowledge of the driving variable cannot reach AUC 1.0. Scoring
    # the blocked result against an absolute number would fail a harness that
    # was working correctly - judge it against what is actually achievable.
    ceiling = roc_auc(y, [x[0] for x in X])
    blocked_b, naive_b = compare_schemes(
        coords, y, X, model, BLOCK_M, n_folds=5, seed=0, buffer_m=chosen
    )
    print(inflation_report(blocked_b, naive_b))
    recovered = (blocked_b.mean_auc - 0.5) / (ceiling - 0.5)
    print()
    print(f"  perfect-knowledge ceiling for this scenario: AUC {ceiling:.3f}")
    print(f"  blocked CV recovers {recovered:.0%} of the achievable signal")

    survives = recovered > 0.70
    verdicts.append(("B: blocking preserves a genuine signal", survives))

    print()
    print("=" * 72)
    print("C. RANGE ESTIMATOR - does it recommend a buffer that actually works?")
    print("   Pointed at scenario A, where clumps are 1.2 km sd by construction.")
    print("=" * 72)
    coords, y, X = scenario_no_ecology()
    presences = [c for c, lab in zip(coords, y) if lab == 1]
    square = Boundary({
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [EXTENT_M, 0.0], [EXTENT_M, EXTENT_M],
                         [0.0, EXTENT_M], [0.0, 0.0]]],
    })
    pc = pair_correlation(
        presences, UniformSampler(square, cell_m=1000.0),
        max_r_m=30000.0, bin_m=500.0, n_sim=39, seed=0,
    )
    est = decay_range(pc)
    print(f"  estimated range: {est:.0f} m")
    if math.isnan(est):
        print("  estimator returned no range on data that has one")
        verdicts.append(("C: the range estimator recovers a known clustering scale", False))
    else:
        r = spatial_block_cv(coords, y, X, model, BLOCK_M, n_folds=5, seed=0, buffer_m=est)
        leak = r.mean_auc - 0.5
        print(f"  leakage at that buffer: {leak:+.3f} (tolerance {LEAKAGE_TOLERANCE:+.3f})")
        print("  A buffer derived from the estimate must remove the leakage the")
        print("  buffer sweep in A shows is there. If it does not, the estimator")
        print("  is not fit to set the buffer on any dataset.")
        verdicts.append(
            ("C: the range estimator recovers a known clustering scale",
             leak <= LEAKAGE_TOLERANCE)
        )

    print()
    print("=" * 72)
    print("SELF-TEST VERDICT")
    print("=" * 72)
    for label, ok in verdicts:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if all(ok for _, ok in verdicts):
        print("\nThe harness distinguishes memorisation from signal. Fit for use.")
        return 0
    print("\nHarness is NOT fit for use. Do not run or interpret a real model.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
