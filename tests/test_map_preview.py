"""The preview read must look at the whole raster, not its top row.

`rasterio`'s `read(band, ...)` with a scalar band index already returns a 2-D
array. The first version of the national preview indexed `[0]` into it, which
takes the top row - for Norway a 1,192-cell sliver of Arctic sea, entirely
nodata - and then reported the whole national surface as empty.

That cost a completed 36-minute prediction, because the emptiness check fired
after the GeoTIFF had been written and the script exited before the sidecar
that would have let it resume. Both halves are fixed: the read is correct, and
stats are written before the preview is drawn.

The fixture puts real data *below* an all-nodata top row, so the old bug fails
this file rather than passing it.
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
sys.path.insert(0, str(ROOT / "scripts"))

import build_maps_national as bmn

W, H = 512, 640
CELL = 32.0


@pytest.fixture
def surface(tmp_path):
    """A suitability raster whose top rows are nodata, like the real one."""
    a = np.full((H, W), np.nan, dtype="float32")
    a[200:600, 50:450] = np.linspace(0.0, 1.0, 400 * 400).reshape(400, 400)
    path = tmp_path / "s.tif"
    with rasterio.open(path, "w", driver="GTiff", width=W, height=H, count=1,
                       dtype="float32", crs="EPSG:25833", nodata=float("nan"),
                       transform=from_origin(0.0, 7_000_000.0, CELL, CELL),
                       tiled=True, blockxsize=128, blockysize=128) as ds:
        ds.write(a, 1)
    return path


class TestDecimate:
    def test_it_returns_two_dimensions(self, surface):
        got = bmn.decimate(surface, 32.0 * 8)
        assert got.ndim == 2, "a 1-D result means the top row was taken"

    def test_it_finds_the_data(self, surface):
        got = bmn.decimate(surface, 32.0 * 8)
        assert np.isfinite(got).any(), "the surface reads as entirely empty"

    def test_the_top_row_alone_would_have_been_empty(self, surface):
        """Pins the exact failure: the old code's view really is all nodata."""
        with rasterio.open(surface) as src:
            top = src.read(1, out_shape=(H // 8, W // 8))[0]
        assert top.ndim == 1 and not np.isfinite(top).any()

    def test_values_stay_in_range(self, surface):
        got = bmn.decimate(surface, 32.0 * 8)
        finite = got[np.isfinite(got)]
        assert finite.min() >= 0.0 and finite.max() <= 1.0

    def test_a_coarser_request_gives_a_smaller_sheet(self, surface):
        fine = bmn.decimate(surface, 32.0 * 4)
        coarse = bmn.decimate(surface, 32.0 * 16)
        assert coarse.shape[0] < fine.shape[0]
        assert np.isfinite(coarse).any() and np.isfinite(fine).any()

    def test_averaging_keeps_partly_covered_cells(self, surface):
        """GDAL skips nodata when averaging, so a coastal cell is not lost."""
        avg = bmn.decimate(surface, 32.0 * 8)
        with rasterio.open(surface) as src:
            near = src.read(1, out_shape=(H // 8, W // 8))
        assert np.isfinite(avg).sum() >= np.isfinite(near).sum()

    def test_a_cell_size_at_or_below_native_does_not_divide_by_zero(self, surface):
        got = bmn.decimate(surface, 1.0)
        assert got.shape == (H, W)
