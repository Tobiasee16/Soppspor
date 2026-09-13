"""The national raster order is a different order, not a bigger one.

Going national changes the area code, and for AR50 it also changes the format
and the projection: `landsdekkende` AR50 is published as a geodatabase only,
in EPSG:25833, where the per-fylke order is GML in 25832. A national run that
quietly reused the regional format and projection would fail at the order
endpoint if we were lucky, and fetch the wrong thing if we were not.

These are offline: they check which order would be placed, not the download.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import download_rasters as dr
from src.ingest.geonorge_download import DownloadError


class TestRegionalIsUnchanged:
    """Phase 0's numbers came off these exact orders. They must not move."""

    def test_agder_is_still_fylke_42(self):
        for spec in dr.DATASETS.values():
            code, kind, _, _ = dr.area_spec(spec, national=False)
            assert (code, kind) == ("42", "fylke")

    def test_ar50_regionally_is_still_gml_in_utm32(self):
        _, _, proj, fmt = dr.area_spec(dr.DATASETS["ar50"], national=False)
        assert (proj, fmt) == ("25832", "gml")

    def test_sr16_and_losmasser_regionally_are_unchanged(self):
        assert dr.area_spec(dr.DATASETS["sr16"], national=False)[2:] == ("25833", "RASTER")
        assert dr.area_spec(dr.DATASETS["losmasser"], national=False)[2:] == ("25833", "SHAPE")


class TestNational:
    def test_every_dataset_orders_the_landsdekkende_area(self):
        for spec in dr.DATASETS.values():
            code, kind, _, _ = dr.area_spec(spec, national=True)
            assert (code, kind) == ("0000", "landsdekkende")

    def test_everything_national_arrives_in_the_project_crs(self):
        """25833 throughout, so nothing needs reprojecting on the way in."""
        for key, spec in dr.DATASETS.items():
            assert dr.area_spec(spec, national=True)[2] == "25833", key

    def test_ar50_nationally_switches_to_the_geodatabase(self):
        _, _, proj, fmt = dr.area_spec(dr.DATASETS["ar50"], national=True)
        assert fmt == "gdb"
        assert proj == "25833"

    def test_the_ar50_layer_name_is_recorded_not_guessed(self):
        assert dr.DATASETS["ar50"]["national"]["layer"] == "ar50"

    def test_a_dataset_with_no_national_spec_is_refused(self):
        """The dangerous default is falling back to the regional format."""
        with pytest.raises(DownloadError, match="no national spec"):
            dr.area_spec({"title": "Invented", "format": "gml"}, national=True)
