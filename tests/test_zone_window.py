"""The window of tiles the app loads must behave like the whole country.

`tests/test_national_rank.py` pins the *arithmetic*: given a window of bytes,
the interface's habitat field matches what the national surface would give.
This pins the machinery around it - fetching tiles, assembling them into a
grid, rebinding what GW, GH, OX and OY mean, and throwing the right caches away
when the window moves. All of that can be wrong while every number in it is
right.

The failure this suite exists to catch is a quiet one. A window assembled one
tile out of place, or a zone path drawn against the origin of the window that
replaced the one it was traced in, produces a map that renders perfectly and
puts the good ground in the wrong wood. Nothing throws, nothing looks odd, and
somebody walks to the wrong hillside.

So the country is synthetic and known, the tiles are cut by the real exporter,
and the shell is the real shell - booted under node by tests/national_boot.js
against a fetch that reads those tiles off disk.
"""
from __future__ import annotations

import base64
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import zlib

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_zone_tiles as ezt
from tests.test_national_rank import national_rank

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

CELL = 128.0
NOX, NOY = 100_000.0, 7_000_000.0
NW, NH = 1024, 1024              # cells: 4 x 4 tiles, and more than one window
KEYS = ["craterellus_tubaeformis", "cantharellus_cibarius"]

# Room to pan well off the data, as the hosted build has: BOUNDS is the
# country, and the scored grid is only the part of it the model reached.
BOUNDS = [NOX - 200_000, NOY - NH * CELL - 200_000,
          NOX + NW * CELL + 200_000, NOY + 200_000]


def country() -> np.ndarray:
    """A smooth surface with a sea margin, a south-north gradient, and one
    tile that holds nothing at all.

    Smooth on purpose. A noisy field makes the contour tracer walk hundreds of
    thousands of segments per level, which turns a behavioural test into a
    benchmark; how the encoding behaves on a skewed distribution is pinned in
    tests/test_zone_tiles.py, where it belongs.
    """
    rng = np.random.default_rng(11)
    yy, xx = np.mgrid[0:NH, 0:NW]
    base = np.exp(2.2 * (yy / NH)) * (1.0 + 0.55 * np.sin(xx / 41.0)
                                      * np.cos(yy / 59.0))
    g = (base * (1.0 + rng.normal(0, 0.02, base.shape))).astype("float32")
    g[:40, :] = np.nan
    g[-40:, :] = np.nan
    g[:, :40] = np.nan
    g[:, -40:] = np.nan
    # One whole tile with nothing in it. The exporter never writes that tile,
    # and the app must know better than to ask for it.
    g[:ezt.TILE, :ezt.TILE] = np.nan
    return g


def world(col: float, row: float) -> list[float]:
    """The centre of a cell, in metres."""
    return [NOX + (col + 0.5) * CELL, NOY - (row + 0.5) * CELL]


STEP = 4          # between pyramid levels, as in the exporter


