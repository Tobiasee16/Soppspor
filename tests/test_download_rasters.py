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


# --- which SR16 layers come out of the archive ------------------------------

import extract_sr16 as ex


class TestSR16MemberResolution:
    """SR16 ships 23 layers; this project fits on 8. Picking the wrong member
    is not a loud failure, so the matching is pinned here.

    The national and regional downloads name members differently
    (`SRRLAI.tif` against `sr16_42_SRRLAI.tif`), and several layer names are
    prefixes of others (`SRRTREANTALL` of `SRRTREANTALL_GE8`,
    `SRRDIAMMIDDEL` of `SRRDIAMMIDDEL_GE8`). A prefix match would silently
    hand back a raster of a different quantity at the same grid, which no
    downstream check would notice.
    """

    NATIONAL = ["SRRLAI.tif", "SRRTRESLAG.tif", "SRRVOLMB.tif", "SRRGRFLATE.tif",
                "SRRMHOYDE.tif", "SRRTREALDER.tif", "SRRKRONEDEK.tif",
                "SRRBONITET.tif", "SRRTREANTALL.tif", "SRRTREANTALL_GE8.tif",
                "SRRDIAMMIDDEL.tif", "SRRDIAMMIDDEL_GE8.tif"]
    REGIONAL = ["sr16_42_" + n for n in NATIONAL]

    def test_national_names_resolve(self):
        got = ex.wanted_members(self.NATIONAL)
        assert got["LAI"] == "SRRLAI.tif"
        assert set(got) == set(ex.WANTED)

    def test_regional_names_resolve_to_the_same_codes(self):
        got = ex.wanted_members(self.REGIONAL)
        assert got["LAI"] == "sr16_42_SRRLAI.tif"
        assert set(got) == set(ex.WANTED)

    def test_a_prefix_is_not_a_match(self):
        """SRRTREANTALL.tif must not answer a request for TREANTALL_GE8."""
        got = ex.wanted_members(self.NATIONAL, wanted=("TREANTALL",))
        assert got["TREANTALL"] == "SRRTREANTALL.tif"
        got = ex.wanted_members(self.NATIONAL, wanted=("TREANTALL_GE8",))
        assert got["TREANTALL_GE8"] == "SRRTREANTALL_GE8.tif"
        got = ex.wanted_members(self.NATIONAL, wanted=("DIAMMIDDEL",))
        assert got["DIAMMIDDEL"] == "SRRDIAMMIDDEL.tif"

    def test_a_missing_layer_stops_rather_than_skips(self):
        with pytest.raises(SystemExit, match="no member for LAI"):
            ex.wanted_members(["SRRVOLMB.tif"], wanted=("LAI",))

    def test_an_ambiguous_match_stops(self):
        with pytest.raises(SystemExit, match="matched 2 members"):
            ex.wanted_members(["SRRLAI.tif", "x/SRRLAI.tif"], wanted=("LAI",))


class TestExtractedLayersCoverTheModel:
    def test_wanted_matches_what_the_feature_stack_asks_for(self):
        """A layer in the stack but not in WANTED disappears silently.

        `run_models.layers()` guards every SR16 raster with `if path.exists()`,
        which is right for optional data and dangerous here: a layer that was
        never extracted does not raise, it just leaves a column out of the
        feature stack, and the run reports a clean result computed on fewer
        features than intended.
        """
        import run_models
        needed = set(run_models.SR16_NUMERIC) | {"TRESLAG"}
        assert needed == set(ex.WANTED), (
            f"extract_sr16.WANTED and run_models.SR16_NUMERIC disagree: "
            f"only in stack {sorted(needed - set(ex.WANTED))}, "
            f"only in WANTED {sorted(set(ex.WANTED) - needed)}")
