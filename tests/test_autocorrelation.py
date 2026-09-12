"""Tests for the autocorrelation-range estimator.

This estimator sets the exclusion buffer, and the buffer decides how much of
every future AUC is real. So it is checked against point patterns whose
clustering scale is known by construction, plus the degenerate cases where it
should refuse to answer rather than guess.
"""
import math
import pathlib
import random
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.geometry import Boundary, cells_inside
from src.validate.autocorrelation import (
    PairCorrelation,
    UniformSampler,
    clustering_range,
    nearest_neighbour_distances,
    pair_correlation,
    pair_counts,
    percentile,
)
from src.validate.blocks import assign_blocks, make_folds
from src.validate.harness import buffer_cost


def _square(size_m: float) -> Boundary:
    return Boundary(
        {
            "type": "Polygon",
            "coordinates": [[[0, 0], [size_m, 0], [size_m, size_m], [0, size_m], [0, 0]]],
        },
        name="test square",
    )


# --- Cell fill ----------------------------------------------------------------


def test_cells_inside_covers_a_square():
    cells = cells_inside(_square(10000.0), 1000.0)
    assert len(cells) == 100
    assert all(0.0 <= x <= 10000.0 and 0.0 <= y <= 10000.0 for x, y in cells)


def test_cells_inside_excludes_a_hole():
    ring = [[0, 0], [10000, 0], [10000, 10000], [0, 10000], [0, 0]]
    hole = [[4000, 4000], [6000, 4000], [6000, 6000], [4000, 6000], [4000, 4000]]
    b = Boundary({"type": "Polygon", "coordinates": [ring, hole]})
    cells = cells_inside(b, 1000.0)
    assert (4500.0, 4500.0) not in cells
    assert (500.0, 500.0) in cells
    assert len(cells) == 100 - 4


def test_cells_inside_agrees_with_point_in_polygon():
    b = _square(6000.0)
    for pt in cells_inside(b, 1000.0):
        assert pt in b


def test_cells_inside_rejects_nonpositive_cell():
    with pytest.raises(ValueError):
        cells_inside(_square(1000.0), 0)


# --- Pair counting ------------------------------------------------------------


def test_pair_counts_known_configuration():
    # Three collinear points at 0, 1000, 3000 -> distances 1000, 2000, 3000.
    coords = [(0.0, 0.0), (1000.0, 0.0), (3000.0, 0.0)]
    edges = [0.0, 1500.0, 2500.0, 3500.0]
    assert pair_counts(coords, edges) == [1, 1, 1]


def test_pair_counts_ignores_pairs_beyond_the_last_edge():
    coords = [(0.0, 0.0), (99999.0, 0.0)]
    assert pair_counts(coords, [0.0, 500.0, 1000.0]) == [0, 0]


def test_pair_counts_counts_each_pair_once():
    coords = [(0.0, 0.0)] * 4
    # Four coincident points: 6 unordered pairs, all at distance 0.
    assert pair_counts(coords, [0.0, 500.0]) == [6]


def test_nearest_neighbour_distances():
    coords = [(0.0, 0.0), (300.0, 0.0), (5000.0, 0.0)]
    nn = nearest_neighbour_distances(coords)
    assert nn == pytest.approx([300.0, 300.0, 4700.0])


def test_percentile_endpoints_and_interpolation():
    assert percentile([1, 2, 3, 4, 5], 0) == 1
    assert percentile([1, 2, 3, 4, 5], 100) == 5
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([0, 10], 25) == pytest.approx(2.5)
    assert math.isnan(percentile([], 50))


# --- Range extraction ---------------------------------------------------------


def _pc(observed, envelope_hi, bin_m=1000.0):
    n = len(observed)
    return PairCorrelation(
        edges_m=[i * bin_m for i in range(n + 1)],
        observed=observed,
        expected=[max(h - 1, 1) for h in envelope_hi],
        envelope_lo=[0.0] * n,
        envelope_hi=[float(h) for h in envelope_hi],
        n_points=10,
        n_sim=39,
    )


