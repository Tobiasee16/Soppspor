"""A cached sample must be the same number as an uncached one.

The national run calls `build` nine times - eight species and the null - and
every call passes the same 52,249 background points. Uncached that is nine
passes over roughly 40 GB of raster to obtain one pass worth of numbers.

The cache is therefore a speed fix on the hot path of a run whose output is a
published table, which is the worst place for a subtle difference. These tests
demand exact equality against the uncached path, and pin the guard that stops
a cached row being served under a different stack's column names.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.features import stack as st
from src.features.stack import RasterLayer, SampleCache, build, sample_layers

W, H = 120, 90
CELL = 32.0
X0, Y0 = 50_000.0, 6_600_000.0
NODATA = -9999


@pytest.fixture
def raster(tmp_path):
    path = tmp_path / "r.tif"
    data = np.zeros((2, H, W), dtype="int16")
    for b in range(2):
        for r in range(H):
            data[b, r, :] = (b + 1) * 1000 + r * 7 + np.arange(W) % 11
    data[:, 10:20, 10:20] = NODATA
    with rasterio.open(path, "w", driver="GTiff", width=W, height=H, count=2,
                       dtype="int16", crs="EPSG:25833", nodata=NODATA,
                       transform=from_origin(X0, Y0, CELL, CELL),
                       tiled=True, blockxsize=16, blockysize=16) as ds:
        ds.write(data)
    return path


def pts(n, seed=0):
    rng = np.random.default_rng(seed)
    return [(float(x), float(y)) for x, y in zip(
        rng.uniform(X0, X0 + W * CELL, n), rng.uniform(Y0 - H * CELL, Y0, n))]


def layers(raster):
    return [RasterLayer(str(raster), bands=[1, 2], names=["a", "b"])]


class TestCachedEqualsUncached:
    def test_one_call(self, raster):
        coords = pts(200, seed=1)
        direct, n1 = sample_layers(layers(raster), coords)
        cached, n2 = SampleCache().sample(layers(raster), coords)
        assert n1 == n2
        np.testing.assert_array_equal(direct, cached)

    def test_repeated_calls_with_overlapping_points(self, raster):
        """The real access pattern: the same background, different presences."""
        background = pts(300, seed=2)
        cache = SampleCache()
        for seed in (3, 4, 5):
            coords = pts(40, seed=seed) + background
            direct, _ = sample_layers(layers(raster), coords)
            got, _ = cache.sample(layers(raster), coords)
            np.testing.assert_array_equal(direct, got)

    def test_the_background_is_read_only_once(self, raster, monkeypatch):
        background = pts(300, seed=6)
        calls = []
        real = st.sample_layers
        monkeypatch.setattr(st, "sample_layers",
                            lambda l, c: (calls.append(len(c)), real(l, c))[1])
        cache = SampleCache()
        for seed in (7, 8, 9):
            cache.sample(layers(raster), pts(40, seed=seed) + background)
        # First call reads 340 points; the next two read only their own 40.
        assert calls == [340, 40, 40]
        assert cache.n_cached == 340 + 80

    def test_duplicate_points_in_one_call(self, raster):
        coords = pts(20, seed=10)
        doubled = coords + coords
        direct, _ = sample_layers(layers(raster), doubled)
        got, _ = SampleCache().sample(layers(raster), doubled)
        np.testing.assert_array_equal(direct, got)
        assert len(got) == 40

    def test_nodata_rows_are_cached_as_nan_not_dropped(self, raster):
        # Dead centre of the nodata block, so every column is NaN.
        coords = [(X0 + 15 * CELL, Y0 - 15 * CELL)] + pts(10, seed=11)
        direct, _ = sample_layers(layers(raster), coords)
        cache = SampleCache()
        first, _ = cache.sample(layers(raster), coords)
        second, _ = cache.sample(layers(raster), coords)
        assert np.isnan(direct[0]).all(), "the fixture's nodata block moved"
        np.testing.assert_array_equal(np.isnan(direct), np.isnan(first))
        np.testing.assert_array_equal(np.isnan(first), np.isnan(second))

    def test_no_points(self, raster):
        got, names = SampleCache().sample(layers(raster), [])
        assert got.shape == (0, 2) and names == ["a", "b"]


class TestBuildWithAndWithoutACache:
    def test_designs_are_identical(self, raster):
        presence, background = pts(30, seed=12), pts(200, seed=13)
        a = build(presence, background, layers(raster))
        b = build(presence, background, layers(raster), cache=SampleCache())
        np.testing.assert_array_equal(a.X, b.X)
        np.testing.assert_array_equal(a.y, b.y)
        np.testing.assert_array_equal(a.coords, b.coords)
        assert a.names == b.names
        assert a.dropped == b.dropped

    def test_a_shared_cache_across_species_changes_nothing(self, raster):
        background = pts(200, seed=14)
        cache = SampleCache()
        for seed in (15, 16, 17):
            presence = pts(30, seed=seed)
            a = build(presence, background, layers(raster))
            b = build(presence, background, layers(raster), cache=cache)
            np.testing.assert_array_equal(a.X, b.X)
            assert a.dropped == b.dropped


class TestTheStackSignatureGuard:
    def test_a_changed_stack_is_refused(self, raster):
        cache = SampleCache()
        cache.sample(layers(raster), pts(10, seed=18))
        other = [RasterLayer(str(raster), bands=[2], names=["b"])]
        with pytest.raises(ValueError, match="feature stack changed"):
            cache.sample(other, pts(10, seed=18))

    def test_an_equivalent_stack_is_accepted(self, raster):
        """Rebuilt layer objects are fine; it is the contents that matter."""
        cache = SampleCache()
        cache.sample(layers(raster), pts(10, seed=19))
        cache.sample(layers(raster), pts(10, seed=20))
        assert cache.n_cached == 20

    def test_renaming_a_band_is_a_different_stack(self, raster):
        cache = SampleCache()
        cache.sample(layers(raster), pts(5, seed=21))
        renamed = [RasterLayer(str(raster), bands=[1, 2], names=["a", "zzz"])]
        with pytest.raises(ValueError, match="feature stack changed"):
            cache.sample(renamed, pts(5, seed=21))
