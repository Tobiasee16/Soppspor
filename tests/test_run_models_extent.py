"""Phase 0's published numbers come out of run_models.py. Pin them, then extend.

`scripts/run_models.py` is about to learn a second extent - the whole country,
fitted on fourteen fylker and held out on Nordland. Every path it reads is
currently a hardcoded Agder filename, and the file it writes is the one
FINDINGS quotes.

So this pins the Agder path *first*, against the code as it stands, and the
national tests are added below it afterwards. The rule the project keeps
relearning is that a refactor which silently changes what a published number
was computed from is indistinguishable from a refactor that did not - both
produce a file full of plausible numbers.

These are offline. They create empty files with the right names and check which
ones the stack picks up, so they pin the *naming and band selection* without
needing the 58 GB of real rasters.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_models as rm

AGDER_FILES = [
    "terrain_agder_32m.tif",
    "autumn_rain_agder_1km.tif",
    "sr16/sr16_42_SRRTRESLAG.tif",
    "sr16/sr16_42_SRRVOLMB.tif",
    "sr16/sr16_42_SRRGRFLATE.tif",
    "sr16/sr16_42_SRRMHOYDE.tif",
    "sr16/sr16_42_SRRLAI.tif",
    "sr16/sr16_42_SRRTREALDER.tif",
    "sr16/sr16_42_SRRKRONEDEK.tif",
    "sr16/sr16_42_SRRBONITET.tif",
    "losmasser_agder_32m.tif",
    "ar50_agder_32m.tif",
]

# The order the stack is REPORTED in, which is not the order of the files:
# VOLMB appears twice, once as the estimate among the numeric SR16 variables
# and once again at the end carrying its own lower and upper bounds.
AGDER_ORDER = [
    "terrain_agder_32m.tif",
    "autumn_rain_agder_1km.tif",
    "sr16_42_SRRTRESLAG.tif",
    "sr16_42_SRRVOLMB.tif",
    "sr16_42_SRRGRFLATE.tif",
    "sr16_42_SRRMHOYDE.tif",
    "sr16_42_SRRLAI.tif",
    "sr16_42_SRRTREALDER.tif",
    "sr16_42_SRRKRONEDEK.tif",
    "sr16_42_SRRBONITET.tif",
    "sr16_42_SRRVOLMB.tif",
    "losmasser_agder_32m.tif",
    "ar50_agder_32m.tif",
]


@pytest.fixture
def fake_interim(tmp_path, monkeypatch):
    """An interim directory holding every expected filename, all empty."""
    def make(names):
        for rel in names:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
        monkeypatch.setattr(rm, "INTERIM", tmp_path)
        return tmp_path
    return make


def names_of(stack):
    return [pathlib.Path(l.path).name for l in stack]


class TestAgderStackIsUnchanged:
    """The stack Phase 0 was fitted on. Any change here changes the numbers."""

    def test_every_agder_raster_is_picked_up_in_order(self, fake_interim):
        fake_interim(AGDER_FILES)
        assert names_of(rm.layers()) == AGDER_ORDER

    def test_terrain_takes_all_six_bands_by_default(self, fake_interim):
        fake_interim(AGDER_FILES)
        terrain = rm.layers()[0]
        # bands=None means "all of them", which is how the published run read it.
        assert terrain.bands is None and terrain.method == "nearest"

    def test_climate_is_bilinear_and_named(self, fake_interim):
        fake_interim(AGDER_FILES)
        climate = rm.layers()[1]
        assert climate.method == "bilinear"
        assert climate.names == ["autumn_rain_mm"]

    def test_treslag_carries_the_shared_sr16_missing_indicator(self, fake_interim):
        fake_interim(AGDER_FILES)
        treslag = [l for l in rm.layers() if "TRESLAG" in l.path][0]
        assert treslag.categories == [1, 2, 3]
        assert treslag.missing_indicator is True
        # Exactly one SR16 layer may carry it, or the indicator splits its own
        # signal seven ways in the permutation importance.
        sr16 = [l for l in rm.layers() if "SRR" in l.path]
        assert sum(1 for l in sr16 if l.missing_indicator) == 1

    def test_volume_bounds_are_bands_2_and_3_of_volmb(self, fake_interim):
        fake_interim(AGDER_FILES)
        vol = [l for l in rm.layers() if l.names == ["vol_lower", "vol_upper"]][0]
        assert vol.bands == [2, 3] and "VOLMB" in vol.path

    def test_no_vol_bounds_drops_only_the_bounds(self, fake_interim, monkeypatch):
        fake_interim(AGDER_FILES)
        monkeypatch.setattr(rm, "INCLUDE_VOL_BOUNDS", False)
        got = names_of(rm.layers())
        assert got.count("sr16_42_SRRVOLMB.tif") == 1   # the estimate survives
        assert not [l for l in rm.layers() if l.names == ["vol_lower", "vol_upper"]]

    def test_no_climate_drops_only_the_climate_layer(self, fake_interim, monkeypatch):
        # The registered ablation on FINDINGS section 6's accessibility worry.
        # It has to remove the rainfall layer and nothing else, or the result
        # cannot be attributed to rainfall.
        fake_interim(AGDER_FILES)
        before = names_of(rm.layers())
        monkeypatch.setattr(rm, "INCLUDE_CLIMATE", False)
        after = names_of(rm.layers())
        assert [n for n in before if n != "autumn_rain_agder_1km.tif"] == after

    def test_no_climate_nationally_drops_the_national_climate_layer(
            self, fake_interim, monkeypatch):
        fake_interim(NORGE_FILES)
        monkeypatch.setattr(rm, "EXTENT", rm.NORGE.key)
        monkeypatch.setattr(rm, "INCLUDE_TWI", False)
        monkeypatch.setattr(rm, "INCLUDE_CLIMATE", False)
        got = names_of(rm.layers())
        assert "autumn_rain_norge_1km.tif" not in got
        assert "terrain_norge_32m.tif" in got

    def test_climate_is_on_by_default(self, fake_interim):
        fake_interim(AGDER_FILES)
        assert rm.INCLUDE_CLIMATE is True
        assert "autumn_rain_agder_1km.tif" in names_of(rm.layers())

    def test_missing_rasters_are_dropped_not_faked(self, fake_interim):
        fake_interim(["terrain_agder_32m.tif"])
        assert names_of(rm.layers()) == ["terrain_agder_32m.tif"]


class TestTerrainBandAblations:
    """--no-twi and --no-elevation each drop exactly one band and rename none."""

    ALL = ["elevation", "slope_deg", "northness", "eastness", "curvature", "twi"]

    def test_no_twi_keeps_the_first_five_in_order(self, fake_interim, monkeypatch):
        fake_interim(AGDER_FILES)
        monkeypatch.setattr(rm, "INCLUDE_TWI", False)
        terrain = rm.layers()[0]
        assert terrain.bands == [1, 2, 3, 4, 5]
        assert terrain.names == self.ALL[:5]

    def test_no_elevation_keeps_bands_2_to_6(self, fake_interim, monkeypatch):
        fake_interim(AGDER_FILES)
        monkeypatch.setattr(rm, "INCLUDE_ELEVATION", False)
        terrain = rm.layers()[0]
        assert terrain.bands == [2, 3, 4, 5, 6]
        assert terrain.names == self.ALL[1:]

    def test_both_off_drops_both(self, fake_interim, monkeypatch):
        fake_interim(AGDER_FILES)
        monkeypatch.setattr(rm, "INCLUDE_ELEVATION", False)
        monkeypatch.setattr(rm, "INCLUDE_TWI", False)
        terrain = rm.layers()[0]
        assert terrain.bands == [2, 3, 4, 5]
        assert terrain.names == self.ALL[1:5]


class TestAgderPresenceFiltering:
    """County filter, precision filter and thinning, in that order."""

    def write(self, tmp_path, monkeypatch, rows):
        (tmp_path / "x.json").write_text(
            json.dumps({"observations": rows}), encoding="utf-8")
        monkeypatch.setattr(rm, "RAW", tmp_path)

    def test_only_agder_counties_survive(self, tmp_path, monkeypatch):
        rows = [
            {"Id": "a", "East": 100.0, "North": 200.0, "Precision": 10, "County": "Agder"},
            {"Id": "b", "East": 300.0, "North": 400.0, "Precision": 10, "County": "Vestland"},
            {"Id": "c", "East": 500.0, "North": 600.0, "Precision": 10, "County": "Aust-Agder"},
            {"Id": "d", "East": 700.0, "North": 800.0, "Precision": 10, "County": "Vest-Agder"},
        ]
        self.write(tmp_path, monkeypatch, rows)
        got = rm.presences({"key": "x"}, Everywhere(), max_prec=250, thin_m=0)
        assert sorted(got) == [(100.0, 200.0), (500.0, 600.0), (700.0, 800.0)]

    def test_coarse_records_are_dropped(self, tmp_path, monkeypatch):
        rows = [
            {"Id": "a", "East": 100.0, "North": 200.0, "Precision": 10, "County": "Agder"},
            {"Id": "b", "East": 300.0, "North": 400.0, "Precision": 5000, "County": "Agder"},
            {"Id": "c", "East": 500.0, "North": 600.0, "Precision": None, "County": "Agder"},
        ]
        self.write(tmp_path, monkeypatch, rows)
        got = rm.presences({"key": "x"}, Everywhere(), max_prec=250, thin_m=0)
        assert got == [(100.0, 200.0)]

    def test_points_outside_the_boundary_are_dropped(self, tmp_path, monkeypatch):
        rows = [
            {"Id": "a", "East": 100.0, "North": 200.0, "Precision": 10, "County": "Agder"},
            {"Id": "b", "East": 999.0, "North": 999.0, "Precision": 10, "County": "Agder"},
        ]
        self.write(tmp_path, monkeypatch, rows)
        got = rm.presences({"key": "x"}, OnlyFirst(), max_prec=250, thin_m=0)
        assert got == [(100.0, 200.0)]


class Everywhere:
    def __contains__(self, pt):
        return True


class OnlyFirst:
    def __contains__(self, pt):
        return pt == (100.0, 200.0)


class TestPublishedOutputPath:
    # `rm.OUT` became `rm.out_path()` when the second extent arrived, because
    # the destination now depends on which extent is running. The behaviour
    # being pinned is unchanged: an untagged Agder run writes the file FINDINGS
    # quotes, and nothing else may.
    def test_untagged_runs_write_model_results_md(self):
        assert rm.out_path().name == "model_results.md"
        assert rm.out_path().parent.name == "processed"

    def test_the_null_ceiling_is_not_quietly_relaxed(self):
        assert rm.NULL_CEILING == 0.60


# ---------------------------------------------------------------------------
# The national extent, added afterwards. Everything above stays green.
# ---------------------------------------------------------------------------

NORGE_FILES = [
    "terrain_norge_32m.tif",
    "autumn_rain_norge_1km.tif",
    "sr16_norge/SRRTRESLAG.tif",
    "sr16_norge/SRRVOLMB.tif",
    "sr16_norge/SRRGRFLATE.tif",
    "sr16_norge/SRRMHOYDE.tif",
    "sr16_norge/SRRLAI.tif",
    "sr16_norge/SRRTREALDER.tif",
    "sr16_norge/SRRKRONEDEK.tif",
    "sr16_norge/SRRBONITET.tif",
    "losmasser_norge_32m.tif",
    "ar50_norge_32m.tif",
]


@pytest.fixture
def national(monkeypatch):
    monkeypatch.setattr(rm, "EXTENT", rm.NORGE.key)
    monkeypatch.setattr(rm, "INCLUDE_TWI", False)


class TestTheTwoExtentsShareNothing:
    """The failure mode is not a crash. It is a plausible file in the wrong place."""

    def test_no_raster_path_is_shared(self, fake_interim, national):
        fake_interim(AGDER_FILES + NORGE_FILES)
        norge = set(names_of(rm.layers()))
        rm.EXTENT = rm.AGDER.key
        agder = set(names_of(rm.layers()))
        assert not (norge & agder)

    def test_the_output_files_differ(self, national):
        national_out = rm.out_path()
        rm.EXTENT = rm.AGDER.key
        assert national_out != rm.out_path()
        assert national_out.name == "model_results_norge.md"

    def test_the_background_pools_differ(self):
        assert rm.AGDER.pool != rm.NORGE.pool

    def test_a_national_run_cannot_reach_phase_zeros_results_file(self, national):
        # Structural, not a check: there is no argument combination that makes
        # --national write model_results.md, because the extent picks the name.
        assert rm.out_path().name != rm.AGDER.report


class TestNationalStack:
    def test_it_reads_the_national_rasters(self, fake_interim, national):
        fake_interim(AGDER_FILES + NORGE_FILES)
        got = names_of(rm.layers())
        assert got[0] == "terrain_norge_32m.tif"
        assert got[1] == "autumn_rain_norge_1km.tif"
        assert got[-2:] == ["losmasser_norge_32m.tif", "ar50_norge_32m.tif"]
        assert got.count("SRRVOLMB.tif") == 2      # estimate, then its bounds

    def test_the_stack_has_the_same_shape_minus_twi(self, fake_interim, national):
        fake_interim(AGDER_FILES + NORGE_FILES)
        n_national = len(rm.layers())
        rm.EXTENT = rm.AGDER.key
        assert n_national == len(rm.layers())      # same layers, different files

    def test_terrain_has_five_bands_and_no_twi(self, national):
        assert [n for _, n in rm.NORGE.terrain_bands] == [
            "elevation", "slope_deg", "northness", "eastness", "curvature"]
        assert "twi" not in [n for _, n in rm.NORGE.terrain_bands]

    def test_all_five_bands_means_bands_is_none(self, fake_interim, national):
        # bands=None asks for every band the raster has, which is right only
        # because the national raster has exactly the five listed. If a sixth
        # were ever written into it, this and the band-name list disagree.
        fake_interim(NORGE_FILES)
        assert rm.layers()[0].bands is None

    def test_dropping_elevation_still_names_the_rest_correctly(self, fake_interim,
                                                               national, monkeypatch):
        fake_interim(NORGE_FILES)
        monkeypatch.setattr(rm, "INCLUDE_ELEVATION", False)
        terrain = rm.layers()[0]
        assert terrain.bands == [2, 3, 4, 5]
        assert terrain.names == ["slope_deg", "northness", "eastness", "curvature"]


class TestNationalPresenceFiltering:
    def test_no_county_filter_nationally(self, tmp_path, monkeypatch, national):
        """Fourteen polygons, not three names."""
        rows = [
            {"Id": "a", "East": 100.0, "North": 200.0, "Precision": 10, "County": "Agder"},
            {"Id": "b", "East": 300.0, "North": 400.0, "Precision": 10, "County": "Vestland"},
            {"Id": "c", "East": 500.0, "North": 600.0, "Precision": 10, "County": "Troms"},
        ]
        (tmp_path / "x.json").write_text(json.dumps({"observations": rows}),
                                         encoding="utf-8")
        monkeypatch.setattr(rm, "RAW", tmp_path)
        got = rm.presences({"key": "x"}, Everywhere(), max_prec=250, thin_m=0)
        assert len(got) == 3

    def test_the_boundary_is_what_excludes_the_holdout(self, tmp_path, monkeypatch,
                                                      national):
        rows = [
            {"Id": "a", "East": 100.0, "North": 200.0, "Precision": 10, "County": "Agder"},
            {"Id": "b", "East": 900.0, "North": 900.0, "Precision": 10, "County": "Nordland"},
        ]
        (tmp_path / "x.json").write_text(json.dumps({"observations": rows}),
                                         encoding="utf-8")
        monkeypatch.setattr(rm, "RAW", tmp_path)
        got = rm.presences({"key": "x"}, OnlyFirst(), max_prec=250, thin_m=0)
        assert got == [(100.0, 200.0)]

    def test_the_agder_prefilter_is_still_applied_by_default(self, tmp_path,
                                                            monkeypatch):
        """No --national means the Agder path, county filter and all."""
        rows = [{"Id": "b", "East": 300.0, "North": 400.0, "Precision": 10,
                 "County": "Vestland"}]
        (tmp_path / "x.json").write_text(json.dumps({"observations": rows}),
                                         encoding="utf-8")
        monkeypatch.setattr(rm, "RAW", tmp_path)
        assert rm.presences({"key": "x"}, Everywhere(), 250, 0) == []


class TestTransferPool:
    def test_agder_uses_one_pool(self, fake_interim):
        d = fake_interim([])
        (d / rm.AGDER.pool).write_text(
            json.dumps({"pool": [[1.0, 2.0, 10.0, "a"]]}), encoding="utf-8")
        assert len(rm.transfer_pool()) == 1

    def test_national_uses_both_sides(self, fake_interim, national):
        # Background inside the held-out region is what the held-out AUC is
        # measured against. Without it there is nothing to rank presences
        # above, and the number would be about the training region instead.
        d = fake_interim([])
        (d / rm.NORGE.pool).write_text(json.dumps({"pools": {
            "training": {"points": [[1.0, 2.0], [3.0, 4.0]]},
            "holdout": {"points": [[9.0, 9.0]]}}}), encoding="utf-8")
        got = rm.transfer_pool()
        assert [(p["east"], p["north"]) for p in got] == [
            (1.0, 2.0), (3.0, 4.0), (9.0, 9.0)]


class TestTransferFlags:
    """The transfer ablation has to match the blocked-CV one, or the contrast
    between them - which is the whole point of running both - compares two
    different stacks."""

    def run_argv(self, monkeypatch, *args):
        import run_transfer as rt
        monkeypatch.setattr(rm, "EXTENT", rm.AGDER.key)
        monkeypatch.setattr(rm, "INCLUDE_CLIMATE", True)
        monkeypatch.setattr(rm, "INCLUDE_TWI", True)
        monkeypatch.setattr(rm, "INCLUDE_VOL_BOUNDS", True)
        monkeypatch.setattr(rm, "transfer_regions",
                            lambda area: (_ for _ in ()).throw(SystemExit("stop")))
        monkeypatch.setattr(sys, "argv", ["run_transfer.py", *args])
        with pytest.raises(SystemExit, match="stop"):
            rt.main()

    def test_no_climate_sets_the_same_flag_run_models_uses(self, monkeypatch):
        self.run_argv(monkeypatch, "--national", "--no-climate")
        assert rm.INCLUDE_CLIMATE is False
        assert rm.EXTENT == rm.NORGE.key

    def test_national_alone_leaves_climate_on(self, monkeypatch):
        self.run_argv(monkeypatch, "--national")
        assert rm.INCLUDE_CLIMATE is True


class TestTransferReportPaths:
    def test_the_two_extents_write_different_files(self, monkeypatch):
        import run_transfer as rt
        monkeypatch.setattr(rm, "EXTENT", rm.AGDER.key)
        agder = rt.out_path()
        monkeypatch.setattr(rm, "EXTENT", rm.NORGE.key)
        national = rt.out_path()
        assert agder.name == "transferability.md"
        assert national.name == "transferability_norge.md"
        assert agder != national


class TestFlagHandling:
    """Checked before any file is opened, so these are cheap and total."""

    def run_argv(self, monkeypatch, *args):
        monkeypatch.setattr(rm, "EXTENT", rm.AGDER.key)
        monkeypatch.setattr(rm, "INCLUDE_TWI", True)
        monkeypatch.setattr(rm, "INCLUDE_ELEVATION", True)
        monkeypatch.setattr(rm, "INCLUDE_VOL_BOUNDS", True)
        monkeypatch.setattr(rm, "INCLUDE_CLIMATE", True)
        monkeypatch.setattr(sys, "argv", ["run_models.py", *args])
        return rm.main()

    def test_no_climate_sets_the_flag_and_tags_the_run(self, monkeypatch):
        monkeypatch.setattr(rm, "cv_boundary",
                            lambda area: (_ for _ in ()).throw(SystemExit("stop")))
        with pytest.raises(SystemExit, match="stop"):
            self.run_argv(monkeypatch, "--national", "--no-climate")
        assert rm.INCLUDE_CLIMATE is False
        assert rm.EXTENT == rm.NORGE.key

    def test_no_twi_with_national_is_refused(self, monkeypatch):
        # Silently accepting it would tag a run "no-twi" that never had TWI,
        # and invite the reader to compare it against the Agder ablation.
        with pytest.raises(SystemExit, match="no TWI band"):
            self.run_argv(monkeypatch, "--national", "--no-twi")

    def test_national_switches_the_extent_and_turns_twi_off(self, monkeypatch):
        # Stop at the first thing needing real data, after the flags are read.
        monkeypatch.setattr(rm, "cv_boundary",
                            lambda area: (_ for _ in ()).throw(SystemExit("stop")))
        with pytest.raises(SystemExit, match="stop"):
            self.run_argv(monkeypatch, "--national")
        assert rm.EXTENT == rm.NORGE.key
        assert rm.INCLUDE_TWI is False

    def test_the_default_is_still_agder_with_twi(self, monkeypatch):
        monkeypatch.setattr(rm, "cv_boundary",
                            lambda area: (_ for _ in ()).throw(SystemExit("stop")))
        with pytest.raises(SystemExit, match="stop"):
            self.run_argv(monkeypatch)
        assert rm.EXTENT == rm.AGDER.key
        assert rm.INCLUDE_TWI is True


class TestTargetGroupPool:
    def test_agder_flat_pool(self, fake_interim, monkeypatch):
        d = fake_interim([])
        (d / rm.AGDER.pool).write_text(
            json.dumps({"pool": [[1.0, 2.0, 10.0, "id1"], [3.0, 4.0, 20.0, "id2"]]}),
            encoding="utf-8")
        got = rm.target_group_pool()
        assert [(p["east"], p["north"]) for p in got] == [(1.0, 2.0), (3.0, 4.0)]
        assert got[0]["precision_m"] == 10.0

    def test_national_split_pool_takes_training_by_default(self, fake_interim,
                                                           national):
        d = fake_interim([])
        (d / rm.NORGE.pool).write_text(json.dumps({"pools": {
            "training": {"points": [[1.0, 2.0], [3.0, 4.0]]},
            "holdout": {"points": [[9.0, 9.0]]}}}), encoding="utf-8")
        train = rm.target_group_pool()
        hold = rm.target_group_pool("holdout")
        assert [(p["east"], p["north"]) for p in train] == [(1.0, 2.0), (3.0, 4.0)]
        assert [(p["east"], p["north"]) for p in hold] == [(9.0, 9.0)]

    def test_a_missing_pool_names_the_script_that_builds_it(self, fake_interim,
                                                            national):
        fake_interim([])
        with pytest.raises(SystemExit, match="build_background_pool"):
            rm.target_group_pool()

    def test_an_unknown_role_is_refused(self, fake_interim, national):
        d = fake_interim([])
        (d / rm.NORGE.pool).write_text(
            json.dumps({"pools": {"training": {"points": []}}}), encoding="utf-8")
        with pytest.raises(SystemExit, match="no 'holdout' pool"):
            rm.target_group_pool("holdout")


class TestCvBoundary:
    def area(self, tmp_path, exclude="18"):
        return {
            "spatial_cv": {"cv_extent": "training_extent"},
            "training_extent": {"name": "Agder fylke",
                                "boundary_file": "data/interim/b.geojson"},
            "national_training_extent": {
                "name": "Norway minus the held-out region",
                "boundary_dir": str(tmp_path.relative_to(ROOT))
                if tmp_path.is_relative_to(ROOT) else str(tmp_path),
                "exclude_code": exclude},
        }

    def write_fylke(self, d, name, code, x0):
        ring = [[x0, 0], [x0 + 10, 0], [x0 + 10, 10], [x0, 10], [x0, 0]]
        (d / f"boundary_{name}_25833.geojson").write_text(json.dumps({
            "type": "FeatureCollection", "crs_declared_by_source": "EPSG:25833",
            "features": [{"type": "Feature",
                          "properties": {"fylkesnavn": name, "fylkesnummer": code},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}}],
        }), encoding="utf-8")

    def test_the_excluded_fylke_is_not_in_the_region(self, tmp_path, monkeypatch,
                                                    national):
        self.write_fylke(tmp_path, "agder", "42", 0)
        self.write_fylke(tmp_path, "nordland", "18", 100)
        monkeypatch.setattr(rm, "ROOT", pathlib.Path("/"))
        area = self.area(tmp_path)
        area["national_training_extent"]["boundary_dir"] = str(tmp_path)
        region = rm.cv_boundary(area)
        assert (5, 5) in region
        assert (105, 5) not in region
        assert len(region) == 1

    def test_a_missing_exclude_code_is_refused(self, tmp_path, monkeypatch, national):
        # Nothing excluded means Nordland silently inside the training extent,
        # and a transfer test measuring a model against its own training data.
        self.write_fylke(tmp_path, "agder", "42", 0)
        monkeypatch.setattr(rm, "ROOT", pathlib.Path("/"))
        area = self.area(tmp_path)
        area["national_training_extent"]["boundary_dir"] = str(tmp_path)
        with pytest.raises(SystemExit, match="no fylke with code 18"):
            rm.cv_boundary(area)

    def test_the_national_draw_region_includes_the_holdout(self, tmp_path,
                                                           monkeypatch, national):
        """The property the whole national design rests on.

        Blocked CV must never see Nordland, and the transfer test must be able
        to pull presences from it. Those are different regions, and getting
        them the same way round would either leak the held-out data into
        fitting or leave the transfer test with nothing to evaluate.
        """
        d = tmp_path / "fylker"
        d.mkdir()
        self.write_fylke(d, "agder", "42", 0)
        self.write_fylke(d, "nordland", "18", 100)
        monkeypatch.setattr(rm, "ROOT", tmp_path)
        area = {
            "spatial_cv": {"cv_extent": "training_extent"},
            "national_training_extent": {"name": "Norway minus the held-out region",
                                         "boundary_dir": str(d),
                                         "exclude_code": "18"},
            "national_holdout_extent": {"name": "Nordland fylke",
                                        "boundary_file": "fylker/boundary_nordland_25833.geojson"},
        }
        fitting = rm.cv_boundary(area)
        draw, held = rm.transfer_regions(area)
        in_nordland = (105.0, 5.0)
        assert in_nordland not in fitting, "the held-out region reached fitting"
        assert in_nordland in draw, "no held-out presence could ever be drawn"
        assert in_nordland in held
        assert (5.0, 5.0) in fitting and (5.0, 5.0) in draw
        assert (5.0, 5.0) not in held

    def test_a_wrong_crs_is_refused(self, tmp_path, monkeypatch, national):
        self.write_fylke(tmp_path, "agder", "42", 0)
        self.write_fylke(tmp_path, "nordland", "18", 100)
        f = tmp_path / "boundary_agder_25833.geojson"
        blob = json.loads(f.read_text(encoding="utf-8"))
        blob["crs_declared_by_source"] = "EPSG:25832"
        f.write_text(json.dumps(blob), encoding="utf-8")
        monkeypatch.setattr(rm, "ROOT", pathlib.Path("/"))
        area = self.area(tmp_path)
        area["national_training_extent"]["boundary_dir"] = str(tmp_path)
        with pytest.raises(SystemExit, match="25832"):
            rm.cv_boundary(area)
