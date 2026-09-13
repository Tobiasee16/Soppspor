"""Terrain derivatives computed in windows must equal the whole-array answer.

Agder fits in memory: 6,633 x 6,708 cells. Norway at 32 m is 1.83 billion, and
one float32 band of that is 7.3 GB against 15.7 GB of RAM, so the national
build has to process in windows.

Slope, aspect and curvature are 3x3 operations, so windowing is safe if and
only if each window is read with a one-cell halo of REAL neighbouring data.
Get the halo wrong and every window seam grows a line of cells computed as if
the terrain stopped there - a grid of false ridges across the country, at
exactly the spacing of the block size, and nothing downstream would flag it.

These tests compare windowed output against the whole-array function on the
same data. Equality is the only acceptable result.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

rasterio = pytest.importorskip("rasterio")

from src.features.terrain import (curvature, derivatives_windowed, eastness,
                                  northness, slope_aspect)

CELL = 32.0


def _dem(h=137, w=211, seed=3):
    rng = np.random.default_rng(seed)
    # Smooth terrain plus noise, so slope and curvature are non-trivial
    # everywhere rather than dominated by a single gradient.
    yy, xx = np.mgrid[0:h, 0:w]
    z = (120 * np.sin(xx / 23.0) + 80 * np.cos(yy / 17.0)
         + rng.normal(0, 4, (h, w)))
    # A NaN patch, because the halo has to carry missing data correctly too.
    z[40:60, 70:95] = np.nan
    z[0:8, :] = np.nan                      # a masked strip on the edge
    return z.astype("float64")


def _write(path, arr):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0],
                       width=arr.shape[1], count=1, dtype="float32",
                       crs="EPSG:25833", nodata=np.nan,
                       transform=rasterio.transform.from_origin(0, 0, CELL, CELL)
                       ) as dst:
        dst.write(arr.astype("float32"), 1)


def _whole(arr):
    slope, aspect = slope_aspect(arr, CELL)
    return {
        "elevation": arr,
        # DEGREES, matching what build_terrain.py writes. This helper
        # originally returned the radian slope, which let every equality
        # test above pass against a raster wrong by a factor of 57.3 -
        # a test comparing new code only against the function it wraps
        # checks nothing about convention.
        "slope_deg": np.degrees(slope),
        "northness": northness(aspect, slope),
        "eastness": eastness(aspect, slope),
        "curvature": curvature(arr, CELL),
    }


def _run(tmp_path, arr, block):
    src_p, dst_p = tmp_path / "dem.tif", tmp_path / f"out{block}.tif"
    _write(src_p, arr)
    with rasterio.open(src_p) as src:
        derivatives_windowed(src, dst_p, CELL, block=block)
    out = {}
    with rasterio.open(dst_p) as ds:
        for i, name in enumerate(ds.descriptions, start=1):
            out[name] = ds.read(i).astype("float64")
    return out


@pytest.mark.parametrize("block", [32, 64, 137, 4096])
def test_windowed_equals_whole_array(tmp_path, block):
    """Including block=4096, which is larger than the raster: one window."""
    arr = _dem()
    want = _whole(arr)
    got = _run(tmp_path, arr, block)
    assert set(got) == set(want)
    for name in want:
        a = np.asarray(want[name], dtype="float32").astype("float64")
        b = got[name]
        both_nan = np.isnan(a) & np.isnan(b)
        assert np.array_equal(np.isnan(a), np.isnan(b)), f"{name}: NaN mask moved"
        np.testing.assert_allclose(a[~both_nan], b[~both_nan], rtol=1e-5,
                                   atol=1e-4, err_msg=name)


def test_a_seam_does_not_appear_in_curvature(tmp_path):
    """The failure this whole file exists for, stated directly.

    Without a halo the block boundaries read as ridges. Curvature is the most
    sensitive to it, so compare the column either side of a seam against the
    unwindowed answer rather than against its neighbours.
    """
    arr = _dem()
    want = _whole(arr)["curvature"]
    got = _run(tmp_path, arr, 32)["curvature"]
    for seam in (32, 64, 96, 128):
        for col in (seam - 1, seam):
            a = np.asarray(want[:, col], dtype="float32").astype("float64")
            b = got[:, col]
            ok = ~(np.isnan(a) & np.isnan(b))
            np.testing.assert_allclose(a[ok], b[ok], rtol=1e-5, atol=1e-4,
                                       err_msg=f"seam at column {col}")


def test_bands_are_named_in_the_order_the_stack_expects(tmp_path):
    """run_models indexes terrain bands by number, so order is load-bearing."""
    got = _run(tmp_path, _dem(), 64)
    assert list(got) == ["elevation", "slope_deg", "northness", "eastness",
                         "curvature"]


# --- the 4 GB wall -----------------------------------------------------------
#
# A classic TIFF cannot exceed 4 GB. The national terrain stack is five float32
# bands over 1.83 billion cells - 36.6 GB uncompressed - and the first attempt
# died at 54% with "TIFFAppendToStrip: Maximum TIFF file size exceeded".
#
# GDAL's default, BIGTIFF=IF_NEEDED, decides at creation time from an estimate,
# and with compression in play it guessed the file would fit. It did not. These
# writers exist specifically for rasters too large to hold in memory, so they
# ask for BigTIFF outright rather than letting an estimate decide.

def _is_bigtiff(path):
    """TIFF version field: 42 is classic, 43 is BigTIFF."""
    import struct
    with open(path, "rb") as fh:
        head = fh.read(4)
    order = "<" if head[:2] == b"II" else ">"
    return struct.unpack(order + "H", head[2:4])[0] == 43


def test_output_is_a_bigtiff(tmp_path):
    src_p, dst_p = tmp_path / "dem.tif", tmp_path / "out.tif"
    _write(src_p, _dem())
    with rasterio.open(src_p) as src:
        derivatives_windowed(src, dst_p, CELL, block=64)
    assert _is_bigtiff(dst_p), "classic TIFF caps at 4 GB; the national stack is 36.6"


def test_a_failed_write_does_not_leave_a_partial_raster(tmp_path, monkeypatch):
    """A half-written terrain stack opens cleanly and reads as real terrain.

    The first national attempt left 3.1 GB of valid-looking GeoTIFF behind when
    it hit the 4 GB wall. Nothing downstream would have questioned it.
    """
    import src.features.terrain as T

    src_p, dst_p = tmp_path / "dem.tif", tmp_path / "out.tif"
    _write(src_p, _dem())

    real = T.curvature
    calls = {"n": 0}

    def flaky(dem, cell_m):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("disk full")
        return real(dem, cell_m)

    monkeypatch.setattr(T, "curvature", flaky)
    with rasterio.open(src_p) as src:
        with pytest.raises(RuntimeError, match="disk full"):
            derivatives_windowed(src, dst_p, CELL, block=32)
    assert not dst_p.exists(), "a partial terrain stack must not survive"


# --- units, which the equality tests above cannot see ------------------------
#
# `slope_aspect` returns RADIANS. `build_terrain.py` converts with np.degrees()
# before writing, and names the band slope_deg. derivatives_windowed did not,
# so the national raster carried radians in a band labelled degrees - values
# 57.3 times too small, in a band every model reads.
#
# The equality tests above could never catch this: they compare
# derivatives_windowed against slope_aspect, and both were in radians. The test
# was self-consistent and checked nothing about the convention the rest of the
# pipeline uses. Comparing new code only against the function it wraps is how a
# units bug survives a green suite.

def test_slope_band_is_in_degrees_not_radians(tmp_path):
    """A 10% grade must read 5.71, not 0.0997."""
    import math
    h = w = 40
    xx = np.tile(np.arange(w, dtype="float64"), (h, 1))
    dem = xx * 0.1 * CELL          # a constant 10% grade to the east
    src_p, dst_p = tmp_path / "dem.tif", tmp_path / "out.tif"
    _write(src_p, dem)
    with rasterio.open(src_p) as src:
        derivatives_windowed(src, dst_p, CELL, block=16)
    with rasterio.open(dst_p) as ds:
        slope = ds.read(2)[5:-5, 5:-5]
    assert slope == pytest.approx(math.degrees(math.atan(0.1)), abs=1e-3)


def test_slope_matches_what_build_terrain_writes(tmp_path):
    """Pinned against the Agder pipeline's own convention, not against itself.

    build_terrain.py writes np.degrees(slope) into `slope_deg`. Anything that
    produces a terrain stack has to agree with that, or the national and Agder
    models read the same column as different quantities.
    """
    from src.features.terrain import slope_aspect
    arr = _dem()
    src_p, dst_p = tmp_path / "dem.tif", tmp_path / "out.tif"
    _write(src_p, arr)
    with rasterio.open(src_p) as src:
        derivatives_windowed(src, dst_p, CELL, block=64)
    with rasterio.open(dst_p) as ds:
        got = ds.read(2).astype("float64")
    want_rad, _ = slope_aspect(np.asarray(arr, dtype="float32").astype("float64"), CELL)
    want = np.degrees(want_rad).astype("float32").astype("float64")
    ok = ~(np.isnan(want) & np.isnan(got))
    np.testing.assert_allclose(want[ok], got[ok], rtol=1e-5, atol=1e-4)


def test_northness_and_eastness_still_use_the_radian_slope(tmp_path):
    """Only the written band converts. The weighting does not.

    build_terrain.py computes northness/eastness from the RADIAN slope and
    converts only on the way out, so converting too early would silently
    rescale two more bands.
    """
    from src.features.terrain import eastness, northness, slope_aspect
    arr = _dem()
    src_p, dst_p = tmp_path / "dem.tif", tmp_path / "out.tif"
    _write(src_p, arr)
    with rasterio.open(src_p) as src:
        derivatives_windowed(src, dst_p, CELL, block=64)
    with rasterio.open(dst_p) as ds:
        gn, ge = ds.read(3).astype("float64"), ds.read(4).astype("float64")
    a32 = np.asarray(arr, dtype="float32").astype("float64")
    s, asp = slope_aspect(a32, CELL)
    wn = northness(asp, s).astype("float32").astype("float64")
    we = eastness(asp, s).astype("float32").astype("float64")
    for want, got, nm in ((wn, gn, "northness"), (we, ge, "eastness")):
        ok = ~(np.isnan(want) & np.isnan(got))
        np.testing.assert_allclose(want[ok], got[ok], rtol=1e-5, atol=1e-4,
                                   err_msg=nm)