def test_range_is_where_clustering_stops():
    # Clustered in the first three bins (0-3 km), flat thereafter.
    pc = _pc([100, 90, 80] + [10] * 10, [20] * 13)
    assert clustering_range(pc, persistence_bins=4) == 3000.0


def test_range_ignores_an_isolated_exceedance_far_out():
    # One noisy bin at 10 km must not drag the range out to 11 km.
    obs = [100, 90, 80] + [10] * 6 + [25] + [10] * 6
    pc = _pc(obs, [20] * 16)
    assert clustering_range(pc, persistence_bins=4) == 3000.0


def test_range_does_not_stop_at_a_single_dip():
    # A dip at bin 1 is not the range; clustering resumes immediately after.
    obs = [100, 5, 90, 80] + [10] * 10
    pc = _pc(obs, [20] * 14)
    assert clustering_range(pc, persistence_bins=4) == 4000.0


def test_unclustered_pattern_has_zero_range():
    pc = _pc([5] * 12, [20] * 12)
    assert clustering_range(pc, persistence_bins=4) == 0.0


def test_range_is_nan_when_clustering_never_settles():
    """Refusing to answer beats rounding an unfinished curve down."""
    pc = _pc([100] * 12, [20] * 12)
    assert math.isnan(clustering_range(pc, persistence_bins=4))


def test_ratio_criterion_uses_g_not_the_envelope():
    pc = PairCorrelation(
        edges_m=[0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0],
        observed=[300, 100, 100, 100, 100, 100],
        expected=[100.0] * 6,
        envelope_lo=[0.0] * 6,
        envelope_hi=[1e9] * 6,   # envelope criterion would see no clustering
        n_points=10,
        n_sim=39,
    )
    assert clustering_range(pc, criterion="envelope", persistence_bins=3) == 0.0
    assert clustering_range(pc, criterion="ratio", ratio=1.2, persistence_bins=3) == 1000.0


def test_unknown_criterion_raises():
    with pytest.raises(ValueError):
        clustering_range(_pc([1], [1]), criterion="vibes", persistence_bins=1)


# --- End to end, against a known clustering scale ------------------------------


def test_uniform_pattern_yields_a_short_range():
    rng = random.Random(0)
    b = _square(50000.0)
    coords = [(rng.uniform(0, 50000), rng.uniform(0, 50000)) for _ in range(300)]
    pc = pair_correlation(
        coords, UniformSampler(b, cell_m=1000.0), max_r_m=20000.0, bin_m=1000.0, n_sim=19
    )
    assert clustering_range(pc) <= 2000.0


def test_clustered_pattern_yields_a_range_near_the_cluster_scale():
    """Points in 1 km-sd clumps must produce a range of a few km, not 20 km."""
    rng = random.Random(1)
    b = _square(50000.0)
    centres = [(rng.uniform(0, 50000), rng.uniform(0, 50000)) for _ in range(20)]
    coords = []
    while len(coords) < 300:
        cx, cy = rng.choice(centres)
        p = (rng.gauss(cx, 1000.0), rng.gauss(cy, 1000.0))
        if p in b:
            coords.append(p)
    pc = pair_correlation(
        coords, UniformSampler(b, cell_m=1000.0), max_r_m=20000.0, bin_m=1000.0, n_sim=19
    )
    r = clustering_range(pc)
    assert 2000.0 <= r <= 8000.0, f"range {r} is not near the 1 km cluster scale"


def test_sampler_is_reproducible_under_seed():
    s = UniformSampler(_square(10000.0), cell_m=1000.0)
    a = s.sample(50, random.Random(4))
    b = s.sample(50, random.Random(4))
    assert a == b


def test_sampler_rejects_a_cell_size_that_resolves_nothing():
    with pytest.raises(ValueError):
        UniformSampler(_square(1000.0), cell_m=100000.0)


# --- Buffer cost --------------------------------------------------------------


