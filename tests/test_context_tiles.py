"""The card's reasoning has to describe the cell it is under.

Grid alignment is the failure that shows nothing. Every layer reads, every
number is plausible, and the sentence under a score describes the wood next
door - a beginner walks to the wrong hillside and the map never looks wrong.
So each reader is checked against the coordinates it claims to be reading, and
the byte encoding is checked against the interface's own decoder rather than
against a copy of it.

The three readers exist because the sources do not share a grid: land cover and
substrate are already on the 32 m national grid, SR16 is 16 m on its own
origin, and the rainfall climatology is 1 km on a third. Each is a different
kind of mistake waiting to happen.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_zone_tiles as ezt

ect = pytest.importorskip("export_context_tiles",
                          reason="needs the national extent config")

NODE = shutil.which("node")

CELL = 128.0
OX, OY = 100_000.25, 7_000_000.5      # deliberately not round: the real one is not


def grid(w=8, h=8, ox=OX, oy=OY):
    return {"cell_m": CELL, "origin_x": ox, "origin_y": oy,
            "w": w, "h": h, "tile_px": ezt.TILE, "tiles": []}


def write(path, arr, res, ox, oy, dtype, nodata=None):
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1],
                       height=arr.shape[0], count=1, dtype=dtype,
                       crs="EPSG:25833", nodata=nodata,
                       transform=from_origin(ox, oy, res, res)) as ds:
        ds.write(arr.astype(dtype), 1)
    return path


class TestTheThirtyTwoMetreReader:
    def test_a_measurement_is_the_mean_of_its_block(self, tmp_path):
        a = np.arange(32 * 32, dtype="float32").reshape(32, 32)
        p = write(tmp_path / "n.tif", a, 32.0, OX, OY, "float32")
        got = ect.read_grid32(p, grid(), "num")
        want = a.reshape(8, 4, 8, 4).mean(axis=(1, 3))
        np.testing.assert_allclose(got, want, rtol=1e-6)

    def test_nodata_does_not_drag_the_mean_down(self, tmp_path):
        a = np.full((32, 32), 100.0, dtype="float32")
        a[0, :2] = -9999.0
        p = write(tmp_path / "n.tif", a, 32.0, OX, OY, "float32", nodata=-9999.0)
        got = ect.read_grid32(p, grid(), "num")
        assert got[0, 0] == pytest.approx(100.0), "nodata was averaged in"

    def test_a_block_with_nothing_in_it_has_no_value(self, tmp_path):
        a = np.full((32, 32), 100.0, dtype="float32")
        a[:4, :4] = -9999.0
        p = write(tmp_path / "n.tif", a, 32.0, OX, OY, "float32", nodata=-9999.0)
        got = ect.read_grid32(p, grid(), "num")
        assert not np.isfinite(got[0, 0]) and got[0, 1] == pytest.approx(100.0)

    def test_a_class_takes_the_majority_not_the_mean(self, tmp_path):
        """Halfway between bog and bare rock is not a substrate."""
        a = np.zeros((32, 32), dtype="uint8")
        a[0:4, 0:4] = 9                       # all bedrock
        a[0:4, 4:8] = 6
        a[0:2, 4:8] = 9                       # 8 bedrock, 8 peat -> a tie
        a[4:8, 0:4] = 6
        a[4, 0] = 9                           # 15 peat, 1 bedrock
        p = write(tmp_path / "c.tif", a, 32.0, OX, OY, "uint8", nodata=0)
        got = ect.read_grid32(p, grid(), "cat", codes=[6, 9])
        assert got[0, 0] == 9
        assert got[1, 0] == 6, "a clear majority lost"
        # A mean would have produced 7.5, which names nothing at all.
        assert got[0, 1] in (6, 9)

    def test_a_tie_goes_to_the_first_code_every_time(self, tmp_path):
        a = np.zeros((32, 32), dtype="uint8")
        a[0:4, 0:2] = 6
        a[0:4, 2:4] = 9
        p = write(tmp_path / "c.tif", a, 32.0, OX, OY, "uint8", nodata=0)
        first = ect.read_grid32(p, grid(), "cat", codes=[6, 9])[0, 0]
        again = ect.read_grid32(p, grid(), "cat", codes=[6, 9])[0, 0]
        assert first == again == 6

    def test_a_grid_that_does_not_tile_the_source_is_refused(self, tmp_path):
        a = np.zeros((32, 32), dtype="float32")
        p = write(tmp_path / "n.tif", a, 32.0, OX + 7.0, OY, "float32")
        with pytest.raises(SystemExit):
            ect.read_grid32(p, grid(), "num")


class TestTheSixteenMetreReader:
    """SR16 is 16 m on its own origin. The offsets work out exactly, and that
    is a fact worth a test rather than an assumption worth a comment."""

    def make(self, tmp_path, offset_cells=4):
        # An origin a whole number of 16 m cells away from the zone grid, which
        # is what the national files actually are.
        ox = OX - offset_cells * 16.0
        oy = OY + offset_cells * 16.0
        a = np.arange(128 * 128, dtype="int16").reshape(128, 128)
        # Where cell (0,0)'s *centre* lands in the source: the origin offset
        # plus half a zone cell. Not the offset itself - that is the corner,
        # and sampling corners would shift every value by half a cell.
        first = offset_cells + int(CELL / 2 / 16)
        return write(tmp_path / "sr16.tif", a, 16.0, ox, oy, "int16",
                     nodata=-9999), a, first

    def test_it_samples_the_cell_centre(self, tmp_path):
        p, a, first = self.make(tmp_path)
        got = ect.read_sr16(p, grid())
        for r in range(8):
            for c in range(8):
                assert got[r, c] == a[first + 8 * r, first + 8 * c], (r, c)

    def test_nodata_comes_back_missing_rather_than_as_minus_nine_thousand(self, tmp_path):
        p, a, first = self.make(tmp_path)
        with rasterio.open(p, "r+") as ds:
            b = ds.read(1)
            b[first, first] = -9999
            ds.write(b, 1)
        got = ect.read_sr16(p, grid())
        assert not np.isfinite(got[0, 0])
        assert np.isfinite(got[0, 1])

    def test_a_misaligned_origin_is_refused(self, tmp_path):
        a = np.zeros((128, 128), dtype="int16")
        p = write(tmp_path / "bad.tif", a, 16.0, OX - 3.0, OY, "int16")
        with pytest.raises(SystemExit):
            ect.read_sr16(p, grid())


class TestTheCoarseReader:
    def test_every_cell_takes_the_block_its_centre_is_in(self, tmp_path):
        a = np.arange(4 * 4, dtype="float32").reshape(4, 4) * 10
        ox, oy = OX - 500.0, OY + 500.0
        p = write(tmp_path / "km.tif", a, 1000.0, ox, oy, "float32")
        got = ect.read_coarse(p, grid())
        for r in range(8):
            for c in range(8):
                e = OX + (c + 0.5) * CELL
                n = OY - (r + 0.5) * CELL
                want = a[int((oy - n) // 1000.0), int((e - ox) // 1000.0)]
                assert got[r, c] == want, (r, c)
        # Eight 128 m cells span more than one kilometre, so this is not the
        # same value everywhere - otherwise the check above proves nothing.
        assert len(np.unique(got)) > 1

    def test_outside_the_source_is_missing_not_zero(self, tmp_path):
        a = np.ones((2, 2), dtype="float32")
        p = write(tmp_path / "km.tif", a, 1000.0, OX, OY, "float32")
        got = ect.read_coarse(p, grid(w=64, h=64))
        assert not np.isfinite(got[-1, -1]), "off the edge read as a value"


class TestTheByteEncoding:
    def decode(self, b, lo, hi):
        """What `val()` in shell.html does, and what `ctxAt` does with a tile."""
        return None if b == 0 else lo + (b - 1) / 254.0 * (hi - lo)

    def test_a_class_code_survives_exactly(self):
        """A rounded class code that lands one off names a different substrate."""
        codes = np.array([[10, 20, 30, 50, 60, 70, 81, 82]], dtype="float32")
        b, lo, hi = ect.to_bytes(codes, "cat")
        for want, byte in zip(codes.ravel(), b.ravel()):
            assert round(self.decode(int(byte), lo, hi)) == want

    def test_no_value_stays_no_value(self):
        b, lo, hi = ect.to_bytes(np.array([[0.0, 30.0]], dtype="float32"), "cat")
        assert b[0, 0] == 0 and self.decode(int(b[0, 0]), lo, hi) is None
        b2, lo2, hi2 = ect.to_bytes(np.array([[np.nan, 5.0]], dtype="float32"), "num")
        assert b2[0, 0] == 0

    def test_a_measurement_round_trips_within_one_step(self):
        rng = np.random.default_rng(4)
        a = (rng.random((40, 40)) * 300).astype("float32")
        b, lo, hi = ect.to_bytes(a, "num")
        step = (hi - lo) / 254.0
        back = np.array([[self.decode(int(v), lo, hi) for v in row] for row in b])
        assert np.abs(back - a).max() <= step / 2 + 1e-6

    def test_a_flat_layer_does_not_divide_by_zero(self):
        b, lo, hi = ect.to_bytes(np.full((4, 4), 7.0, dtype="float32"), "num")
        assert hi > lo and (b == 1).all()
        assert self.decode(1, lo, hi) == pytest.approx(7.0)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestTheCardReadsIt:
    """End to end: a tile on disk, the real shell, a tap, and the sentence it
    produces. The encoding could be right in Python and read back wrong."""

    LAYERS = [
        {"key": "ar50", "no": "arealtype", "en": "land cover", "kind": "cat",
         "min": 0.0, "max": 254.0,
         "labels": {"30": ["Skog", "forest"], "60": ["Myr", "mire"]}},
        {"key": "treslag", "no": "treslag", "en": "tree species", "kind": "cat",
         "min": 0.0, "max": 254.0,
         "labels": {"1": ["Gran", "Spruce"], "2": ["Furu", "Pine"]}},
        {"key": "stand_age_yr", "no": "bestandsalder", "en": "stand age",
         "kind": "num", "min": 0.0, "max": 254.0},
        {"key": "elevation", "no": "hoyde", "en": "elevation",
         "kind": "num", "min": 0.0, "max": 1270.0},
    ]

    @pytest.fixture(scope="class")
    def run(self, tmp_path_factory):
        import test_zone_window as tzw

        tmp = tmp_path_factory.mktemp("ctxcard")
        tzw.prepare(tmp, tzw.country(), n_levels=2)
        payload = json.loads((tmp / "payload.json").read_text(encoding="utf-8"))

        # One context tile covering the north-west corner, with a value per
        # layer chosen so a wrong band, a wrong offset or a wrong decode all
        # produce something visibly different.
        T = ezt.TILE
        stack = np.zeros((T * len(self.LAYERS), T), dtype="uint8")
        stack[0 * T:1 * T, :] = 31          # ar50 30  -> Skog
        stack[1 * T:2 * T, :] = 2           # treslag 1 -> Gran
        stack[2 * T:3 * T, :] = 74          # stand age 73
        stack[3 * T:4 * T, :] = 41          # elevation 200 m
        # The tile the tapped point falls in, computed rather than assumed:
        # a fixture written to the wrong tile leaves the card waiting forever
        # and every assertion below fails for the wrong reason.
        tap_e, tap_n = tzw.PROBES[1]
        t_row = int((tzw.NOY - tap_n) // tzw.CELL) // T
        t_col = int((tap_e - tzw.NOX) // tzw.CELL) // T
        (tmp / "context").mkdir()
        (tmp / "context" / f"{t_row}_{t_col}.png").write_bytes(ezt.png_gray(stack))

        payload["ctx"] = {
            "url": "context/{row}_{col}.png", "cell_m": tzw.CELL,
            "tile_px": T, "origin_x": tzw.NOX, "origin_y": tzw.NOY,
            "grid": {"w": tzw.NW, "h": tzw.NH}, "layers": self.LAYERS}
        (tmp / "payload.json").write_text(json.dumps(payload), encoding="utf-8")

        plan = {"bounds": tzw.BOUNDS, "steps": [
            {"name": "tap", "view": tzw.WEST + [25], "tap": tzw.PROBES[1]}]}
        (tmp / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        out = subprocess.run(
            [NODE, str(ROOT / "tests" / "national_boot.js"), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", timeout=900)
        if out.returncode != 0:
            raise AssertionError(f"node failed: {out.stderr[-1500:]}")
        got = json.loads(out.stdout)
        assert got["boot"], got.get("error")
        return got["steps"][0]

    def test_the_card_names_the_ground(self, run):
        card = run["card"]
        assert card, "the card rendered nothing"
        assert "Skog" in card or "skog" in card, card[:300]
        assert "Gran" in card or "gran" in card, card[:300]

    def test_it_reports_the_numbers_the_tile_holds(self, run):
        card = run["card"]
        assert "~73 år" in card, card[:400]          # the lead sentence
        assert "<dd>73 år</dd>" in card, card[-600:]   # and the detail row
        assert "200 moh." in card, card[:400]
        assert "NaN" not in card, "a layer this payload lacks printed as NaN"

    def test_it_does_not_offer_a_wetness_index_it_does_not_have(self, run):
        """The national model has no TWI. A dash against a named quantity reads
        as "missing here" when it is missing everywhere, by decision."""
        assert "TWI" not in run["card"]
