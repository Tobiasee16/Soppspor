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


class TestFittingRows:
    """One exclusion pass, not one per row.

    The inline version re-ran the whole buffer exclusion for every training
    row. It returned the right answer and cost hours, which is the worst
    combination: nothing fails, nothing is wrong, and the run simply does not
    finish. The call count is therefore the thing worth asserting.
    """

    class Box:
        """A square held-out region, standing in for Nordland."""

        name = "Box"

        def __init__(self, x0, y0, side):
            self.b = (x0, y0, x0 + side, y0 + side)

        def __contains__(self, pt):
            x, y = pt
            return self.b[0] <= x <= self.b[2] and self.b[1] <= y <= self.b[3]

    def grid(self, n=40, step=1000.0):
        return [(i * step, j * step) for i in range(n) for j in range(n)]

    def test_the_exclusion_runs_exactly_once(self, monkeypatch):
        import src.validate.harness as harness
        calls = []
        real = harness._excluded_by_buffer
        monkeypatch.setattr(harness, "_excluded_by_buffer",
                            lambda *a, **k: (calls.append(1), real(*a, **k))[1])
        pts = self.grid()
        bmn.fitting_rows(pts, self.Box(20_000.0, 20_000.0, 8_000.0), 4000.0)
        assert calls == [1], f"exclusion ran {len(calls)} times, not once"

    def test_held_out_points_are_not_in_the_training_set(self):
        pts = self.grid()
        hold = self.Box(20_000.0, 20_000.0, 8_000.0)
        tr, n_held = bmn.fitting_rows(pts, hold, 4000.0)
        assert n_held > 0
        assert not any(pts[i] in hold for i in tr)

    def test_the_buffer_removes_more_than_the_region_alone(self):
        pts = self.grid()
        hold = self.Box(20_000.0, 20_000.0, 8_000.0)
        wide, _ = bmn.fitting_rows(pts, hold, 4000.0)
        none, _ = bmn.fitting_rows(pts, hold, 0.0)
        assert len(wide) < len(none)

    def test_it_matches_what_build_maps_does_line_for_line(self):
        """The Agder script is the reference; the national one must agree."""
        from src.validate.harness import _excluded_by_buffer
        pts = self.grid()
        hold = self.Box(20_000.0, 20_000.0, 8_000.0)
        in_pred = [p in hold for p in pts]
        te = [i for i, f in enumerate(in_pred) if f]
        tr = [i for i, f in enumerate(in_pred) if not f]
        dropped = _excluded_by_buffer(pts, tr, te, 4000.0)
        expected = [i for i in tr if i not in dropped]
        assert bmn.fitting_rows(pts, hold, 4000.0)[0] == expected
