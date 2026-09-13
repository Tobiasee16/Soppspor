"""Losmasser: rasterise in windows, and from more than one source file.

Two things force this, both discovered on the national delivery.

**Size.** The national LosmasseFlate shapefile is 728 MB of geometry against
15.7 GB of RAM, and the target grid is 1.83 billion cells.

**Count.** NGU's national delivery contains TWO polygon layers -
LosmasseFlate_20240621 and _20240622 - where the Agder delivery contains one.
They are spatially disjoint: 0.0% of sampled points from the second fall inside
any polygon of the first. It is 11,482 km2 of extra ground, 14.4% of it in
Nordland, which is the held-out region. Reading only the first would leave the
transfer test scoring `losmasse_missing` over ground that is actually mapped.
See FINDINGS section 20.

So the windowed path takes a LIST of sources and must agree, cell for cell,
with rasterising their concatenation in one go.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

rasterio = pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")

from src.ingest.losmasser import rasterise, rasterise_windowed

CELL = 32.0
# Real NGU jordart codes, spread across mapped groups.
CODES = [11, 12, 41, 42, 43, 90, 100, 130]


def _reference(path, width=180, height=140):
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs="EPSG:25833", nodata=np.nan,
                       transform=rasterio.transform.from_origin(2000.0, 9000.0,
                                                                CELL, CELL)) as dst:
        dst.write(np.zeros((height, width), dtype="float32"), 1)


def _shapes(path, n, seed, xshift=0.0):
    from shapely.geometry import box
    rng = np.random.default_rng(seed)
    geoms, vals = [], []
    for _ in range(n):
        w, h = rng.uniform(50, 1500), rng.uniform(50, 1500)
        x = rng.uniform(2000.0, 2000.0 + 180 * CELL) + xshift
        y = rng.uniform(9000.0 - 140 * CELL, 9000.0)
        geoms.append(box(x, y - h, x + w, y))
        vals.append(int(rng.choice(CODES)))
    gdf = gpd.GeoDataFrame({"jordart": vals}, geometry=geoms, crs="EPSG:25833")
    gdf.to_file(path)
    return gdf


@pytest.mark.parametrize("block", [16, 48, 4096])
def test_windowed_equals_whole_for_one_file(tmp_path, block):
    ref = tmp_path / "ref.tif"
    _reference(ref)
    src = tmp_path / "a.shp"
    _shapes(src, 150, seed=11)

    a, b = tmp_path / "whole.tif", tmp_path / f"win{block}.tif"
    rasterise(src, ref, a)
    rasterise_windowed([src], ref, b, block=block)
    with rasterio.open(a) as x, rasterio.open(b) as y:
        assert int((x.read(1) != y.read(1)).sum()) == 0


def test_two_disjoint_files_are_both_burned(tmp_path):
    """The national case: a supplement covering ground the main file does not."""
    ref = tmp_path / "ref.tif"
    _reference(ref)
    main = tmp_path / "main.shp"
    supp = tmp_path / "supp.shp"
    from shapely.geometry import box
    # Main covers the left half, supplement the right. No overlap at all.
    gpd.GeoDataFrame({"jordart": [90]},
                     geometry=[box(2000.0, 9000.0 - 140 * CELL,
                                   2000.0 + 90 * CELL, 9000.0)],
                     crs="EPSG:25833").to_file(main)
    gpd.GeoDataFrame({"jordart": [130]},
                     geometry=[box(2000.0 + 90 * CELL, 9000.0 - 140 * CELL,
                                   2000.0 + 180 * CELL, 9000.0)],
                     crs="EPSG:25833").to_file(supp)

    only_main = tmp_path / "one.tif"
    both = tmp_path / "both.tif"
    rasterise_windowed([main], ref, only_main, block=32)
    rasterise_windowed([main, supp], ref, both, block=32)

    with rasterio.open(only_main) as a, rasterio.open(both) as b:
        one, two = a.read(1), b.read(1)
    # Reading one file leaves the right half unmapped; reading both fills it.
    assert (one == 0).sum() > 0, "the one-file case should leave a hole"
    assert (two == 0).sum() == 0, "both files together should cover everything"
    assert set(np.unique(two).tolist()) == {6, 9}   # peat, bedrock_bare


def test_a_missing_source_is_refused_not_skipped(tmp_path):
    ref = tmp_path / "ref.tif"
    _reference(ref)
    src = tmp_path / "a.shp"
    _shapes(src, 10, seed=2)
    with pytest.raises(FileNotFoundError, match="nope"):
        rasterise_windowed([src, tmp_path / "nope.shp"], ref, tmp_path / "o.tif")


def test_stats_count_every_source(tmp_path):
    ref = tmp_path / "ref.tif"
    _reference(ref)
    a, b = tmp_path / "a.shp", tmp_path / "b.shp"
    ga = _shapes(a, 40, seed=4)
    gb = _shapes(b, 25, seed=9)
    out = rasterise_windowed([a, b], ref, tmp_path / "o.tif", block=64)
    assert out["sources"] == [str(a), str(b)]
    assert out["polygons"] == len(ga) + len(gb)


def test_a_failed_write_leaves_no_partial_raster(tmp_path, monkeypatch):
    """A partial substrate layer reads as genuine unmapped ground."""
    import src.ingest.losmasser as L
    ref = tmp_path / "ref.tif"
    _reference(ref)
    src = tmp_path / "a.shp"
    _shapes(src, 60, seed=7)

    real = L.group_codes
    calls = {"n": 0}

    def flaky(values):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("boom")
        return real(values)

    monkeypatch.setattr(L, "group_codes", flaky)
    out = tmp_path / "o.tif"
    with pytest.raises(RuntimeError, match="boom"):
        L.rasterise_windowed([src], ref, out, block=16)
    assert not out.exists()
