"""How far apart must two records be before one says nothing about the other?

The exclusion buffer in the CV harness was set from a synthetic cluster scale,
which is evidence about the self-test and nothing else. This module derives the
number from the real occurrence data.

The statistic is the pair correlation function g(r): the density of record
pairs separated by roughly r, divided by the density expected if the same
number of records were scattered uniformly across the same region. g(r) >> 1 at
short range means records come in clumps. The distance at which g(r) settles to
1 is the range beyond which one record's position carries no information about
another's - which is exactly the distance a test point must be held away from
the training set.

g(r) is used rather than Ripley's K because K is cumulative: strong clustering
at 500 m keeps K elevated at 20 km, so K cannot locate a range. g uses annuli
and falls back to 1 once the clustering stops.

Two deliberate choices:

* The null is uniform over the region, so the estimated range absorbs clumping
  from *both* ecology and recording effort. That is what the buffer has to
  defeat. Separating the two causes is a different question, and the one
  target-group background sampling exists to answer.
* The null is evaluated by simulation inside the real boundary rather than
  against the analytic pi*r^2, so edge effects are carried by the null instead
  of being corrected for in the estimator.
"""
from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass

from ..geometry import Boundary, cells_inside


@dataclass
class PairCorrelation:
    """Result of a pair-correlation estimate. Distances in metres."""

    edges_m: list[float]
    observed: list[int]
    expected: list[float]
    envelope_lo: list[float]
    envelope_hi: list[float]
    n_points: int
    n_sim: int

    @property
    def centres_m(self) -> list[float]:
        return [(self.edges_m[i] + self.edges_m[i + 1]) / 2.0 for i in range(len(self.observed))]

    @property
    def g(self) -> list[float]:
        return [
            (o / e) if e > 0 else float("nan")
            for o, e in zip(self.observed, self.expected)
        ]


def pair_counts(coords: list[tuple[float, float]], edges_m: list[float]) -> list[int]:
    """Number of unordered point pairs falling in each distance annulus.

    Brute force over pairs, binned on squared distance so no square roots are
    taken. Fine for the hundreds of thinned presences we have; it is O(n^2) and
    would need a grid hash before being pointed at a full background set.
    """
    sq = [e * e for e in edges_m]
    max_sq = sq[-1]
    counts = [0] * (len(edges_m) - 1)
    n = len(coords)
    for i in range(n):
        xi, yi = coords[i]
        for j in range(i + 1, n):
            xj, yj = coords[j]
            d2 = (xi - xj) ** 2 + (yi - yj) ** 2
            if d2 >= max_sq:
                continue
            counts[bisect_right(sq, d2) - 1] += 1
    return counts


