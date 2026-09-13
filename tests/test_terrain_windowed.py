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
        "slope_deg": slope,
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