def test_buffer_cost_reports_zero_for_no_buffer():
    coords = [(i * 1000.0, 0.0) for i in range(40)]
    y = [1 if i % 4 == 0 else 0 for i in range(40)]
    blocks = assign_blocks(coords, 5000)
    fa = make_folds(blocks, y, n_folds=4, block_size_m=5000, seed=0)
    cost = buffer_cost(coords, y, fa.fold_of_point, 4, 0.0)
    assert cost["rows_dropped"] == 0
    assert cost["presence_retention"] == pytest.approx(1.0)


def test_buffer_cost_grows_with_the_buffer():
    coords = [(i * 1000.0, 0.0) for i in range(40)]
    y = [1 if i % 4 == 0 else 0 for i in range(40)]
    blocks = assign_blocks(coords, 5000)
    fa = make_folds(blocks, y, n_folds=4, block_size_m=5000, seed=0)
    small = buffer_cost(coords, y, fa.fold_of_point, 4, 1000.0)
    large = buffer_cost(coords, y, fa.fold_of_point, 4, 6000.0)
    assert large["rows_dropped"] > small["rows_dropped"] > 0
    assert large["presence_retention"] < small["presence_retention"] < 1.0


# --- Decay against a regional baseline ----------------------------------------

from src.validate.autocorrelation import decay_range, far_field_baseline


def _pc_from_g(gvals, bin_m=1000.0):
    """PairCorrelation with a prescribed g curve (expected fixed at 100)."""
    n = len(gvals)
    return PairCorrelation(
        edges_m=[i * bin_m for i in range(n + 1)],
        observed=[int(round(v * 100)) for v in gvals],
        expected=[100.0] * n,
        envelope_lo=[0.0] * n,
        envelope_hi=[120.0] * n,
        n_points=10,
        n_sim=39,
    )


def test_far_field_baseline_reads_the_tail_not_the_peak():
    pc = _pc_from_g([8.0, 6.0, 4.0] + [1.5] * 12)
    assert far_field_baseline(pc) == pytest.approx(1.5)


def test_decay_range_ignores_a_raised_regional_baseline():
    """g settling at 1.5 rather than 1.0 must not push the buffer outwards."""
    pc = _pc_from_g([8.0, 4.0, 2.0] + [1.5] * 12)
    # Excess peaks at 6.5 over the 1.5 baseline, so the threshold is
    # 1.5 + 0.325 = 1.825 and the 3-4 km bin is the first below it. Against an
    # absolute baseline of 1.0 this curve would never be judged decayed at all.
    assert decay_range(pc, persistence_bins=4) == 3000.0


def test_envelope_criterion_would_have_demanded_a_far_larger_buffer():
    # Same curve: every bin exceeds the envelope, so the strict criterion
    # cannot find a range at all. This is why decay_range exists.
    pc = _pc_from_g([8.0, 4.0, 2.0] + [1.5] * 12)
    assert math.isnan(clustering_range(pc, persistence_bins=4))


def test_decay_range_zero_when_there_is_no_short_range_excess():
    assert decay_range(_pc_from_g([1.0] * 15), persistence_bins=4) == 0.0


def test_flat_curve_has_no_excess_and_so_no_range():
    # Uniformly elevated g is a regional intensity difference, not a clump.
    assert decay_range(_pc_from_g([8.0] * 15), persistence_bins=4) == 0.0


def test_decay_range_nan_when_the_curve_is_still_falling():
    """Too short a distance range must be reported, not rounded down.

    A curve that has not levelled off has no far field, so the level it happens
    to sit at in the last bin is not a baseline to measure decay against.
    """
    declining = [float(16 - i) for i in range(15)]
    assert math.isnan(decay_range(_pc_from_g(declining), persistence_bins=4))


def test_flat_curve_counts_as_having_reached_its_far_field():
    from src.validate.autocorrelation import far_field_reached
    assert far_field_reached(_pc_from_g([8.0, 4.0, 2.0] + [1.5] * 12))
    assert not far_field_reached(_pc_from_g([float(16 - i) for i in range(15)]))


# --- Inhomogeneous null -------------------------------------------------------

