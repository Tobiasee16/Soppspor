"""Streaming the suitability surface must produce the same numbers as holding it.

`predict_grid` builds the whole grid in memory. That is right for Kristiansand -
11.4 million cells, 45 MB - and impossible nationally, where 38,173 x 48,022 at
32 m is 1.83 billion cells and 7.3 GB as float32 before anything else is
allocated. `predict_grid_to_raster` writes each row-block as it is scored.

A suitability map is the worst artefact in this project to get quietly wrong,
because nobody can tell by looking at it. So every test here runs both paths
over the same grid and demands exact equality, NaN placement included.
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

from src.features.stack import RasterLayer
from src.model.predict import predict_grid, predict_grid_to_raster

CELL = 32.0
X0, Y0 = 200_000.0, 6_700_000.0
SRC_W, SRC_H = 80, 60
NODATA = -9999


@pytest.fixture
def layer(tmp_path):
    """A two-band feature raster with a patch of nodata."""
    data = np.zeros((2, SRC_H, SRC_W), dtype="int16")
    for b in range(2):
        for r in range(SRC_H):
            data[b, r, :] = (b + 1) * 100 + r * 3 + np.arange(SRC_W) % 7
    data[:, 20:30, 15:25] = NODATA
    path = tmp_path / "feat.tif"
    with rasterio.open(path, "w", driver="GTiff", width=SRC_W, height=SRC_H,
                       count=2, dtype="int16", crs="EPSG:25833", nodata=NODATA,
                       transform=from_origin(X0, Y0, CELL, CELL),
                       tiled=True, blockxsize=16, blockysize=16) as ds:
        ds.write(data)
    return [RasterLayer(str(path), bands=[1, 2], names=["a", "b"])]


def model(X):
    """Deterministic and order-sensitive, so a mis-assembled block shows up."""
    return [float(r[0]) * 0.001 - float(r[1]) * 0.0005 for r in X]


GRID_H, GRID_W = 50, 70
TRANSFORM = from_origin(X0 + 100.0, Y0 - 100.0, CELL, CELL)


def read(path):
    with rasterio.open(path) as ds:
        return ds.read(1)


class TestTheTwoPathsAgree:
    def test_no_mask(self, layer, tmp_path):
        held = predict_grid(model, layer, ["a", "b"], TRANSFORM, GRID_H, GRID_W,
                            rows_per_block=13)
        out = tmp_path / "out.tif"
        predict_grid_to_raster(model, layer, ["a", "b"], TRANSFORM, GRID_H,
                               GRID_W, out, "EPSG:25833", rows_per_block=13)
        np.testing.assert_array_equal(held, read(out))

    def test_with_a_mask(self, layer, tmp_path):
        mask = np.zeros((GRID_H, GRID_W), dtype=bool)
        mask[5:40, 10:60] = True
        held = predict_grid(model, layer, ["a", "b"], TRANSFORM, GRID_H, GRID_W,
                            inside_mask=mask, rows_per_block=8)
        out = tmp_path / "out.tif"
        predict_grid_to_raster(model, layer, ["a", "b"], TRANSFORM, GRID_H,
                               GRID_W, out, "EPSG:25833", rows_per_block=8,
                               inside_block=lambda s, e: mask[s:e, :])
        np.testing.assert_array_equal(held, read(out))

    def test_nodata_cells_stay_nan_on_both(self, layer, tmp_path):
        held = predict_grid(model, layer, ["a", "b"], TRANSFORM, GRID_H, GRID_W,
                            rows_per_block=16)
        out = tmp_path / "out.tif"
        predict_grid_to_raster(model, layer, ["a", "b"], TRANSFORM, GRID_H,
                               GRID_W, out, "EPSG:25833", rows_per_block=16)
        got = read(out)
        assert np.isnan(held).any(), "the fixture's nodata patch moved"
        np.testing.assert_array_equal(np.isnan(held), np.isnan(got))

    @pytest.mark.parametrize("rows", [1, 7, 50, 999])
    def test_block_size_does_not_change_the_answer(self, layer, tmp_path, rows):
        held = predict_grid(model, layer, ["a", "b"], TRANSFORM, GRID_H, GRID_W)
        out = tmp_path / f"out{rows}.tif"
        predict_grid_to_raster(model, layer, ["a", "b"], TRANSFORM, GRID_H,
                               GRID_W, out, "EPSG:25833", rows_per_block=rows)
        np.testing.assert_array_equal(held, read(out))

    def test_a_fully_masked_block_is_written_as_nan(self, layer, tmp_path):
        mask = np.zeros((GRID_H, GRID_W), dtype=bool)
        mask[45:, :] = True          # only the last rows are inside
        out = tmp_path / "out.tif"
        predict_grid_to_raster(model, layer, ["a", "b"], TRANSFORM, GRID_H,
                               GRID_W, out, "EPSG:25833", rows_per_block=10,
                               inside_block=lambda s, e: mask[s:e, :])
        got = read(out)
        assert np.isnan(got[:45, :]).all()
        assert np.isfinite(got[45:, :]).any()


class TestTheGeoTiffItself:
    def test_georeferencing_survives(self, layer, tmp_path):
        out = tmp_path / "out.tif"
        predict_grid_to_raster(model, layer, ["a", "b"], TRANSFORM, GRID_H,
                               GRID_W, out, "EPSG:25833")
        with rasterio.open(out) as ds:
            assert ds.transform == TRANSFORM
            assert ds.crs.to_string() == "EPSG:25833"
            assert (ds.height, ds.width) == (GRID_H, GRID_W)
            assert ds.count == 1 and ds.dtypes[0] == "float32"

    def test_counts_add_up(self, layer, tmp_path):
        mask = np.zeros((GRID_H, GRID_W), dtype=bool)
        mask[:25, :] = True
        out = tmp_path / "out.tif"
        stats = predict_grid_to_raster(
            model, layer, ["a", "b"], TRANSFORM, GRID_H, GRID_W, out,
            "EPSG:25833", rows_per_block=5,
            inside_block=lambda s, e: mask[s:e, :])
        assert (stats["scored"] + stats["no_features"] + stats["masked_out"]
                == GRID_H * GRID_W)
        assert stats["masked_out"] == 25 * GRID_W
        assert stats["no_features"] > 0, "the nodata patch should be counted"


class TestItDeletesPartialOutput:
    """Three artefacts in this project have been left half-written and
    plausible. A suitability map is the one nobody could spot by eye."""

    def test_a_failing_model_leaves_no_file(self, layer, tmp_path):
        out = tmp_path / "out.tif"
        calls = {"n": 0}

        def explodes(X):
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("model blew up mid-grid")
            return model(X)

        with pytest.raises(RuntimeError, match="blew up"):
            predict_grid_to_raster(explodes, layer, ["a", "b"], TRANSFORM,
                                   GRID_H, GRID_W, out, "EPSG:25833",
                                   rows_per_block=5)
        assert not out.exists(), "a partial suitability map survived"

    def test_a_feature_mismatch_leaves_no_file(self, layer, tmp_path):
        out = tmp_path / "out.tif"
        with pytest.raises(ValueError, match="does not match the fitted"):
            predict_grid_to_raster(model, layer, ["a", "WRONG"], TRANSFORM,
                                   GRID_H, GRID_W, out, "EPSG:25833")
        assert not out.exists()
