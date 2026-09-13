"""Tests for the AR50 land-cover grouping and rasterisation.

The test that matters most here is `test_twenty_is_agriculture_not_transport`.
AR50 and AR5 disagree about what code 20 means, and reading it the AR5 way
would silently relabel 5,363 agricultural polygons in Agder as roads - a wrong
label that would travel into the model, the writeup and the interface without
anything failing.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest.ar50 import (AR50Error, CATEGORIES, GROUPS, LABELS, NODATA,
                             NORWEGIAN, NOT_MAPPED, group_codes, rasterise,
                             unmapped_codes)

rasterio = pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")


class TestGrouping:
    def test_twenty_is_agriculture_not_transport(self):
        assert LABELS[20] == "agriculture"
        assert NORWEGIAN[20] == "Jordbruksareal"

    def test_forest_mire_and_open_ground_stay_distinct(self):
        # SR16 collapses all three into "not productive forest". Splitting them
        # is the entire reason this layer is in the stack.
        assert len({LABELS[30], LABELS[60], LABELS[50]}) == 3

    def test_not_mapped_is_not_a_class(self):
        assert NOT_MAPPED not in GROUPS
        assert group_codes(np.array([NOT_MAPPED]))[0] == NODATA

    def test_nodata_is_not_a_class(self):
        assert NODATA not in GROUPS

    def test_known_codes_burn_as_themselves(self):
        vals = np.array([10, 20, 30, 50, 60, 81, 82])
        assert list(group_codes(vals)) == list(vals)

    def test_an_unknown_code_becomes_nodata(self):
        assert group_codes(np.array([12345]))[0] == NODATA

    def test_group_codes_returns_uint8(self):
        assert group_codes(np.array([30])).dtype == np.dtype("uint8")

    def test_unmapped_codes_reports_novel_codes(self):
        assert unmapped_codes(np.array([30, 30, 77, 77, 77])) == {77: 3}

    def test_unmapped_codes_excludes_not_mapped_deliberately(self):
        assert unmapped_codes(np.array([30, NOT_MAPPED, NOT_MAPPED])) == {}

    def test_categories_are_sorted_and_unique(self):
        assert CATEGORIES == sorted(set(CATEGORIES))


def _reference(path, *, epsg=25833, cell=32.0, width=4, height=3):
    transform = rasterio.transform.from_origin(1000.0, 7000.0, cell, cell)
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", crs=f"EPSG:{epsg}",
                       transform=transform, nodata=np.nan) as dst:
        dst.write(np.zeros((height, width), dtype="float32"), 1)
    return transform


def _gml(path, records, epsg):
    from shapely.geometry import box
    gdf = gpd.GeoDataFrame({"arealtype": [r[0] for r in records]},
                           geometry=[box(*r[1]) for r in records],
                           crs=f"EPSG:{epsg}")
    gdf.to_file(path, driver="GML")
    return gdf


class TestRasterise:
    def test_output_aligns_with_the_reference_grid(self, tmp_path):
        ref = tmp_path / "ref.tif"
        transform = _reference(ref)
        src = tmp_path / "a.gml"
        _gml(src, [(30, (1000.0, 6904.0, 1128.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"

        rasterise(src, ref, out)

        with rasterio.open(out) as dst, rasterio.open(ref) as r:
            assert dst.transform == transform == r.transform
            assert (dst.height, dst.width) == (r.height, r.width)
            assert dst.crs == r.crs

    def test_arealtype_burns_as_its_own_code(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gml"
        _gml(src, [(60, (1000.0, 6904.0, 1128.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"

        rasterise(src, ref, out)

        with rasterio.open(out) as dst:
            assert set(np.unique(dst.read(1))) == {60}

    def test_not_mapped_polygons_are_left_as_nodata(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gml"
        _gml(src, [(NOT_MAPPED, (1000.0, 6904.0, 1128.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"

        stats = rasterise(src, ref, out)

        assert stats["not_mapped_polygons"] == 1
        assert stats["polygons_burned"] == 0
        with rasterio.open(out) as dst:
            assert (dst.read(1) == NODATA).all()

    def test_a_source_in_utm32_is_reprojected_not_refused(self, tmp_path):
        """AR50 ships Agder in 25832. Reprojection belongs here, on ingest."""
        ref = tmp_path / "ref.tif"
        _reference(ref, epsg=25833)
        src = tmp_path / "a.gml"
        # A box in UTM32 that lands on the reference window once reprojected.
        import geopandas as g
        from shapely.geometry import box
        target = g.GeoDataFrame(geometry=[box(1000.0, 6904.0, 1128.0, 7000.0)],
                                crs="EPSG:25833").to_crs(epsg=25832)
        gdf = g.GeoDataFrame({"arealtype": [30]}, geometry=list(target.geometry),
                             crs="EPSG:25832")
        gdf.to_file(src, driver="GML")
        out = tmp_path / "o.tif"

        stats = rasterise(src, ref, out)

        assert stats["reprojected"] is True
        assert stats["source_crs"] == "EPSG:25832"
        assert stats["target_crs"] == "EPSG:25833"
        with rasterio.open(out) as dst:
            assert (dst.read(1) == 30).any(), "reprojected polygon must land on the grid"

    def test_a_matching_crs_is_not_reprojected(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref, epsg=25833)
        src = tmp_path / "a.gml"
        _gml(src, [(30, (1000.0, 6904.0, 1128.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"
        assert rasterise(src, ref, out)["reprojected"] is False

    def test_the_smaller_polygon_wins_on_overlap(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gml"
        _gml(src, [(30, (1000.0, 6904.0, 1128.0, 7000.0)),
                   (60, (1000.0, 6968.0, 1032.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"

        rasterise(src, ref, out)

        with rasterio.open(out) as dst:
            band = dst.read(1)
        assert band[0, 0] == 60, "the small mire must win over the large forest"
        assert band[0, 3] == 30

    def test_group_counts_sum_to_covered_cells(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gml"
        _gml(src, [(30, (1000.0, 6904.0, 1064.0, 7000.0)),
                   (60, (1064.0, 6904.0, 1128.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"

        stats = rasterise(src, ref, out)

        assert sum(stats["cells_by_group"].values()) == stats["cells_covered"]
        assert set(stats["cells_by_group"]) == set(LABELS.values())

    def test_nodata_reads_back_as_nan_for_the_feature_stack(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gml"
        _gml(src, [(30, (1000.0, 6904.0, 1032.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"

        rasterise(src, ref, out)

        with rasterio.open(out) as dst:
            arr = dst.read(1, masked=True).astype("float64").filled(np.nan)
        assert np.isnan(arr).any()
        assert np.nanmin(arr) >= 10


class TestSourceFieldName:
    """AR50 names its land-cover column differently in GML than in geodatabase.

    The per-fylke GML order calls it `arealtype`; the national geodatabase
    calls it `artype`. Same codes, same polygons - checked on Kristiansand by
    sampling 20,000 random points in both sources and comparing the code at
    each, which agreed 20,000 out of 20,000. Only the spelling moved.

    This matters because the national expansion has to use the geodatabase:
    `landsdekkende` AR50 is offered as gdb only, and it is the one national
    form published in EPSG:25833 rather than the per-fylke patchwork of 25832,
    25833 and 25835.
    """

    def _write(self, path, column, epsg=25833, driver=None):
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame({column: [30]},
                               geometry=[box(1000.0, 6904.0, 1128.0, 7000.0)],
                               crs=f"EPSG:{epsg}")
        gdf.to_file(path, driver=driver)
        return gdf

    def test_the_geodatabase_spelling_is_accepted(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gpkg"           # a stand-in for the gdb: same column
        self._write(src, "artype")
        out = tmp_path / "o.tif"

        stats = rasterise(src, ref, out)

        with rasterio.open(out) as dst:
            assert 30 in set(np.unique(dst.read(1)))
        assert stats["source_field"] == "artype"

    def test_the_gml_spelling_still_works(self, tmp_path):
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gml"
        _gml(src, [(30, (1000.0, 6904.0, 1128.0, 7000.0))], 25833)
        out = tmp_path / "o.tif"

        stats = rasterise(src, ref, out)
        assert stats["source_field"] == "arealtype"

    def test_a_source_with_neither_column_is_refused_by_name(self, tmp_path):
        """Silence here would rasterise nothing and report a clean empty map."""
        ref = tmp_path / "ref.tif"
        _reference(ref)
        src = tmp_path / "a.gpkg"
        self._write(src, "landcover")
        with pytest.raises(AR50Error, match="arealtype"):
            rasterise(src, ref, tmp_path / "o.tif")