from src.validate.autocorrelation import (
    IntensitySampler,
    decay_range,
    excess_remaining,
    max_reliable_r,
)


def test_max_reliable_r_scales_to_the_region():
    assert max_reliable_r(_square(40000.0)) == 10000.0
    assert max_reliable_r(_square(400000.0)) == 30000.0     # capped


def test_intensity_sampler_follows_the_records_at_regional_scale():
    """Half the region holds every record; the null must favour that half."""
    rng = random.Random(0)
    b = _square(100000.0)
    coords = [(rng.uniform(0, 40000), rng.uniform(0, 100000)) for _ in range(200)]
    s = IntensitySampler(b, coords, cell_m=2000.0, bandwidth_m=10000.0)
    drawn = s.sample(2000, random.Random(1))
    west = sum(1 for x, _ in drawn if x < 50000)
    assert west > 1400, f"only {west}/2000 drawn from the occupied half"


def test_intensity_sampler_floor_reaches_empty_ground():
    """A null that cannot reach unoccupied ground is not a null."""
    rng = random.Random(0)
    b = _square(100000.0)
    coords = [(rng.uniform(0, 10000), rng.uniform(0, 10000)) for _ in range(50)]
    s = IntensitySampler(b, coords, cell_m=2000.0, bandwidth_m=5000.0, floor=0.05)
    drawn = s.sample(3000, random.Random(2))
    assert any(x > 60000 for x, _ in drawn)


def test_intensity_sampler_rejects_empty_records():
    with pytest.raises(ValueError):
        IntensitySampler(_square(10000.0), [], cell_m=1000.0)


def test_intensity_null_removes_a_regional_gradient_that_uniform_null_reports():
    """The distinction the buffer derivation depends on.

    Points spread smoothly across one half of the region, with no local
    clumping at all. Against a uniform null they look clustered out to tens of
    km. Against the intensity null they do not, because the gradient is
    regional and the buffer is not the tool for it.
    """
    rng = random.Random(3)
    b = _square(100000.0)
    coords = []
    while len(coords) < 400:              # smooth density ramp, no local clumps
        x, y = rng.uniform(0, 100000), rng.uniform(0, 100000)
        if rng.random() < (1 - x / 100000):
            coords.append((x, y))

    uni = pair_correlation(
        coords, UniformSampler(b, cell_m=2000.0), max_r_m=20000.0, bin_m=1000.0, n_sim=19
    )
    inh = pair_correlation(
        coords,
        IntensitySampler(b, coords, cell_m=2000.0, bandwidth_m=20000.0),
        max_r_m=20000.0,
        bin_m=1000.0,
        n_sim=19,
    )
    assert far_field_baseline(uni) > 1.25, "the gradient should show against a uniform null"
    assert 0.85 < far_field_baseline(inh) < 1.15, "the intensity null should absorb it"


def test_intensity_null_still_sees_local_clumping():
    """Absorbing the gradient must not absorb the clumps as well."""
    rng = random.Random(4)
    b = _square(100000.0)
    centres = [(rng.uniform(0, 100000), rng.uniform(0, 100000)) for _ in range(25)]
    coords = []
    while len(coords) < 400:
        cx, cy = rng.choice(centres)
        p = (rng.gauss(cx, 1200.0), rng.gauss(cy, 1200.0))
        if p in b:
            coords.append(p)
    pc = pair_correlation(
        coords,
        IntensitySampler(b, coords, cell_m=2000.0, bandwidth_m=20000.0),
        max_r_m=20000.0,
        bin_m=1000.0,
        n_sim=19,
    )
    assert pc.g[0] > 5.0, "1.2 km clumps must still appear at short range"
    # Stated as excess remaining rather than as a range: with only 25 clumps in
    # the region, a 20 km kernel partly resolves the clumps themselves, so the
    # curve has no clean plateau and decay_range correctly declines to answer.
    assert excess_remaining(pc, 1000.0) > 0.4, "the clumps must show at 1 km"
    assert excess_remaining(pc, 10000.0) < 0.1, "and must be gone by 10 km"
