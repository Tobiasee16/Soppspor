"""A window of tiles must rank the same as the whole country would.

This is the property the national zone pipeline exists to guarantee, and it is
the one that fails silently. The interface ranks its smoothed habitat field so
that a zone means "the top 5 %". With the whole grid loaded that is the top 5 %
of Norway. With only the tiles near the viewport loaded it would be the top 5 %
of whatever is on screen - so the same wood would move in and out of a zone
depending on how far the user had panned, and nothing would look wrong.

The fix is a 256-entry CDF of the nationally smoothed surface, shipped with the
tiles. These tests build a synthetic country, cut a window out of it, run the
interface's own `habitatField` over the window under node, and require the
answer to match the national one.

The counter-test matters as much: the same window through the *old* local-sort
path must visibly disagree. Without it this file could pass against a lookup
that did nothing.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_zone_tiles as ezt
from tests.test_blur_equivalence import extract, py_smooth

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

HALO = 2          # the blur is two separable 3-tap passes: support is 5x5


def js_habitat(byts: np.ndarray, lut: list[float] | None) -> np.ndarray:
    """Run the interface's habitatField over a window, under node."""
    h, w = byts.shape
    sp_lut = "null" if lut is None else json.dumps(lut)
    driver = f"""
const GW = {w}, GH = {h};
const D = {{ meta: {{ percentile_bytes: true }} }};
function ranks(sp){{var v=[];for(var i=0;i<sp.bytes.length;i++)
  if(sp.bytes[i]!==0)v.push(sp.bytes[i]);v.sort(function(a,b){{return a-b;}});return v;}}
function lowerBound(v,b){{var lo=0,hi=v.length;
  while(lo<hi){{var m=(lo+hi)>>1;if(v[m]<b)lo=m+1;else hi=m;}}return lo;}}
{extract('pctLUT')}
{extract('natRank')}
{extract('smoothPct')}
{extract('habitatField')}
const sp = {{ bytes: Uint8Array.from({json.dumps(byts.ravel().tolist())}),
             smoothed_rank_lut: {sp_lut} }};
process.stdout.write(JSON.stringify(Array.from(habitatField(sp))));
"""
    out = subprocess.run([NODE, "-e", driver], capture_output=True, text=True,
                         encoding="utf-8", timeout=180)
    if out.returncode != 0:
        raise AssertionError(f"node failed: {out.stderr[:900]}")
    return np.array(json.loads(out.stdout), dtype="float64").reshape(h, w)


def national_rank(grid: np.ndarray) -> np.ndarray:
    """What the app should show everywhere: rank of the smoothed field, nationally."""
    byts, _ = ezt.to_percentile_bytes(grid)
    ramp = np.zeros(256, dtype="float32")
    ramp[1:] = (np.arange(1, 256) - 1) / 254.0
    S = py_smooth(byts, ramp)
    scored = byts != 0
    vals = np.sort(S[scored])
    out = np.zeros(grid.shape, dtype="float64")
    out[scored] = np.searchsorted(vals, S[scored], side="left") / vals.size
    return out


@pytest.fixture(scope="module")
def country():
    """A skewed surface with a sea margin and a strong north-south gradient.

    The gradient is the point, not decoration. An earlier version of this
    fixture was uniform random over the whole rectangle, which made every
    window a representative sample of the country - so ranking locally gave
    almost the national answer and the counter-test below could not tell the
    lookup from a no-op. Norway is not uniform: a window over Finnmark holds a
    completely different distribution of habitat from one over Ostlandet, and
    that is exactly why a per-view rank would mislead.
    """
    rng = np.random.default_rng(7)
    g = np.full((150, 130), np.nan, dtype="float32")
    land = rng.pareto(0.4, size=(110, 90)).astype("float32")
    # Suppress the north (low row index) hard, so the two ends of the country
    # are genuinely different places.
    gradient = np.linspace(0.05, 1.0, 110)[:, None].astype("float32")
    g[20:130, 20:110] = land * gradient
    return g


class TestAWindowMatchesTheCountry:
    @pytest.mark.parametrize("r0,c0,n", [(30, 30, 48), (60, 40, 40), (90, 60, 36)])
    def test_window_ranks_match_national(self, country, r0, c0, n):
        byts, _ = ezt.to_percentile_bytes(country)
        lut = ezt.smoothed_rank_lut(country)
        got = js_habitat(byts[r0:r0 + n, c0:c0 + n], lut)
        want = national_rank(country)[r0:r0 + n, c0:c0 + n]
        # Compare the interior: the window's outermost cells are smoothed
        # without their true neighbours, which is what the halo is for.
        g = got[HALO:-HALO, HALO:-HALO]
        w = want[HALO:-HALO, HALO:-HALO]
        assert np.abs(g - w).max() < 0.02, f"max drift {np.abs(g - w).max():.4f}"

    def test_the_same_ground_ranks_the_same_from_two_windows(self, country):
        """Panning must not move the zones. This is the user-visible property."""
        byts, _ = ezt.to_percentile_bytes(country)
        lut = ezt.smoothed_rank_lut(country)
        a = js_habitat(byts[40:100, 40:100], lut)[20:40, 20:40]
        b = js_habitat(byts[50:110, 30:90], lut)[10:30, 30:50]
        np.testing.assert_allclose(a, b, atol=1e-6)

    def test_unscored_cells_stay_zero(self, country):
        byts, _ = ezt.to_percentile_bytes(country)
        lut = ezt.smoothed_rank_lut(country)
        win = byts[0:40, 0:40]                      # mostly sea
        got = js_habitat(win, lut)
        assert (got[win == 0] == 0).all()


class TestTheLookupIsActuallyDoingSomething:
    def test_local_ranking_disagrees_with_national(self, country):
        """Without the lookup the same window gives a different answer.

        If this ever passes, the lookup is a no-op and the test above is
        checking nothing.
        """
        byts, _ = ezt.to_percentile_bytes(country)
        r0, c0, n = 60, 40, 40
        local = js_habitat(byts[r0:r0 + n, c0:c0 + n], None)
        want = national_rank(country)[r0:r0 + n, c0:c0 + n]
        drift = np.abs(local[HALO:-HALO, HALO:-HALO] - want[HALO:-HALO, HALO:-HALO])
        assert drift.max() > 0.05, "local and national ranking agree; lookup is moot"

    def test_two_windows_disagree_with_each_other_without_the_lookup(self, country):
        byts, _ = ezt.to_percentile_bytes(country)
        a = js_habitat(byts[40:100, 40:100], None)[20:40, 20:40]
        b = js_habitat(byts[50:110, 30:90], None)[10:30, 30:50]
        assert np.abs(a - b).max() > 0.01, "panning already stable; nothing to fix"


class TestNatRank:
    def test_it_is_monotone_and_bounded(self, country):
        lut = ezt.smoothed_rank_lut(country)
        xs = np.linspace(0.0, 1.0, 400)
        driver_vals = [
            float(np.interp(x, np.arange(256) / 255.0, lut)) for x in xs]
        assert all(b >= a - 1e-9 for a, b in zip(driver_vals, driver_vals[1:]))
        assert 0.0 <= min(driver_vals) and max(driver_vals) <= 1.0
