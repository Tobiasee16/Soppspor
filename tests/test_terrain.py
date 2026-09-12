"""Tests for terrain derivatives.

TWI is the feature CLAUDE.md expects to carry the top-ranked species, and it is
computed here rather than taken from a library, so it is checked against
surfaces whose correct answer can be written down: a tilted plane has a slope
we can compute by hand, a cone has an aspect that rotates through the compass,
a single channel has a flow accumulation we can count.
"""
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.features.terrain import (
    curvature,
    eastness,
    fill_depressions,
    flow_accumulation,
    northness,
    slope_aspect,
    twi,
)

CELL = 16.0


def _plane(n=20, dzdx=0.0, dzdy=0.0, cell=CELL):
    r, c = np.mgrid[0:n, 0:n]
    # rows increase southward, so a positive dzdy means rising to the south
    return (c * cell * dzdx + r * cell * dzdy).astype("float64")


# --- slope --------------------------------------------------------------------


def test_flat_ground_has_zero_slope():
    s, _ = slope_aspect(np.zeros((10, 10)), CELL)
    assert np.allclose(s, 0.0)


def test_slope_of_a_known_gradient():
    """A 10% grade must read as atan(0.1) = 5.71 degrees."""
    dem = _plane(dzdx=0.1)
    s, _ = slope_aspect(dem, CELL)
    assert s[5:-5, 5:-5] == pytest.approx(math.atan(0.1), abs=1e-9)


def test_slope_is_independent_of_direction():
    a, _ = slope_aspect(_plane(dzdx=0.2), CELL)
    b, _ = slope_aspect(_plane(dzdy=0.2), CELL)
    assert a[5:-5, 5:-5].mean() == pytest.approx(b[5:-5, 5:-5].mean(), abs=1e-9)


def test_slope_scales_with_cell_size():
    """The same elevation array over bigger cells is a gentler hill."""
    dem = np.tile(np.arange(20, dtype="float64"), (20, 1))
    fine, _ = slope_aspect(dem, 10.0)
    coarse, _ = slope_aspect(dem, 100.0)
    assert coarse[5:-5, 5:-5].mean() < fine[5:-5, 5:-5].mean()


# --- aspect -------------------------------------------------------------------


def test_aspect_of_a_south_facing_slope():
    """Ground descending toward the south faces south: aspect 180 degrees."""
    dem = _plane(dzdy=-0.1)            # height falls as rows increase (southward)
    _, a = slope_aspect(dem, CELL)
    assert np.degrees(a[5:-5, 5:-5]) == pytest.approx(180.0, abs=1e-6)


def test_aspect_of_a_north_facing_slope():
    dem = _plane(dzdy=0.1)             # height rises southward, so it faces north
    _, a = slope_aspect(dem, CELL)
    deg = np.degrees(a[5:-5, 5:-5]) % 360.0
    assert deg == pytest.approx(0.0, abs=1e-6) or deg == pytest.approx(360.0, abs=1e-6)


def test_aspect_of_an_east_facing_slope():
    dem = _plane(dzdx=-0.1)            # falls toward increasing column = east
    _, a = slope_aspect(dem, CELL)
    assert np.degrees(a[5:-5, 5:-5]) == pytest.approx(90.0, abs=1e-6)


def test_northness_is_positive_facing_north_negative_facing_south():
    for dzdy, expect_positive in ((0.1, True), (-0.1, False)):
        s, a = slope_aspect(_plane(dzdy=dzdy), CELL)
        n = northness(a, s)[5:-5, 5:-5]
        assert (n > 0).all() if expect_positive else (n < 0).all()


def test_aspect_on_flat_ground_does_not_vote():
    """Aspect is arbitrary where there is no slope, so it must be weighted out."""
    s, a = slope_aspect(np.zeros((10, 10)), CELL)
    assert np.allclose(northness(a, s), 0.0)
    assert np.allclose(eastness(a, s), 0.0)


# --- curvature ----------------------------------------------------------------


def test_curvature_is_negative_in_a_hollow():
    r, c = np.mgrid[0:21, 0:21]
    bowl = ((r - 10) ** 2 + (c - 10) ** 2).astype("float64")
    assert curvature(bowl, CELL)[10, 10] > 0      # bowl: z increases outward
    assert curvature(-bowl, CELL)[10, 10] < 0     # dome


def test_curvature_of_a_plane_is_zero():
    assert np.allclose(curvature(_plane(dzdx=0.3), CELL)[2:-2, 2:-2], 0.0, atol=1e-9)


# --- depression filling -------------------------------------------------------


def test_a_pit_is_raised_to_its_outlet():
    dem = np.full((7, 7), 10.0)
    dem[3, 3] = 2.0
    filled = fill_depressions(dem)
    assert filled[3, 3] > 9.0, "the pit should fill to near the surrounding level"
    assert filled[3, 3] >= 2.0


def test_filling_never_lowers_the_surface():
    rng = np.random.default_rng(0)
    dem = rng.normal(50, 5, (30, 30))
    filled = fill_depressions(dem)
    assert (filled >= dem - 1e-9).all()


def test_a_slope_with_no_pits_is_unchanged():
    dem = _plane(n=15, dzdy=-0.2)
    filled = fill_depressions(dem)
    assert np.allclose(filled[1:-1, 1:-1], dem[1:-1, 1:-1], atol=1e-2)


