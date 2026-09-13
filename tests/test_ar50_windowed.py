"""Rasterising AR50 in windows must equal rasterising it in one go.

Agder's AR50 is 250 MB of GML and loads whole. The national geodatabase holds
1,948,279 MultiPolygons in a 1.86 GB table, and shapely objects cost several
times their packed size, so reading it all would likely exhaust 15.7 GB of RAM
- and the target grid is 1.83 billion cells, another 1.83 GB as uint8.

Windowing introduces one specific hazard. `rasterise` burns polygons largest
first so that a smaller, more specific polygon lands on top; that ordering is
global. A window that reads only the polygons whose bbox intersects it must
still see every polygon that overlaps it, or the overlap would resolve
differently at a window edge than in the middle - a seam of wrong land cover
one cell wide, at block spacing, across the country.

These compare the two paths on the same data. Equality is the only acceptable
result.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

rasterio = pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")

from src.ingest.ar50 import AR50Error, rasterise, rasterise_windowed

CELL = 32.0


def _reference(path, width=200, height=160):
    transform = rasterio.transform.from_origin(1000.0, 7000.0, CELL, CELL)
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs="EPSG:25833",
                       transform=transform, nodata=np.nan) as dst:
        dst.write(np.zeros((height, width), dtype="float32"), 1)


def _polygons(path, n=180, seed=5, field="artype"):
    """Overlapping boxes of assorted sizes, so burn order actually matters."""
    from shapely.geometry import box
    rng = np.random.default_rng(seed)
    codes = [10, 20, 30, 50, 60, 81, 82]
    geoms, vals = [], []
    for _ in range(n):
        w = rng.uniform(60, 1800)
        h = rng.uniform(60, 1800)
        x = rng.uniform(1000.0, 1000.0 + 200 * CELL)
        y = rng.uniform(7000.0 - 160 * CELL, 7000.0)
        geoms.append(box(x, y - h, x + w, y))
        vals.append(int(rng.choice(codes)))
    gdf = gpd.GeoDataFrame({field: vals}, geometry=geoms, crs="EPSG:25833")
    gdf.to_file(path, layer="ar50", driver="GPKG")
    return gdf


@pytest.mark.parametrize("block", [16, 32, 64, 4096])
def test_windowed_equals_whole(tmp_path, block):
    ref = tmp_path / "ref.tif"
    _reference(ref)
    src = tmp_path / "a.gpkg"
    _polygons(src)

    whole_out = tmp_path / "whole.tif"
    rasterise(src, ref, whole_out, layer="ar50")
    win_out = tmp_path / f"win{block}.tif"
    rasterise_windowed(src, ref, win_out, layer="ar50", block=block)

    with rasterio.open(whole_out) as a, rasterio.open(win_out) as b:
        A, B = a.read(1), b.read(1)
        assert a.transform == b.transform
        assert a.crs == b.crs
    diff = int((A != B).sum())
    assert diff == 0, f"block={block}: {diff} cells differ ({100*diff/A.size:.3f}%)"


def test_a_large_polygon_spanning_many_windows_is_not_lost(tmp_path):
    """The bbox filter must catch a polygon whose centre is far outside."""
    from shapely.geometry import box
    ref = tmp_path / "ref.tif"
    _reference(ref)
    src = tmp_path / "a.gpkg"
    # One polygon covering the whole grid, one small one on top of it.
    gdf = gpd.GeoDataFrame(
        {"artype": [30, 60]},
        geometry=[box(0.0, 7000.0 - 400 * CELL, 1000.0 + 400 * CELL, 8000.0),
                  box(1200.0, 7000.0 - 20 * CELL, 1500.0, 7000.0 - 5 * CELL)],
        crs="EPSG:25833")
    gdf.to_file(src, layer="ar50", driver="GPKG")

    out = tmp_path / "w.tif"
    rasterise_windowed(src, ref, out, layer="ar50", block=16)
    with rasterio.open(out) as ds:
        arr = ds.read(1)
    vals = set(np.unique(arr).tolist())
    assert 30 in vals, "the grid-spanning polygon vanished"
    assert 60 in vals, "the small polygon on top of it vanished"


def test_a_crs_mismatch_is_refused_rather_than_silently_wrong(tmp_path):
    """The windowed path filters by bbox, which is meaningless across CRSs."""
    ref = tmp_path / "ref.tif"
    _reference(ref)
    src = tmp_path / "a.gpkg"
    from shapely.geometry import box
    gpd.GeoDataFrame({"artype": [30]},
                     geometry=[box(1000.0, 6900.0, 1100.0, 7000.0)],
                     crs="EPSG:25832").to_file(src, layer="ar50", driver="GPKG")
    with pytest.raises(AR50Error, match="25832"):
        rasterise_windowed(src, ref, tmp_path / "w.tif", layer="ar50", block=32)