def nearest_neighbour_distances(coords: list[tuple[float, float]]) -> list[float]:
    """Distance from each point to its closest other point.

    Descriptive companion to g(r): it says how tightly packed the records are,
    and therefore how much data a given buffer will cost.
    """
    out = []
    for i, (xi, yi) in enumerate(coords):
        best = math.inf
        for j, (xj, yj) in enumerate(coords):
            if i == j:
                continue
            d2 = (xi - xj) ** 2 + (yi - yj) ** 2
            if d2 < best:
                best = d2
        out.append(math.sqrt(best) if best < math.inf else math.nan)
    return out


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 100]. NaNs dropped."""
    vals = sorted(v for v in values if not math.isnan(v))
    if not vals:
        return math.nan
    if len(vals) == 1:
        return vals[0]
    pos = (q / 100.0) * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


class UniformSampler:
    """Draws points uniformly from a region, via its cell decomposition.

    Rejection sampling against a real administrative boundary is far too slow -
    every trial costs a full point-in-polygon test. The boundary is rasterised
    once instead, and a draw picks a cell and jitters inside it. The region is
    therefore approximated at cell_m resolution.
    """

    def __init__(self, boundary: Boundary, cell_m: float = 1000.0):
        self.cells = cells_inside(boundary, cell_m)
        if not self.cells:
            raise ValueError(
                f"no cells of {cell_m:.0f} m fall inside the boundary; "
                f"the cell size is too coarse for this region"
            )
        self.cell_m = cell_m

    def sample(self, n: int, rng: random.Random) -> list[tuple[float, float]]:
        h = self.cell_m / 2.0
        out = []
        for _ in range(n):
            cx, cy = self.cells[rng.randrange(len(self.cells))]
            out.append((cx + rng.uniform(-h, h), cy + rng.uniform(-h, h)))
        return out


def pair_correlation(
    coords: list[tuple[float, float]],
    sampler: UniformSampler,
    max_r_m: float = 30000.0,
    bin_m: float = 500.0,
    n_sim: int = 39,
    seed: int = 0,
) -> PairCorrelation:
    """Estimate g(r) against a simulated uniform null in the same region.

    39 simulations gives a pointwise two-sided envelope at alpha = 2/(39+1) =
    0.05 using the simulated minimum and maximum. Pointwise: it is not a
    simultaneous band across all distances, so a single bin poking outside the
    envelope is not on its own evidence of anything.
    """
    if n_sim < 5:
        raise ValueError("n_sim must be at least 5 for an envelope to mean anything")
    n_bins = int(round(max_r_m / bin_m))
    edges = [i * bin_m for i in range(n_bins + 1)]

    observed = pair_counts(coords, edges)

    rng = random.Random(seed)
    sims = [pair_counts(sampler.sample(len(coords), rng), edges) for _ in range(n_sim)]

    expected, lo, hi = [], [], []
    for b in range(n_bins):
        col = [s[b] for s in sims]
        expected.append(sum(col) / len(col))
        lo.append(float(min(col)))
        hi.append(float(max(col)))

    return PairCorrelation(
        edges_m=edges,
        observed=observed,
        expected=expected,
        envelope_lo=lo,
        envelope_hi=hi,
        n_points=len(coords),
        n_sim=n_sim,
    )


def _clustered_flags(pc: PairCorrelation, criterion: str, ratio: float) -> list[bool]:
    g = pc.g
    if criterion == "envelope":
        return [o > h for o, h in zip(pc.observed, pc.envelope_hi)]
    if criterion == "ratio":
        return [(not math.isnan(v)) and v > ratio for v in g]
    raise ValueError(f"unknown criterion: {criterion!r}")


def clustering_range(
    pc: PairCorrelation,
    criterion: str = "envelope",
    ratio: float = 1.2,
    persistence_bins: int = 6,
) -> float:
    """Distance beyond which clustering has stopped and stays stopped.

    "Stays stopped" is the whole point. Taking the first bin where g dips to 1
    would report a range from a single noisy bin, and taking the last clustered
    bin anywhere in the curve would let one chance exceedance at 25 km set the
    buffer. So the range is the start of the first run of `persistence_bins`
    consecutive unclustered bins.

    criterion:
      "envelope" - clustered while the observed count exceeds the simulated
                   maximum. Adapts to the region and the sample size, but the
                   envelope is pointwise, so isolated exceedances are expected.
      "ratio"    - clustered while g exceeds `ratio`. Insensitive to the
                   simulation count, sensitive to an arbitrary threshold.

    Returns NaN when the curve never settles within max_r_m, which means the
    estimate was run over too short a distance to contain the range. That is a
    stop-and-report, not a number to round down.
    """
    flags = _clustered_flags(pc, criterion, ratio)
    n = len(flags)
    if persistence_bins < 1:
        raise ValueError("persistence_bins must be at least 1")
    for b in range(n - persistence_bins + 1):
        if not any(flags[b : b + persistence_bins]):
            return pc.edges_m[b]
    return math.nan


def far_field_baseline(pc: PairCorrelation, outer_frac: float = 0.34) -> float:
    """Median g over the outermost stretch of the curve.

    g does not have to settle at 1.0. Records concentrate near towns across the
    whole of Agder, so the pattern is inhomogeneous at regional scale and g
    stays above 1 out to tens of kilometres. That regional gradient is what
    blocking and target-group background sampling exist to handle; it is not
    what the buffer is for, and treating it as buffer distance would demand an
    exclusion radius wider than the folds themselves.
    """
    n = len(pc.observed)
    start = max(0, int(n * (1.0 - outer_frac)))
    tail = [v for v in pc.g[start:] if not math.isnan(v)]
    if not tail:
        return math.nan
    return percentile(tail, 50)


def far_field_reached(pc: PairCorrelation, outer_frac: float = 0.34, tol: float = 0.8) -> bool:
    """Has g stopped falling by the time the curve ends?

    If the outermost stretch is still meaningfully below the stretch before it,
    the curve is mid-decline and whatever it looks like at the last bin is not a
    baseline. Estimating a range against it silently understates the answer,
    which is worse than refusing.

    This is what the uniform null does on Agder: g falls from 3.8 at 20 km to
    1.7 at 30 km and is still going. There is no far field within the distance
    we can estimate, and the honest report is that the question was asked the
    wrong way.
    """
    g = pc.g
    n = len(g)
    outer = [v for v in g[int(n * (1.0 - outer_frac)):] if not math.isnan(v)]
    middle = [
        v for v in g[int(n * (1.0 - 2 * outer_frac)):int(n * (1.0 - outer_frac))]
        if not math.isnan(v)
    ]
    if not outer or not middle:
        return False
    return percentile(outer, 50) >= tol * percentile(middle, 50)


def decay_range(
    pc: PairCorrelation,
    frac: float = 0.05,
    peak_bins: int = 3,
    persistence_bins: int = 6,
    outer_frac: float = 0.34,
) -> float:
    """Distance at which local clumping has decayed into the regional baseline.

    Measures the excess of g over its far-field level rather than over 1.0, and
    returns where that excess has fallen to `frac` of its short-range peak and
    stayed there. This is the criterion the exclusion buffer is derived from: it
    isolates the clump a test point sits in from the regional gradient, which
    blocking and target-group background sampling are the tools for.

    `frac` is calibrated, once, against the self-test scenario whose answer is
    known: presences in 1.2 km-sd clumps, where the buffer sweep showed leakage
    surviving at 3 km and gone by 4 km. A 10% threshold returns 3500 m there and
    would under-protect; 5% returns 4000 m and matches. It is fixed at that value
    and applied unchanged to every species - retuning it per species would be
    choosing the buffer that produces the most agreeable AUC.

    The decay threshold is never allowed below the far field's own 90th
    percentile. A curve cannot be called "still clustered" while it is inside its
    own noise, and without that floor a flat curve reports that it never decays.

    Returns 0.0 when there is no clumping above the far-field noise, and NaN when
    the far field was never reached or the excess never decays - both meaning the
    estimate was run over too short a distance to answer.
    """
    g = pc.g
    if not far_field_reached(pc, outer_frac):
        return math.nan

    base = far_field_baseline(pc, outer_frac)
    n = len(g)
    tail = [v for v in g[int(n * (1.0 - outer_frac)):] if not math.isnan(v)]
    if math.isnan(base) or not tail:
        return math.nan
    noise_ceiling = percentile(tail, 90)

    head = [v for v in g[:peak_bins] if not math.isnan(v)]
    if not head:
        return math.nan
    peak = max(head)
    if peak <= noise_ceiling:
        return 0.0
    threshold = max(base + frac * (peak - base), noise_ceiling)

    flags = [(not math.isnan(v)) and v > threshold for v in g]
    for b in range(n - persistence_bins + 1):
        if not any(flags[b : b + persistence_bins]):
            return pc.edges_m[b]
    return math.nan


class IntensitySampler:
    """Draws points following a smoothed version of the records' own density.

    The uniform null answers "are these records clumped?", and for Norwegian
    fungal records the answer is yes at every scale out to tens of kilometres -
    g stays near 4 at 20 km across Agder. Against that null there is no flat
    baseline to decay into, and the estimated range runs away.

    But most of that structure is regional: more records near Kristiansand than
    in the inland valleys. Regional intensity is what target-group background
    sampling exists to correct, and it is not what the exclusion buffer is for -
    a buffer wide enough to span it would be wider than the folds.

    So the null here carries the regional gradient and nothing finer. Intensity
    is estimated by a Gaussian kernel at `bandwidth_m`, which must be well above
    the clump scale being measured, and the resulting g is a comparison against
    "the same regional density, locally unclumped". Where it settles is the
    distance at which local clumping has gone.

    The bandwidth is the assumption. Structure coarser than it is delegated to
    blocking and to background correction; structure finer than it is charged to
    the buffer. Report the range at more than one bandwidth.
    """

    def __init__(
        self,
        boundary: Boundary,
        coords: list[tuple[float, float]],
        cell_m: float = 2000.0,
        bandwidth_m: float = 20000.0,
        floor: float = 0.05,
    ):
        if bandwidth_m <= 0:
            raise ValueError("bandwidth_m must be positive")
        if not coords:
            raise ValueError("no records to estimate intensity from")
        self.cells = cells_inside(boundary, cell_m)
        if not self.cells:
            raise ValueError(f"no cells of {cell_m:.0f} m fall inside the boundary")
        self.cell_m = cell_m
        self.bandwidth_m = bandwidth_m

        two_h2 = 2.0 * bandwidth_m * bandwidth_m
        cut2 = (3.0 * bandwidth_m) ** 2   # beyond 3 sd the kernel adds nothing
        weights = []
        for cx, cy in self.cells:
            acc = 0.0
            for x, y in coords:
                d2 = (cx - x) ** 2 + (cy - y) ** 2
                if d2 < cut2:
                    acc += math.exp(-d2 / two_h2)
            weights.append(acc)

        # A floor keeps the null from being unable to place a point where the
        # species has never been recorded. Without it the null inherits the
        # observed pattern's holes and stops being a null.
        top = max(weights)
        if top <= 0:
            raise ValueError("kernel intensity is zero everywhere; bandwidth too small")
        weights = [w + floor * top for w in weights]

        self.cum = []
        acc = 0.0
        for w in weights:
            acc += w
            self.cum.append(acc)
        self.total = acc

    def sample(self, n: int, rng: random.Random) -> list[tuple[float, float]]:
        from bisect import bisect_left

        h = self.cell_m / 2.0
        out = []
        for _ in range(n):
            i = bisect_left(self.cum, rng.random() * self.total)
            cx, cy = self.cells[min(i, len(self.cells) - 1)]
            out.append((cx + rng.uniform(-h, h), cy + rng.uniform(-h, h)))
        return out


def max_reliable_r(boundary: Boundary, frac: float = 0.25, cap_m: float = 30000.0) -> float:
    """Largest distance at which g can be estimated in this region.

    Beyond roughly a quarter of the region's shorter side, most point pairs at
    that separation cannot exist - both points would have to sit near opposite
    edges - and the estimate is dominated by the shape of the region rather than
    by the pattern. Kristiansand is 45 km across, so estimating out to 30 km
    there produced a far-field g of 0.3: an artefact, not a depletion.
    """
    minx, miny, maxx, maxy = boundary.bbox
    return min(cap_m, frac * min(maxx - minx, maxy - miny))


def excess_remaining(
    pc: PairCorrelation,
    r_m: float,
    peak_bins: int = 3,
    outer_frac: float = 0.34,
) -> float:
    """Share of the short-range clumping excess still present at distance r.

    For data where g settles to a baseline, this reaches 0 at the range and
    `decay_range` is the number to use. For data where it does not settle -
    which is what Norwegian fungal records actually do, declining smoothly from
    g = 8.6 at 1 km to 1.1 at 30 km without ever flattening - there is no range
    to find, and the buffer stops being a threshold to derive and becomes a
    trade-off to choose. This is the quantity that trade-off is made against.

    Measured relative to the far-field level actually observed. Where the curve
    is still falling at the last bin, that level is an upper bound on the true
    asymptote, so the figure understates how much dependence remains. It is a
    floor on the leakage, not an estimate of it.

    Returns 0.0 when there is no excess above the far field to begin with.
    """
    base = far_field_baseline(pc, outer_frac)
    g = pc.g
    head = [v for v in g[:peak_bins] if not math.isnan(v)]
    if math.isnan(base) or not head:
        return math.nan
    peak = max(head)
    if peak <= base:
        return 0.0
    bin_m = pc.edges_m[1] - pc.edges_m[0]
    idx = min(max(int(r_m // bin_m), 0), len(g) - 1)
    if math.isnan(g[idx]):
        return math.nan
    return max(0.0, min(1.0, (g[idx] - base) / (peak - base)))