# --- flow accumulation --------------------------------------------------------


def test_every_cell_contributes_itself():
    dem = _plane(n=10, dzdy=-0.2)
    acc = flow_accumulation(fill_depressions(dem), CELL)
    assert (acc >= 1.0).all()


def test_flow_accumulates_downhill_in_a_channel():
    """A V-shaped valley draining south: accumulation must grow downstream."""
    n = 20
    r, c = np.mgrid[0:n, 0:n]
    dem = (np.abs(c - n // 2) * 2.0 + (n - r) * 5.0).astype("float64")
    acc = flow_accumulation(fill_depressions(dem), CELL)
    channel = acc[:, n // 2]
    assert channel[-3] > channel[3], "downstream must carry more than upstream"


def test_total_accumulation_is_conserved():
    """Nothing is created: the outlet cannot exceed the number of real cells."""
    dem = _plane(n=12, dzdy=-0.3)
    acc = flow_accumulation(fill_depressions(dem), CELL)
    assert np.nanmax(acc) <= dem.size


def test_nan_cells_stay_nan():
    dem = _plane(n=12, dzdy=-0.3)
    dem[0:3, 0:3] = np.nan
    acc = flow_accumulation(fill_depressions(dem), CELL)
    assert np.isnan(acc[0:3, 0:3]).all()


# --- TWI ----------------------------------------------------------------------


def test_twi_is_higher_in_a_valley_than_on_a_ridge():
    """The property the whole feature exists for."""
    n = 40
    r, c = np.mgrid[0:n, 0:n]
    dem = (np.abs(c - n // 2) * 3.0 + (n - r) * 4.0).astype("float64")
    t = twi(dem, CELL)
    valley = t[n // 2, n // 2]
    ridge = t[n // 2, 2]
    assert valley > ridge, f"valley TWI {valley:.2f} should exceed ridge {ridge:.2f}"


def test_twi_is_finite_on_flat_ground():
    """Flat ground has zero slope; without a floor this is a division by zero."""
    t = twi(np.zeros((20, 20)), CELL)
    assert np.isfinite(t).all()


def test_twi_rises_as_slope_falls():
    gentle = twi(_plane(n=30, dzdy=-0.01), CELL)[10:-10, 10:-10].mean()
    steep = twi(_plane(n=30, dzdy=-0.5), CELL)[10:-10, 10:-10].mean()
    assert gentle > steep


def test_twi_preserves_nan():
    dem = _plane(n=20, dzdy=-0.2)
    dem[0:4, 0:4] = np.nan
    t = twi(dem, CELL)
    assert np.isnan(t[0:4, 0:4]).all()


# --- sea masking --------------------------------------------------------------

from src.features.terrain import sea_mask


def test_sea_is_found_from_the_edge_inward():
    dem = np.full((10, 10), 50.0)
    dem[:, :4] = 0.0                      # ocean on the west side
    m = sea_mask(dem)
    assert m[:, :4].all()
    assert not m[:, 4:].any()


def test_an_inland_lake_at_sea_level_is_not_sea():
    """Connectivity, not elevation, is what distinguishes them."""
    dem = np.full((12, 12), 50.0)
    dem[5:8, 5:8] = 0.0                   # a lake in the middle, unconnected
    assert not sea_mask(dem).any()


def test_a_coastal_inlet_reaching_inland_is_sea():
    dem = np.full((12, 12), 50.0)
    dem[:, 0] = 0.0
    dem[6, :8] = 0.0                      # a fjord cutting inland from the west
    m = sea_mask(dem)
    assert m[6, 7]
    assert not m[6, 9]


def test_nothing_below_the_threshold_means_no_sea():
    assert not sea_mask(np.full((8, 8), 100.0)).any()


def test_sea_mask_ignores_nan():
    dem = np.full((8, 8), 50.0)
    dem[0, :] = np.nan
    assert not sea_mask(dem).any()


def test_a_cell_touching_nan_still_gets_a_finite_slope():
    """The coastal halo bug.

    Horn's kernel sums all eight neighbours, so propagating NaN gives every
    land cell adjacent to masked water a NaN slope and therefore a NaN TWI.
    Measured on Agder that removed 28 of 904 thinned presences, unevenly across
    species, which would have biased the ordering test.
    """
    dem = np.full((10, 10), 50.0)
    dem[:, :3] = np.nan                    # masked water to the west
    s, a = slope_aspect(dem, CELL)
    assert np.isfinite(s[5, 3]), "the cell against the mask must survive"
    assert np.isfinite(a[5, 3])
    assert np.isnan(s[5, 1]), "the masked cells themselves stay masked"


def test_curvature_survives_next_to_nan():
    dem = np.full((10, 10), 50.0)
    dem[:, :3] = np.nan
    c = curvature(dem, CELL)
    assert np.isfinite(c[5, 3])
    assert np.isnan(c[5, 1])


def test_twi_survives_next_to_nan():
    n = 30
    r, c = np.mgrid[0:n, 0:n]
    dem = (np.abs(c - n // 2) * 3.0 + (n - r) * 4.0).astype("float64")
    dem[:, :4] = np.nan
    t = twi(dem, CELL)
    assert np.isfinite(t[15, 4]), "coastal ring must carry a TWI value"
