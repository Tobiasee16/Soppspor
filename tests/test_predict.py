"""Tests for the grid prediction path.

The risk this guards against is specific: a prediction path that reads rasters
its own way can disagree with training about half-cell offsets, nodata, or the
one-hot column order, and produce a map that looks entirely plausible and is
wrong. So the tests are mostly about the prediction stack being *identical* to
the training stack, not about the scores themselves.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.features.stack import RasterLayer, build, sample_layers
from src.model.predict import grid_coords, predict_grid, snap_grid

rasterio = pytest.importorskip("rasterio")


def write_raster(path, grid, names, cell=32.0, origin=(0.0, 1000.0), nodata=np.nan):
    transform = rasterio.transform.from_origin(origin[0], origin[1], cell, cell)
    with rasterio.open(path, "w", driver="GTiff", height=grid.shape[0],
                       width=grid.shape[1], count=1, dtype="float32",
                       crs="EPSG:25833", transform=transform, nodata=nodata) as dst:
        dst.write(grid.astype("float32"), 1)
        dst.set_band_description(1, names[0])
    return str(path)


def test_grid_coords_land_on_cell_centres():
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)
    east, north, _ = grid_coords(t, 4, 3, slice(0, 4))
    assert east[0, 0] == pytest.approx(5.0)
    assert north[0, 0] == pytest.approx(995.0)
    assert east[0, 2] == pytest.approx(25.0)
    assert north[3, 0] == pytest.approx(965.0)


def test_predicted_value_matches_sampling_the_same_point_directly(tmp_path):
    """The core guarantee: the map is what the model would say at that point."""
    grid = np.arange(16, dtype="float32").reshape(4, 4)
    path = write_raster(tmp_path / "f.tif", grid, ["v"], cell=10.0)
    layer = RasterLayer(path, method="nearest")
    names = sample_layers([layer], [(5.0, 995.0)])[1]

    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)
    out = predict_grid(lambda X: [row[0] * 2.0 for row in X], [layer], names,
                       t, 4, 4)
    assert out[0, 0] == pytest.approx(0.0)
    assert out[1, 2] == pytest.approx(grid[1, 2] * 2.0)
    assert out[3, 3] == pytest.approx(grid[3, 3] * 2.0)


def test_a_feature_name_mismatch_raises_rather_than_producing_a_map(tmp_path):
    grid = np.ones((3, 3), dtype="float32")
    path = write_raster(tmp_path / "f.tif", grid, ["v"], cell=10.0)
    layer = RasterLayer(path, method="nearest")
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)
    with pytest.raises(ValueError, match="does not match the fitted feature set"):
        predict_grid(lambda X: [0.5] * len(X), [layer], ["something_else"], t, 3, 3)


def test_cells_with_a_missing_feature_stay_nan(tmp_path):
    grid = np.array([[1.0, np.nan], [3.0, 4.0]], dtype="float32")
    path = write_raster(tmp_path / "f.tif", grid, ["v"], cell=10.0)
    layer = RasterLayer(path, method="nearest")
    names = sample_layers([layer], [(5.0, 995.0)])[1]
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)
    out = predict_grid(lambda X: [1.0] * len(X), [layer], names, t, 2, 2)
    assert np.isnan(out[0, 1])
    assert out[0, 0] == pytest.approx(1.0)


def test_cells_outside_the_boundary_are_never_scored(tmp_path):
    grid = np.ones((4, 4), dtype="float32")
    path = write_raster(tmp_path / "f.tif", grid, ["v"], cell=10.0)
    layer = RasterLayer(path, method="nearest")
    names = sample_layers([layer], [(5.0, 995.0)])[1]
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)

    scored = []

    def model(X):
        scored.append(len(X))
        return [1.0] * len(X)

    out = predict_grid(model, [layer], names, t, 4, 4,
                       inside=lambda e, n: e < 20.0)
    assert np.isnan(out[:, 2:]).all()
    assert np.isfinite(out[:, :2]).all()
    assert sum(scored) == 8


def test_blocking_does_not_change_the_result(tmp_path):
    grid = np.arange(64, dtype="float32").reshape(8, 8)
    path = write_raster(tmp_path / "f.tif", grid, ["v"], cell=10.0)
    layer = RasterLayer(path, method="nearest")
    names = sample_layers([layer], [(5.0, 995.0)])[1]
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)
    whole = predict_grid(lambda X: [r[0] for r in X], [layer], names, t, 8, 8,
                         rows_per_block=8)
    blocked = predict_grid(lambda X: [r[0] for r in X], [layer], names, t, 8, 8,
                           rows_per_block=3)
    assert np.array_equal(np.nan_to_num(whole, nan=-1),
                          np.nan_to_num(blocked, nan=-1))


def test_prediction_uses_the_same_column_order_as_training(tmp_path):
    """Two layers, so a swapped order would be silent without this."""
    a = write_raster(tmp_path / "a.tif", np.full((2, 2), 1.0, dtype="float32"),
                     ["a"], cell=10.0)
    b = write_raster(tmp_path / "b.tif", np.full((2, 2), 7.0, dtype="float32"),
                     ["b"], cell=10.0)
    layers = [RasterLayer(a), RasterLayer(b)]
    design = build([(5.0, 995.0)], [(15.0, 985.0)], layers)
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)
    out = predict_grid(lambda X: [row[0] * 10 + row[1] for row in X], layers,
                       design.names, t, 2, 2)
    assert out[0, 0] == pytest.approx(17.0), "a then b, as trained"


class TestSnapGrid:
    def test_the_grid_aligns_to_the_given_origin(self):
        t, h, w = snap_grid((100.0, 500.0, 300.0, 700.0), 16.0, (-21824.0, 6640704.0))
        assert (t.c - (-21824.0)) % 16.0 == pytest.approx(0.0)
        assert ((6640704.0) - t.f) % 16.0 == pytest.approx(0.0)

    def test_the_grid_covers_the_whole_bbox(self):
        bbox = (100.0, 500.0, 300.0, 700.0)
        t, h, w = snap_grid(bbox, 16.0, (-21824.0, 6640704.0))
        assert t.c <= bbox[0]
        assert t.f >= bbox[3]
        assert t.c + w * 16.0 >= bbox[2]
        assert t.f - h * 16.0 <= bbox[1]

    def test_a_finer_cell_gives_proportionally_more_cells(self):
        bbox = (0.0, 0.0, 1600.0, 1600.0)
        _, h32, w32 = snap_grid(bbox, 32.0, (0.0, 1600.0))
        _, h16, w16 = snap_grid(bbox, 16.0, (0.0, 1600.0))
        assert (h16, w16) == (h32 * 2, w32 * 2)


def test_inside_mask_and_inside_callable_agree(tmp_path):
    grid = np.arange(16, dtype="float32").reshape(4, 4)
    path = write_raster(tmp_path / "f.tif", grid, ["v"], cell=10.0)
    layer = RasterLayer(path, method="nearest")
    names = sample_layers([layer], [(5.0, 995.0)])[1]
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)

    mask = np.zeros((4, 4), dtype=bool)
    mask[:, :2] = True
    by_mask = predict_grid(lambda X: [r[0] for r in X], [layer], names, t, 4, 4,
                           inside_mask=mask)
    by_call = predict_grid(lambda X: [r[0] for r in X], [layer], names, t, 4, 4,
                           inside=lambda e, n: e < 20.0)
    assert np.array_equal(np.nan_to_num(by_mask, nan=-1),
                          np.nan_to_num(by_call, nan=-1))


def test_giving_both_inside_forms_is_refused(tmp_path):
    grid = np.ones((2, 2), dtype="float32")
    path = write_raster(tmp_path / "f.tif", grid, ["v"], cell=10.0)
    layer = RasterLayer(path, method="nearest")
    t = rasterio.transform.from_origin(0.0, 1000.0, 10.0, 10.0)
    with pytest.raises(ValueError, match="not both"):
        predict_grid(lambda X: [0.5] * len(X), [layer], ["v"], t, 2, 2,
                     inside=lambda e, n: e > 0,
                     inside_mask=np.ones((2, 2), dtype=bool))