def pyramid(grid: np.ndarray, n_levels: int):
    """Levels the way `export_zone_tiles` builds them: sums and counts down.

    Not a mean of means. The exporter carries counts precisely so a coarse cell
    is the mean of the source cells under it rather than of the coarse cells
    above it, and a fixture that took the shortcut would not be exercising what
    ships.
    """
    sums = np.where(np.isfinite(grid), grid, 0.0).astype("float32")
    counts = np.isfinite(grid).astype("float32")
    out = []
    for z in range(n_levels):
        if z:
            sums, counts = ezt.coarsen(sums, counts, STEP)
        level = ezt.means(sums, counts)
        byts, n = ezt.to_percentile_bytes(level)
        out.append({
            "cell_m": CELL * (STEP ** z),
            "grid": {"w": int(level.shape[1]), "h": int(level.shape[0])},
            "tiles": {"cols": (level.shape[1] + ezt.TILE - 1) // ezt.TILE,
                      "rows": (level.shape[0] + ezt.TILE - 1) // ezt.TILE},
            "bytes": byts, "lut": ezt.smoothed_rank_lut(level), "n": n,
        })
    return out


def prepare(tmp: pathlib.Path, grid: np.ndarray, n_levels: int = 3,
            flat: bool = False) -> np.ndarray:
    """Cut real tiles and write the payload a national build would ship.

    `flat` writes the older single-level shape - one list of tile ids, one
    lookup - which the interface still has to read.
    """
    levels = pyramid(grid, n_levels)
    species = []
    for key in KEYS:
        ids = []
        for z, lv in enumerate(levels):
            dest = (tmp / "zones" / key) if flat else (tmp / "zones" / key / str(z))
            ids.append(ezt.cut_tiles(lv["bytes"], dest))
        species.append({
            "key": key, "scientific": key.replace("_", " ").title(),
            "no": key.split("_")[0], "en": key.split("_")[0],
            "is_null": False, "transfer_auc": 0.71, "transfer_ci": [0.64, 0.78],
            "held_out_n": 120, "scored_cells": levels[0]["n"],
            "tiles": ids[0] if flat else ids,
            "smoothed_rank_lut": levels[0]["lut"] if flat
                                 else [lv["lut"] for lv in levels],
        })
    meta = {"crs": "EPSG:25833", "cell_m": CELL, "tile_px": ezt.TILE,
            "percentile_bytes": True, "origin_x": NOX, "origin_y": NOY,
            "null_transfer_auc": 0.49, "excluded_species": [],
            "tiles": {"url": "zones/{sp}/{row}_{col}.png" if flat
                             else "zones/{sp}/{z}/{row}_{col}.png",
                      "cols": levels[0]["tiles"]["cols"],
                      "rows": levels[0]["tiles"]["rows"]}}
    if not flat:
        meta["levels"] = [{k: lv[k] for k in ("cell_m", "grid", "tiles")}
                          for lv in levels]
    payload = {"meta": meta, "grid": {"w": NW, "h": NH},
               "species": species, "context": {}, "categorical": {}}
    (tmp / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    return levels[0]["bytes"]


def drive(tmp: pathlib.Path, plan: dict) -> dict:
    (tmp / "plan.json").write_text(json.dumps(dict(plan, bounds=BOUNDS)),
                                   encoding="utf-8")
    # encoding= explicitly: node writes UTF-8 and Python on Windows would
    # otherwise decode it with the locale's cp1252, turning every "å" the card
    # prints into mojibake.
    out = subprocess.run([NODE, str(ROOT / "tests" / "national_boot.js"), str(tmp)],
                         capture_output=True, text=True, encoding="utf-8",
                         timeout=900)
    if out.returncode != 0:
        raise AssertionError(f"node failed: {out.stderr[-1500:]}")
    got = json.loads(out.stdout)
    assert got["boot"], got.get("error")
    got["by_name"] = {s["name"]: s for s in got["steps"]}
    return got


def win_bytes(step) -> np.ndarray:
    raw = base64.b64decode(step["bytes"])
    _, _, w, h = step["win"]
    return np.frombuffer(raw, dtype="uint8").reshape(h, w)


# Two viewpoints far enough apart that the second needs a window the first did
# not hold, with ground in common. The probes sit in that common ground, each
# well clear of either window's edge - the halo is what makes the edge cells
# right, and it is pinned in test_national_rank.py rather than here.
#
# They are in the south of the fixture deliberately. The surface rises that
# way, so probes there span most of the national rank (0.47 to 0.96) instead of
# crowding into the bottom tenth of it, which is what lets a conditions term
# that genuinely shifts the ranking show up as more than rounding.
WEST = world(150, 800)
EAST = [WEST[0] + 8000.0, WEST[1]]
PROBES = [world(165, 720), world(180, 800), world(195, 880)]
# A viewpoint whose window straddles the tile that holds nothing at all.
EMPTY_EDGE = world(150, 300)

PLAN = {"steps": [
    {"name": "west", "view": WEST + [25], "probes": PROBES, "bytes": True},
    {"name": "east", "view": EAST + [25], "probes": PROBES, "bytes": True},
    {"name": "empty", "view": EMPTY_EDGE + [25], "bytes": True},
    {"name": "far", "view": world(512, 512) + [400]},
    {"name": "back", "view": WEST + [25], "probes": PROBES},
    {"name": "two", "toggle": KEYS[1], "probes": PROBES},
]}


@pytest.fixture(scope="module")
def grid():
    return country()


@pytest.fixture(scope="module")
def run(tmp_path_factory, grid):
    tmp = tmp_path_factory.mktemp("zonewin")
    nat = prepare(tmp, grid)
    got = drive(tmp, PLAN)
    got["nat"] = nat
    return got


class TestTheWindowIsTheCountry:
    def test_it_boots_in_tiled_mode(self, run):
        assert run["tiled"] is True
        assert run["opened"]["settled"], "the opening window never assembled"

    def test_every_step_settled(self, run):
        bad = [s["name"] for s in run["steps"] if not s["settled"]]
        assert not bad, f"never settled: {bad}"

    def test_the_assembled_window_is_the_national_bytes(self, run):
        """Byte for byte, through fetch, the PNG decoder and the assembler."""
        for name in ("west", "east", "empty"):
            st = run["by_name"][name]
            x0, y0, w, h = st["win"]
            np.testing.assert_array_equal(win_bytes(st),
                                          run["nat"][y0:y0 + h, x0:x0 + w])

    def test_a_window_over_the_empty_tile_is_assembled_around_it(self, run):
        """The tile was never written, so the assembler has to leave a hole
        rather than shift everything after it along by one tile."""
        st = run["by_name"]["empty"]
        x0, y0, w, h = st["win"]
        assert y0 < ezt.TILE and x0 < ezt.TILE, "the window misses the empty tile"
        win = win_bytes(st)
        hole = win[:ezt.TILE - y0, :ezt.TILE - x0]
        assert hole.size and not hole.any(), "the empty tile came back with data"
        assert win[ezt.TILE - y0:].any(), "the rest of the window is empty too"

    def test_the_note_stays_out_of_the_way_when_there_are_zones(self, run):
        """This used to depend on the markup carrying `hidden`, not on the code:
        the first call was always "nothing to say", and the memo swallowed it."""
        for name in ("west", "east", "empty", "back", "two"):
            assert run["by_name"][name]["note"]["hidden"] is True, name

    def test_the_window_actually_moved(self, run):
        a, b = run["by_name"]["west"]["win"], run["by_name"]["east"]["win"]
        assert a != b, "the pan stayed inside one window; this proves nothing"

    def test_the_same_ground_ranks_the_same_from_both_windows(self, run):
        """The property the whole design exists for: panning cannot move a zone."""
        a = run["by_name"]["west"]["probes"]
        b = run["by_name"]["east"]["probes"]
        assert all(p is not None for p in a + b), "a probe fell outside its window"
        assert all(p["byte"] for p in a), "a probe landed on unscored ground"
        for pa, pb in zip(a, b):
            assert pa["byte"] == pb["byte"]
            assert abs(pa["h"] - pb["h"]) < 1e-6, f"{pa['h']} vs {pb['h']}"

    def test_the_rank_is_the_national_one(self, run, grid):
        want = national_rank(grid)
        for (e, n), p in zip(PROBES, run["by_name"]["west"]["probes"]):
            col, row = int((e - NOX) // CELL), int((NOY - n) // CELL)
            assert abs(p["h"] - want[row, col]) < 0.02, (
                f"window {p['h']:.4f} vs country {want[row, col]:.4f}")

    def test_zones_are_traced_against_the_window_they_belong_to(self, run):
        for name in ("west", "east", "back"):
            st = run["by_name"][name]
            x0, y0, _, _ = st["win"]
            ox, oy = st["zoneOrigin"][0]
            assert ox == pytest.approx(NOX + x0 * CELL)
            assert oy == pytest.approx(NOY - y0 * CELL)

    def test_a_second_species_lands_on_the_same_window(self, run):
        st = run["by_name"]["two"]
        assert len(st["zones"]) == 2 and all(st["zones"]), st["zones"]
        assert st["zoneOrigin"][0] == st["zoneOrigin"][1]


class TestZoomingOut:
    """Past one level's budget the map drops to a coarser one rather than
    stopping. Stopping is what a beginner hits first: zoom out to see where in
    the county is worth walking, and the zones vanish."""

    def test_the_close_views_are_at_the_finest_level(self, run):
        for name in ("west", "east", "empty", "back"):
            assert run["by_name"][name]["z"] == 0, name
            assert run["by_name"][name]["cell"] == pytest.approx(CELL)

    def test_zoomed_out_it_changes_level_rather_than_giving_up(self, run):
        st = run["by_name"]["far"]
        assert st["z"] > 0, "it stayed on the finest level"
        assert st["cell"] == pytest.approx(CELL * STEP ** st["z"])
        assert st["blank"] is False
        assert st["zones"][0] == 3, st["zones"]
        assert st["note"]["hidden"] is True

    def test_the_coarse_window_covers_much_more_ground(self, run):
        near = run["by_name"]["west"]
        far = run["by_name"]["far"]
        ground = lambda st: st["win"][2] * st["win"][3] * st["cell"] ** 2
        assert ground(far) > 20 * ground(near)

    def test_zooming_back_in_returns_to_the_finest(self, run):
        st = run["by_name"]["back"]
        assert st["z"] == 0 and st["blank"] is False
        assert st["zones"][0] == 3, st["zones"]
        assert st["note"]["hidden"] is True


class TestWhatItAsksFor:
    def test_it_never_asks_for_a_tile_that_was_never_written(self, run):
        """One 404 per empty tile per species is most of the sea, every pan."""
        asked = {u for u in run["asked"] if u.endswith(".png")}
        assert asked, "no tiles were requested at all"
        for key in KEYS:
            assert f"zones/{key}/0/0_0.png" not in asked

    def test_it_does_not_fetch_the_same_tile_twice(self, run):
        asked = [u for u in run["asked"] if u.endswith(".png")]
        assert len(asked) == len(set(asked)), "a tile was fetched twice"


class TestOneLevelOnly:
    """A payload with a single level, which is both the older export shape and
    the only way the "too much ground" path can still be reached."""

    @pytest.fixture(scope="class")
    def run(self, tmp_path_factory, grid):
        tmp = tmp_path_factory.mktemp("zoneflat")
        prepare(tmp, grid, n_levels=1, flat=True)
        return drive(tmp, {"steps": [
            {"name": "near", "view": WEST + [25], "probes": PROBES},
            {"name": "far", "view": world(512, 512) + [400]},
        ]})

    def test_a_flat_tile_list_still_works(self, run):
        st = run["by_name"]["near"]
        assert st["z"] == 0 and st["blank"] is False
        assert st["zones"][0] == 3, st["zones"]
        assert all(p is not None and p["byte"] for p in st["probes"])

    def test_with_nowhere_coarser_to_go_it_says_so(self, run):
        st = run["by_name"]["far"]
        assert st["blank"] is True
        assert all(z is None for z in st["zones"]), st["zones"]
        assert st["note"]["hidden"] is False
        assert "Zoom inn" in st["note"]["html"]


def filtered_png(arr: np.ndarray) -> bytes:
    """A structurally valid greyscale PNG using filter 1 (Sub) per scanline.

    Any browser would display it happily. Our decoder must not accept it: the
    encoder writes filter 0 precisely so the inflated payload *is* the values,
    and a decoder that guessed would hand the map a landscape of plausible,
    wrong numbers.
    """
    h, w = arr.shape
    rows = []
    for r in range(h):
        prev, line = 0, bytearray([1])
        for c in range(w):
            v = int(arr[r, c])
            line.append((v - prev) % 256)
            prev = v
        rows.append(bytes(line))
    return (b"\x89PNG\r\n\x1a\n"
            + ezt._chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + ezt._chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
            + ezt._chunk(b"IEND", b""))


class TestACorruptTile:
    """A tile the decoder cannot trust drops out; it does not leak values."""

    VICTIM = (1, 2)              # tile row, tile col: inside the window below

    @pytest.fixture(scope="class")
    def run_bad(self, tmp_path_factory, grid):
        tmp = tmp_path_factory.mktemp("zonebad")
        nat = prepare(tmp, grid)
        r, c = self.VICTIM
        victim = tmp / "zones" / KEYS[0] / "0" / f"{r}_{c}.png"
        assert victim.exists(), "the fixture stopped covering the corrupt tile"
        victim.write_bytes(filtered_png(
            nat[r * ezt.TILE:(r + 1) * ezt.TILE, c * ezt.TILE:(c + 1) * ezt.TILE]))
        plan = {"steps": [{"name": "mid", "view": world(500, 500) + [25],
                           "bytes": True}]}
        return drive(tmp, plan), nat

    def test_the_corrupt_tile_reads_as_nodata(self, run_bad):
        got, _ = run_bad
        st = got["steps"][0]
        x0, y0, w, h = st["win"]
        r, c = self.VICTIM
        rs = slice(max(0, r * ezt.TILE - y0), max(0, min(h, (r + 1) * ezt.TILE - y0)))
        cs = slice(max(0, c * ezt.TILE - x0), max(0, min(w, (c + 1) * ezt.TILE - x0)))
        block = win_bytes(st)[rs, cs]
        assert block.size, "the window does not reach the corrupt tile"
        assert not block.any(), "a filtered tile was decoded anyway"

    def test_its_neighbour_is_untouched(self, run_bad):
        """The rest of the window still has to be exactly right."""
        got, nat = run_bad
        st = got["steps"][0]
        x0, y0, w, h = st["win"]
        r, c = self.VICTIM
        rs = slice(max(0, r * ezt.TILE - y0), max(0, min(h, (r + 1) * ezt.TILE - y0)))
        cs = slice(max(0, (c - 1) * ezt.TILE - x0), max(0, min(w, c * ezt.TILE - x0)))
        block = win_bytes(st)[rs, cs]
        assert block.size, "the window does not reach the neighbouring tile"
        np.testing.assert_array_equal(
            block, nat[y0 + rs.start:y0 + rs.stop, x0 + cs.start:x0 + cs.stop])
        assert block.any(), "the neighbour is empty, so this checks nothing"


def conditions(pct) -> dict:
    """A conditions file over the whole country. `pct` fills the 1 km grid.

    The grid is seNorge's own kilometre, as the real file's is, and it is read
    in metres by the interface, so it does not have to line up with the zone
    grid in any way.
    """
    step = 1000.0
    w = int(NW * CELL / step) + 2
    h = int(NH * CELL / step) + 2
    vals = [pct(x, y) for y in range(h) for x in range(w)]
    return {"crs": "EPSG:25833",
            "grid": {"west": NOX - step, "north": NOY + step,
                     "step": step, "w": w, "h": h},
            "pct": vals,
            "rain21": [60.0] * len(vals), "rain7": [20.0] * len(vals),
            "tg7": [11.0] * len(vals),
            "clim_mean": [55.0] * len(vals), "clim_sd": [18.0] * len(vals),
            "daily_rain_mm": [2.0] * 21,
            "first_day": "2026-08-22", "last_day": "2026-09-11",
            "window_days": 21, "short_days": 7, "baseline": [1991, 2020],
            "lag_days": 1, "generated": "2026-09-14", "source": "test",
            "summary": {"pct_mean": 0.5, "rain7_mean": 20.0, "rain21_mean": 60.0,
                        "tg7_mean": 11.0, "clim_mean": 55.0, "ratio": 1.09}}


COND_PLAN = {"cond": True, "steps": [
    {"name": "west", "view": WEST + [25], "probes": PROBES},
    {"name": "east", "view": EAST + [25], "probes": PROBES},
]}


class TestConditionsRankNationallyToo:
    """The conditions term re-ranks the field, and that rank has to be national.

    Ranked against the loaded window it would put "the top 5 %" back inside the
    viewport - the exact bug the shipped habitat lookup exists to avoid, sneaking
    back in through the one layer that cannot be precomputed because the weather
    is not known at build time.
    """

    @pytest.fixture(scope="class")
    def flat(self, tmp_path_factory, grid):
        """Conditions identical everywhere: the ranking must come out unchanged."""
        tmp = tmp_path_factory.mktemp("zonecond_flat")
        prepare(tmp, grid)
        (tmp / "cond.json").write_text(json.dumps(conditions(lambda x, y: 0.5)),
                                       encoding="utf-8")
        return drive(tmp, COND_PLAN)

    @pytest.fixture(scope="class")
    def sloped(self, tmp_path_factory, grid):
        """A wet west and a dry east, which is the case worth worrying about."""
        tmp = tmp_path_factory.mktemp("zonecond_slope")
        prepare(tmp, grid)
        span = NW * CELL / 1000.0
        (tmp / "cond.json").write_text(
            json.dumps(conditions(lambda x, y: max(0.0, min(1.0, 1.0 - x / span)))),
            encoding="utf-8")
        return drive(tmp, COND_PLAN)

    def test_uniform_conditions_leave_the_ranking_alone(self, flat):
        """A constant multiplier cannot reorder anything, so the national CDF of
        H*m has to come back out as H itself. If this drifts, the conditions
        layer is quietly rescaling the map on a still day."""
        for st in flat["steps"]:
            for p in st["probes"]:
                assert p is not None
                assert abs(p["s"] - p["h"]) < 1e-5, f"{p['s']} vs {p['h']}"

    def test_sloped_conditions_actually_move_something(self, sloped):
        """Otherwise the test below passes against a layer that does nothing."""
        moved = max(abs(p["s"] - p["h"])
                    for st in sloped["steps"] for p in st["probes"])
        assert moved > 0.03, f"conditions changed nothing (max {moved:.4f})"

    def test_the_same_ground_still_ranks_the_same_from_both_windows(self, sloped):
        a = sloped["by_name"]["west"]["probes"]
        b = sloped["by_name"]["east"]["probes"]
        assert sloped["by_name"]["west"]["win"] != sloped["by_name"]["east"]["win"]
        for pa, pb in zip(a, b):
            assert abs(pa["s"] - pb["s"]) < 1e-6, f"{pa['s']} vs {pb['s']}"

    def test_the_result_is_still_a_rank(self, sloped):
        for st in sloped["steps"]:
            for p in st["probes"]:
                assert 0.0 <= p["s"] <= 1.0, p["s"]
