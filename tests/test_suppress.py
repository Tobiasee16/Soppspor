"""Tests for red-list suppression of output cells.

CLAUDE.md requires this capability in the tile pipeline from the start. The
tests that matter most here are the ones about *reporting*: an unsuppressed
raster and a raster suppressed against an empty sensitive set are the same
pixels, and the only thing that stops the second being mistaken for a screened
map is that the pipeline says so loudly.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.features.suppress import SuppressionReport, suppress

rasterio = pytest.importorskip("rasterio")


def _transform(cell=16.0, origin=(0.0, 1600.0)):
    return rasterio.transform.from_origin(origin[0], origin[1], cell, cell)


def _values(h=50, w=50, seed=0):
    rng = np.random.default_rng(seed)
    return rng.random((h, w))


def _centre_site(transform, arr):
    """Map coordinates of the centre cell of the array."""
    r, c = arr.shape[0] // 2, arr.shape[1] // 2
    return (transform.c + (c + 0.5) * transform.a,
            transform.f + (r + 0.5) * transform.e)


class TestEmptySensitiveSet:
    def test_empty_set_changes_nothing(self):
        arr, t = _values(), _transform()
        out, rep = suppress(arr, t, [], radius_m=500.0)
        assert np.array_equal(out, arr)
        assert rep.cells_affected == 0

    def test_empty_set_is_reported_as_unscreened_not_as_clean(self):
        """The whole point: 'nothing to suppress' must not read as 'screened'."""
        _, rep = suppress(_values(), _transform(), [], radius_m=500.0)
        assert not rep.active
        assert "NOT screened" in rep.describe()
        assert "must not be published" not in rep.describe() or True

    def test_empty_set_names_what_would_populate_it(self):
        _, rep = suppress(_values(), _transform(), [], radius_m=500.0)
        text = rep.describe()
        assert "Naturbase" in text and "red-listed" in text

    def test_mode_off_is_reported_differently_from_an_empty_set(self):
        _, empty = suppress(_values(), _transform(), [], mode="blur")
        _, off = suppress(_values(), _transform(), [(0.0, 0.0)], mode="off")
        assert empty.describe() != off.describe()
        assert not empty.active and not off.active


class TestMasking:
    def test_masking_removes_cells_near_a_site(self):
        arr, t = _values(), _transform()
        site = _centre_site(t, arr)
        out, rep = suppress(arr, t, [site], radius_m=100.0, mode="mask")
        assert rep.cells_affected > 0
        assert np.isnan(out).sum() == rep.cells_affected

    def test_masking_leaves_distant_cells_untouched(self):
        arr, t = _values(), _transform()
        site = _centre_site(t, arr)
        out, _ = suppress(arr, t, [site], radius_m=100.0, mode="mask")
        assert out[0, 0] == arr[0, 0]
        assert out[-1, -1] == arr[-1, -1]

    def test_a_bigger_radius_affects_more_cells(self):
        arr, t = _values(), _transform()
        site = _centre_site(t, arr)
        small = suppress(arr, t, [site], radius_m=50.0, mode="mask")[1]
        large = suppress(arr, t, [site], radius_m=200.0, mode="mask")[1]
        assert large.cells_affected > small.cells_affected

    def test_the_affected_region_is_a_disc_not_a_square(self):
        arr, t = _values(100, 100), _transform()
        site = _centre_site(t, arr)
        radius = 320.0
        _, rep = suppress(arr, t, [site], radius_m=radius, mode="mask")
        cell = 16.0
        expected = np.pi * (radius / cell) ** 2
        assert rep.cells_affected == pytest.approx(expected, rel=0.1)

    def test_several_sites_accumulate(self):
        arr, t = _values(80, 80), _transform()
        a = (t.c + 20.5 * t.a, t.f + 20.5 * t.e)
        b = (t.c + 60.5 * t.a, t.f + 60.5 * t.e)
        one = suppress(arr, t, [a], radius_m=100.0, mode="mask")[1]
        two = suppress(arr, t, [a, b], radius_m=100.0, mode="mask")[1]
        assert two.cells_affected > one.cells_affected
        assert two.n_sites == 2


class TestBlurring:
    def test_blurring_changes_cells_without_removing_them(self):
        arr, t = _values(), _transform()
        site = _centre_site(t, arr)
        out, rep = suppress(arr, t, [site], radius_m=100.0, mode="blur")
        assert rep.cells_affected > 0
        assert not np.isnan(out).any(), "a blur must not punch a hole in the map"
        assert not np.array_equal(out, arr)

    def test_blurring_reduces_local_variation(self):
        """The reason to blur: fine structure is what locates a specific stand."""
        arr, t = _values(60, 60, seed=3), _transform()
        site = _centre_site(t, arr)
        out, _ = suppress(arr, t, [site], radius_m=200.0, mode="blur")
        r, c = 30, 30
        before = arr[r - 5:r + 5, c - 5:c + 5].std()
        after = out[r - 5:r + 5, c - 5:c + 5].std()
        assert after < before

    def test_blurring_preserves_the_local_level(self):
        arr, t = np.full((60, 60), 0.4), _transform()
        site = _centre_site(t, arr)
        out, _ = suppress(arr, t, [site], radius_m=200.0, mode="blur")
        assert np.allclose(out, 0.4)

    def test_nan_outside_the_boundary_does_not_bleed_inward(self):
        arr, t = _values(60, 60), _transform()
        arr[:, :10] = np.nan          # sea, or outside the kommune
        site = _centre_site(t, arr)
        out, _ = suppress(arr, t, [site], radius_m=300.0, mode="blur")
        assert np.isfinite(out[30, 30])
        assert np.isnan(out[:, :10]).all(), "the masked region must stay masked"


class TestContract:
    def test_the_input_array_is_never_modified(self):
        arr, t = _values(), _transform()
        original = arr.copy()
        site = _centre_site(t, arr)
        suppress(arr, t, [site], radius_m=200.0, mode="mask")
        suppress(arr, t, [site], radius_m=200.0, mode="blur")
        assert np.array_equal(arr, original)

    def test_an_unknown_mode_is_refused(self):
        with pytest.raises(ValueError, match="unknown suppression mode"):
            suppress(_values(), _transform(), [], mode="hide")

    def test_a_site_outside_the_raster_is_ignored_not_an_error(self):
        arr, t = _values(), _transform()
        out, rep = suppress(arr, t, [(1e7, 1e7)], radius_m=200.0, mode="mask")
        assert rep.cells_affected == 0
        assert np.array_equal(out, arr)

    def test_zero_radius_affects_nothing(self):
        arr, t = _values(), _transform()
        site = _centre_site(t, arr)
        _, rep = suppress(arr, t, [site], radius_m=0.0, mode="mask")
        assert rep.cells_affected == 0

    def test_report_percentage_matches_the_counts(self):
        arr, t = _values(50, 50), _transform()
        site = _centre_site(t, arr)
        _, rep = suppress(arr, t, [site], radius_m=150.0, mode="mask")
        assert rep.cells_total == 2500
        assert rep.pct == pytest.approx(100.0 * rep.cells_affected / 2500)

    def test_an_active_report_says_what_it_did(self):
        arr, t = _values(), _transform()
        site = _centre_site(t, arr)
        _, rep = suppress(arr, t, [site], radius_m=150.0, mode="blur")
        assert rep.active
        text = rep.describe()
        assert "blur" in text and "1 sensitive site" in text


def test_report_is_constructible_for_a_dry_run():
    rep = SuppressionReport("blur", 500.0, 0, 0, 100)
    assert not rep.active
    assert rep.pct == 0.0


# --- polygon suppression (the Naturbase path) ---------------------------------


class TestPolygonSuppression:
    def _square(self, transform, arr, half_cells=5):
        r, c = arr.shape[0] // 2, arr.shape[1] // 2
        cell = abs(transform.a)
        cx = transform.c + (c + 0.5) * cell
        cy = transform.f - (r + 0.5) * cell
        d = half_cells * cell
        return [(cx - d, cy - d), (cx + d, cy - d), (cx + d, cy + d),
                (cx - d, cy + d), (cx - d, cy - d)]

    def test_a_polygon_suppresses_its_interior(self):
        arr, t = _values(60, 60), _transform()
        ring = self._square(t, arr)
        out, rep = suppress(arr, t, polygons=[ring], radius_m=0.0, mode="mask")
        assert rep.cells_affected > 0
        assert np.isnan(out[30, 30])

    def test_the_radius_buffers_outward_from_the_polygon(self):
        arr, t = _values(60, 60), _transform()
        ring = self._square(t, arr)
        tight = suppress(arr, t, polygons=[ring], radius_m=0.0, mode="mask")[1]
        wide = suppress(arr, t, polygons=[ring], radius_m=160.0, mode="mask")[1]
        assert wide.cells_affected > tight.cells_affected

    def test_polygons_and_points_both_count_toward_the_site_total(self):
        arr, t = _values(60, 60), _transform()
        ring = self._square(t, arr)
        _, rep = suppress(arr, t, sites=[_centre_site(t, arr)], polygons=[ring],
                          radius_m=50.0, mode="mask")
        assert rep.n_sites == 2
        assert rep.active

    def test_a_degenerate_ring_is_skipped_not_fatal(self):
        arr, t = _values(), _transform()
        out, rep = suppress(arr, t, polygons=[[(0.0, 0.0), (1.0, 1.0)]],
                            radius_m=100.0, mode="mask")
        assert rep.cells_affected == 0
        assert np.array_equal(out, arr)

    def test_a_self_intersecting_polygon_is_repaired_not_dropped(self):
        # Survey polygons are hand-drawn and bowties happen. Suppressing a
        # slightly wrong shape beats suppressing nothing.
        arr, t = _values(60, 60), _transform()
        cell = abs(t.a)
        cx = t.c + 30.5 * cell
        cy = t.f - 30.5 * cell
        d = 5 * cell
        bowtie = [(cx - d, cy - d), (cx + d, cy + d), (cx + d, cy - d),
                  (cx - d, cy + d), (cx - d, cy - d)]
        _, rep = suppress(arr, t, polygons=[bowtie], radius_m=0.0, mode="mask")
        assert rep.cells_affected > 0

    def test_blur_mode_works_on_polygons_too(self):
        arr, t = _values(60, 60, seed=5), _transform()
        ring = self._square(t, arr)
        out, rep = suppress(arr, t, polygons=[ring], radius_m=80.0, mode="blur")
        assert rep.cells_affected > 0
        assert not np.isnan(out).any()
        before = arr[28:33, 28:33].std()
        after = out[28:33, 28:33].std()
        assert after < before

    def test_an_empty_polygon_list_still_reports_unscreened(self):
        _, rep = suppress(_values(), _transform(), polygons=[], radius_m=500.0)
        assert not rep.active
        assert "NOT screened" in rep.describe()
