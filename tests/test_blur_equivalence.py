"""The Python blur and the interface's blur must agree cell for cell.

`export_zone_tiles.smoothed_rank_lut` precomputes, over the whole country, what
the interface's post-smoothing re-rank would produce. The app then smooths its
own visible window and looks the answer up. That only works if the two blurs
are the same blur: if Python smooths differently from JavaScript, every zone
boundary the app draws is looked up against a distribution that was never
computed from it, and the error is invisible - the map still renders, the
zones are still closed curves, they are simply in slightly the wrong place.

So this extracts `smoothPct` **from web/shell.html itself** and runs it under
node against the same input. Nothing is copied, so the test cannot drift from
the code it is checking; if someone edits the blur in the interface, this fails
until the exporter is updated to match.
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

SHELL = ROOT / "web" / "shell.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def extract(name: str) -> str:
    """The source of one top-level `function name(...)` from the shell."""
    text = SHELL.read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    depth, i = 0, text.index("{", start)
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1


def js_smooth(byts: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Run the interface's own smoothPct over `byts` under node."""
    h, w = byts.shape
    driver = f"""
const GW = {w}, GH = {h};
const LUT = Float32Array.from({json.dumps(lut.tolist())});
function pctLUT(sp) {{ return LUT; }}
{extract('smoothPct')}
const sp = {{ bytes: Uint8Array.from({json.dumps(byts.ravel().tolist())}) }};
process.stdout.write(JSON.stringify(Array.from(smoothPct(sp))));
"""
    out = subprocess.run([NODE, "-e", driver], capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    if out.returncode != 0:
        raise AssertionError(f"node failed: {out.stderr[:800]}")
    return np.array(json.loads(out.stdout), dtype="float64").reshape(h, w)


def py_smooth(byts: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """The same two passes the exporter applies, driven from bytes."""
    finite = byts != 0
    P = np.zeros(byts.shape, dtype="float32")
    P[finite] = lut[byts[finite]]
    V = finite.astype("float32")
    for _ in range(2):
        P, V = ezt._blur_pass(P, V, axis=1)
        P, V = ezt._blur_pass(P, V, axis=0)
    P[~finite] = 0.0
    return P.astype("float64")


def grid(seed: int, h: int, w: int, sea: float = 0.6) -> np.ndarray:
    """Bytes with a realistic amount of nodata, since that drives the weighting."""
    rng = np.random.default_rng(seed)
    b = rng.integers(1, 256, size=(h, w)).astype("uint8")
    b[rng.random((h, w)) < sea] = 0
    return b


@pytest.fixture(scope="module")
def lut() -> np.ndarray:
    """Byte -> percentile. The identity ramp the national encoding produces."""
    v = np.zeros(256, dtype="float32")
    v[1:] = (np.arange(1, 256) - 1) / 254.0
    return v


class TestTheTwoBlursAgree:
    @pytest.mark.parametrize("seed,h,w", [(1, 12, 15), (2, 31, 9), (3, 40, 40)])
    def test_same_field(self, seed, h, w, lut):
        b = grid(seed, h, w)
        np.testing.assert_allclose(js_smooth(b, lut), py_smooth(b, lut),
                                   rtol=0, atol=1e-6)

    def test_a_grid_with_no_sea(self, lut):
        b = grid(4, 20, 20, sea=0.0)
        np.testing.assert_allclose(js_smooth(b, lut), py_smooth(b, lut),
                                   rtol=0, atol=1e-6)

    def test_a_grid_that_is_almost_all_sea(self, lut):
        """The coastal weighting is the whole reason the blur is not a box blur."""
        b = grid(5, 24, 24, sea=0.95)
        got_js, got_py = js_smooth(b, lut), py_smooth(b, lut)
        assert (b != 0).sum() > 5, "fixture has too little land to mean anything"
        np.testing.assert_allclose(got_js, got_py, rtol=0, atol=1e-6)

    def test_unscored_cells_stay_zero_in_both(self, lut):
        b = grid(6, 18, 18)
        js, py = js_smooth(b, lut), py_smooth(b, lut)
        assert (js[b == 0] == 0).all() and (py[b == 0] == 0).all()

    def test_a_single_land_cell(self, lut):
        b = np.zeros((9, 9), dtype="uint8")
        b[4, 4] = 200
        np.testing.assert_allclose(js_smooth(b, lut), py_smooth(b, lut),
                                   rtol=0, atol=1e-6)

    def test_edges_and_corners(self, lut):
        """Both must count only the neighbours that exist."""
        b = np.full((5, 5), 128, dtype="uint8")
        js, py = js_smooth(b, lut), py_smooth(b, lut)
        np.testing.assert_allclose(js, py, rtol=0, atol=1e-6)
        # A uniform field must survive the blur unchanged, corners included.
        np.testing.assert_allclose(py, np.full((5, 5), lut[128]), atol=1e-6)


class TestTheExtractionItself:
    def test_it_found_the_real_function(self):
        src = extract("smoothPct")
        assert src.startswith("function smoothPct(sp){")
        assert src.count("{") == src.count("}")
        assert "pctLUT" in src and "GW" in src

    def test_a_missing_function_fails_loudly(self):
        with pytest.raises(ValueError):
            extract("thisFunctionDoesNotExist")
