"""Tests for the NGU superficial-deposit grouping and rasterisation.

The grouping is a set of ecological judgements frozen before the layer is
fitted. These tests exist to stop it drifting silently afterwards - a code
quietly moving from `peat` to `moraine` between runs would change a result
without changing a number anyone is looking at.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest.losmasser import (CATEGORIES, GROUPS, LABELS, NODATA,
                                  group_codes, group_of, rasterise,
                                  unmapped_codes)

rasterio = pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")


class TestGrouping:
    def test_no_jordart_code_lands_in_two_groups(self):
        seen: dict[int, int] = {}
        for group, (_, codes) in GROUPS.items():
            for code in codes:
                assert code not in seen, (
                    f"jordart {code} is in both {LABELS[seen[code]]} and {LABELS[group]}")
                seen[code] = group

    def test_group_codes_are_contiguous_from_one(self):
        # The one-hot in stack.py expands `categories` literally, so a gap here
        # would produce an all-zero column that looks like a real category.
        assert CATEGORIES == list(range(1, len(GROUPS) + 1))

    def test_nodata_is_not_a_group(self):
        assert NODATA not in GROUPS

    @pytest.mark.parametrize("jordart,expected", [
        (11, "moraine"), (12, "moraine"), (16, "moraine"),
        (20, "glaciofluvial"), (30, "glaciofluvial"),
        (41, "marine"), (43, "marine"),
        (50, "fluvial"), (60, "fluvial"),
        (81, "colluvial"), (308, "colluvial"),
        (90, "peat"),
        (100, "organic_thin"),
        (120, "anthropogenic"), (122, "anthropogenic"),
        (130, "bedrock_bare"),
        (0, "water_unspec"), (1, "water_unspec"),
    ])
    def test_known_codes_map_to_the_registered_group(self, jordart, expected):
        assert LABELS[group_of(jordart)] == expected

    def test_peat_and_bare_bedrock_never_collapse_together(self):
        # A bog and a bare rock face are both "no forest soil" and completely
        # different habitats. If these ever share a group the layer is useless.
        assert group_of(90) != group_of(130)

    def test_unknown_code_is_nodata_not_a_residual_class(self):
        assert group_of(999) == NODATA

    def test_group_codes_matches_scalar_version(self):
        values = np.array([11, 90, 130, 999, 43, 0])
        assert list(group_codes(values)) == [group_of(v) for v in values]

    def test_group_codes_returns_uint8(self):
        assert group_codes(np.array([11, 90])).dtype == np.dtype("uint8")

    def test_unmapped_codes_counts_only_unknowns(self):
        values = np.array([11, 11, 999, 999, 999, 90])
        assert unmapped_codes(values) == {999: 3}

    def test_unmapped_codes_empty_when_all_known(self):
        assert unmapped_codes(np.array([11, 90, 130])) == {}


def _reference(path: pathlib.Path, *, epsg: int = 25833, cell: float = 32.0,
               width: int = 4, height: int = 3):
    """A tiny float reference grid standing in for the terrain stack."""
    transform = rasterio.transform.from_origin(1000.0, 7000.0, cell, cell)
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs=f"EPSG:{epsg}",
                       transform=transform, nodata=np.nan) as dst:
        dst.write(np.zeros((height, width), dtype="float32"), 1)
    return transform


def _shapefile(path: pathlib.Path, records, epsg: int = 25833):
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"jordart": [r[0] for r in records]},
        geometry=[box(*r[1]) for r in records],
        crs=f"EPSG:{epsg}",
    )
    gdf.to_file(path, encoding="latin1")
    return gdf


class TestRasterise:
    def test_output_is_cell_for_cell_aligned_with_the_reference(self, tmp_path):
        ref = tmp_path / "ref.tif"
        transform = _reference(ref)
        shp = tmp_path / "dep.shp"
        _shapefile(shp, [(90, (1000.0, 6904.0, 1128.0, 7000.0))])
        out = tmp_path / "out.tif"

        rasterise(shp, ref, out)

        with rasterio.open(out) as dst, rasterio.open(ref) as src:
            assert dst.transform == transform == src.transform
            assert (dst.height, dst.width) == (src.height, src.width)
            assert dst.crs == src.crs

    def test_polygon_burns_its_group_code_not_its_jordart_code(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        shp = tmp_path / "dep.shp"
        _shapefile(shp, [(90, (1000.0, 6904.0, 1128.0, 7000.0))])
        out = tmp_path / "out.tif"

        rasterise(shp, ref, out)

        with rasterio.open(out) as dst:
            band = dst.read(1)
        assert set(np.unique(band)) == {group_of(90)}
        assert group_of(90) != 90

    def test_uncovered_cells_are_nodata(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        shp = tmp_path / "dep.shp"
        # Covers only the left half of the 4-wide grid.
        _shapefile(shp, [(130, (1000.0, 6904.0, 1064.0, 7000.0))])
        out = tmp_path / "out.tif"

        stats = rasterise(shp, ref, out)

        with rasterio.open(out) as dst:
            band = dst.read(1)
            assert dst.nodata == NODATA
        assert (band == NODATA).sum() > 0
        assert stats["coverage_pct"] == pytest.approx(100.0 * (band != NODATA).sum() / band.size)

    def test_nodata_becomes_nan_when_sampled_as_float(self, tmp_path):
        # The contract stack.py relies on: masked read -> NaN -> `_missing`
        # column. If nodata ever stopped being honoured, uncovered ground would
        # enter the model as category 0 and look like a real substrate.
        ref = tmp_path / "ref.tif"
        _reference(ref)
        shp = tmp_path / "dep.shp"
        _shapefile(shp, [(130, (1000.0, 6904.0, 1064.0, 7000.0))])
        out = tmp_path / "out.tif"

        rasterise(shp, ref, out)

        with rasterio.open(out) as dst:
            arr = dst.read(1, masked=True).astype("float64").filled(np.nan)
        assert np.isnan(arr).any()
        assert np.nanmin(arr) >= 1

    def test_smaller_polygon_wins_where_polygons_overlap(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        shp = tmp_path / "dep.shp"
        # A large bedrock sheet with a small peat lens sitting inside it.
        _shapefile(shp, [
            (130, (1000.0, 6904.0, 1128.0, 7000.0)),
            (90, (1000.0, 6968.0, 1032.0, 7000.0)),
        ])
        out = tmp_path / "out.tif"

        rasterise(shp, ref, out)

        with rasterio.open(out) as dst:
            band = dst.read(1)
        assert band[0, 0] == group_of(90), "the small specific polygon must win"
        assert band[0, 3] == group_of(130)

    def test_unrecognised_jordart_is_reported_and_left_unburned(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        shp = tmp_path / "dep.shp"
        _shapefile(shp, [(999, (1000.0, 6904.0, 1128.0, 7000.0))])
        out = tmp_path / "out.tif"

        stats = rasterise(shp, ref, out)

        assert stats["unmapped_jordart"] == {999: 1}
        assert stats["polygons_burned"] == 0
        with rasterio.open(out) as dst:
            assert (dst.read(1) == NODATA).all()

    def test_crs_mismatch_is_refused_rather_than_reprojected(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref, epsg=25833)
        shp = tmp_path / "dep.shp"
        _shapefile(shp, [(90, (1000.0, 6904.0, 1128.0, 7000.0))], epsg=25832)
        out = tmp_path / "out.tif"

        with pytest.raises(ValueError, match="Reproject on download"):
            rasterise(shp, ref, out)

    def test_stats_group_counts_sum_to_covered_cells(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        shp = tmp_path / "dep.shp"
        _shapefile(shp, [
            (130, (1000.0, 6904.0, 1064.0, 7000.0)),
            (90, (1064.0, 6904.0, 1128.0, 7000.0)),
        ])
        out = tmp_path / "out.tif"

        stats = rasterise(shp, ref, out)

        assert sum(stats["cells_by_group"].values()) == stats["cells_covered"]
        assert set(stats["cells_by_group"]) == set(LABELS.values())
