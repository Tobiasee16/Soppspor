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
    def test_untagged_runs_write_model_results_md(self):
        assert rm.OUT.name == "model_results.md"
        assert rm.OUT.parent.name == "processed"

    def test_the_null_ceiling_is_not_quietly_relaxed(self):
        assert rm.NULL_CEILING == 0.60
