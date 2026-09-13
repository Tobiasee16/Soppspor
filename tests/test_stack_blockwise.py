"""The two sampling paths must return the same numbers, cell for cell.

`sample_layers` reads a whole band when the raster is small and only the
occupied blocks when it is large. That switch exists because the national
rasters cannot be read whole - 14.7 GB per terrain band, 72 GB per SR16 band,
on a 15.7 GB machine - and it is dangerous precisely because both paths
return plausible numbers.

So every test here samples the same points twice, once each way, and demands
exact equality. NaN handling is included rather than assumed: nodata and
out-of-bounds are where two sampling implementations usually diverge, and a
divergence there would show up as a model fitted on slightly different data,
which no AUC could reveal.
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
from src.features.stack import RasterLayer, sample_layers

W, H = 200, 160
CELL = 32.0
X0, Y0 = 100_000.0, 6_500_000.0      # top-left corner, UTM33-ish
NODATA = -9999


def make_raster(path, tiled: bool, count: int = 3, dtype: str = "int16"):
    """A raster whose values encode their own (band, row, col).

    A wrong cell then reads as an obviously wrong number rather than as a
    merely different one, which is what makes the equality assertions below
    worth anything.
    """
    data = np.zeros((count, H, W), dtype=dtype)
    for b in range(count):
        for r in range(H):
            data[b, r, :] = (b + 1) * 10_000 + r * 50 + np.arange(W) % 50
    # A block of nodata, and a scattering of it, so both paths meet it.
    data[:, 40:60, 30:70] = NODATA
    rng = np.random.default_rng(3)
    for _ in range(200):
        data[:, rng.integers(0, H), rng.integers(0, W)] = NODATA

    profile = dict(driver="GTiff", width=W, height=H, count=count, dtype=dtype,
                   crs="EPSG:25833", transform=from_origin(X0, Y0, CELL, CELL),
                   nodata=NODATA)
    if tiled:
        profile.update(tiled=True, blockxsize=32, blockysize=32, compress="deflate")
    else:
        # Stripes, one row per block, LZW - exactly how NIBIO ships the
        # national SR16 mosaic, and the layout that makes a whole-band read
        # tempting and a point read expensive.
        profile.update(tiled=False, compress="lzw")
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data)
    return data


def points(n, seed=0, margin=0.0):
    rng = np.random.default_rng(seed)
    xs = rng.uniform(X0 - margin, X0 + W * CELL + margin, n)
    ys = rng.uniform(Y0 - H * CELL - margin, Y0 + margin, n)
    return np.column_stack([xs, ys])


@pytest.fixture(params=[True, False], ids=["tiled", "striped"])
def raster(request, tmp_path):
    path = tmp_path / ("r_tiled.tif" if request.param else "r_striped.tif")
    make_raster(path, tiled=request.param)
    return path


class TestTheTwoPathsAgree:
    def test_same_values_for_every_band(self, raster):
        pts = points(500, seed=1)
        with rasterio.open(raster) as ds:
            whole = [st._nearest(ds, b, pts) for b in (1, 2, 3)]
            blocky = st._nearest_by_block(ds, [1, 2, 3], pts)
        for w, b in zip(whole, blocky):
            np.testing.assert_array_equal(w, b)

    def test_nodata_becomes_nan_on_both(self, raster):
        # Aim squarely at the nodata rectangle: rows 40-59, cols 30-69.
        xs = X0 + (np.arange(30, 70) + 0.5) * CELL
        ys = Y0 - (np.arange(40, 60) + 0.5) * CELL
        pts = np.array([(x, y) for x in xs for y in ys])
        with rasterio.open(raster) as ds:
            whole = st._nearest(ds, 1, pts)
            blocky = st._nearest_by_block(ds, [1], pts)[0]
        assert np.isnan(whole).all(), "the fixture's nodata block moved"
        np.testing.assert_array_equal(np.isnan(whole), np.isnan(blocky))

    def test_points_outside_the_raster_are_nan_on_both(self, raster):
        pts = points(400, seed=2, margin=5_000.0)
        with rasterio.open(raster) as ds:
            whole = st._nearest(ds, 1, pts)
            blocky = st._nearest_by_block(ds, [1], pts)[0]
        assert np.isnan(whole).any(), "no point landed outside; widen the margin"
        np.testing.assert_array_equal(np.isnan(whole), np.isnan(blocky))

    def test_corner_cells(self, raster):
        """Half a cell inside each corner, where an off-by-one shows up."""
        h = CELL / 2
        pts = np.array([
            (X0 + h, Y0 - h),                        # top-left
            (X0 + W * CELL - h, Y0 - h),             # top-right
            (X0 + h, Y0 - H * CELL + h),             # bottom-left
            (X0 + W * CELL - h, Y0 - H * CELL + h),  # bottom-right
        ])
        with rasterio.open(raster) as ds:
            whole = st._nearest(ds, 2, pts)
            blocky = st._nearest_by_block(ds, [2], pts)[0]
        np.testing.assert_array_equal(whole, blocky)
        # Top-left must be band 2, row 0, col 0 = 20000. Without this the two
        # implementations could agree on the same error.
        assert whole[0] == 20_000

    def test_a_single_point_still_works(self, raster):
        pts = np.array([(X0 + 100.0, Y0 - 100.0)])
        with rasterio.open(raster) as ds:
            assert st._nearest(ds, 1, pts)[0] == st._nearest_by_block(ds, [1], pts)[0]

    def test_no_points_at_all(self, raster):
        pts = np.empty((0, 2))
        with rasterio.open(raster) as ds:
            got = st._nearest_by_block(ds, [1, 2], pts)
        assert len(got) == 2 and all(len(g) == 0 for g in got)


class TestSampleLayersSwitchesOnSize:
    def test_the_limit_is_above_agder_and_below_national(self):
        # Agder's largest raster is SR16 at 13,265 x 13,416; the national
        # mosaic is 90,016 x 100,299. The limit has to sit between them, or
        # one of the two runs takes the wrong path.
        assert 13_265 * 13_416 < st.WHOLE_BAND_CELL_LIMIT
        assert 6_633 * 6_708 < st.WHOLE_BAND_CELL_LIMIT        # Agder terrain
        assert 38_173 * 48_022 > st.WHOLE_BAND_CELL_LIMIT      # national terrain
        assert 90_016 * 100_299 > st.WHOLE_BAND_CELL_LIMIT     # national SR16

    def test_identical_features_either_side_of_the_limit(self, raster, monkeypatch):
        layer = RasterLayer(str(raster), bands=[1, 2, 3],
                            names=["a", "b", "c"], method="nearest")
        pts = [tuple(p) for p in points(300, seed=4)]
        big, names_big = sample_layers([layer], pts)          # under the limit
        monkeypatch.setattr(st, "WHOLE_BAND_CELL_LIMIT", 0)   # force blockwise
        small, names_small = sample_layers([layer], pts)
        assert names_big == names_small == ["a", "b", "c"]
        np.testing.assert_array_equal(big, small)

    def test_categorical_one_hot_is_identical_either_side(self, raster, monkeypatch):
        layer = RasterLayer(str(raster), bands=[1], names=["cat"],
                            categories=[10_000, 10_050, 10_100],
                            missing_as=0.0, missing_indicator=True)
        pts = [tuple(p) for p in points(300, seed=5)]
        before, n1 = sample_layers([layer], pts)
        monkeypatch.setattr(st, "WHOLE_BAND_CELL_LIMIT", 0)
        after, n2 = sample_layers([layer], pts)
        assert n1 == n2
        np.testing.assert_array_equal(before, after)
        assert before.sum() > 0, "no point hit any category; the test is vacuous"


class TestBilinearRefusesRatherThanGuesses:
    def test_a_huge_bilinear_layer_raises(self, raster, monkeypatch):
        monkeypatch.setattr(st, "WHOLE_BAND_CELL_LIMIT", 0)
        layer = RasterLayer(str(raster), bands=[1], names=["x"], method="bilinear")
        with pytest.raises(MemoryError, match="blockwise bilinear"):
            sample_layers([layer], [(X0 + 100.0, Y0 - 100.0)])

    def test_a_normal_bilinear_layer_is_untouched(self, raster):
        layer = RasterLayer(str(raster), bands=[1], names=["x"], method="bilinear")
        got, _ = sample_layers([layer], [(X0 + 100.0, Y0 - 100.0)])
        assert np.isfinite(got).all()
