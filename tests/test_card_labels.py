"""The card must not state a fact about forest over a lake.

SR16 maps productive forest and nothing else, so an absent tree species means
two different things depending on what the ground is. On forest it is a
finding - the stand is there and is not productive forest, which is worth
knowing before walking in. On water, bog, farmland or a street it is simply not
a question SR16 was asked.

The card used to answer it anyway, printing "ikke produktiv skog" under
"Ferskvann" on the same card, while the summary line one paragraph above
silently omitted the species. Two halves of one card disagreeing about whether
there was anything to say. Bogs and water are ordinary taps in Norwegian
terrain, so this was not an edge case that only showed up in the middle of Oslo.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_blur_equivalence import extract

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# As they arrive from the payload: [Norwegian, English].
FOREST = ["Skog", "forest"]
WATER = ["Ferskvann", "freshwater"]
BUILT = ["Bebygd", "built"]
MIRE = ["Myr", "mire"]
SPRUCE = ["Gran", "Spruce"]


def label(ar, tre):
    """Run the interface's own labeller."""
    driver = f"""
{extract('treslagLabel')}
process.stdout.write(JSON.stringify(
  treslagLabel({json.dumps(ar)}, {json.dumps(tre)})));
"""
    out = subprocess.run([NODE, "-e", driver], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise AssertionError(f"node failed: {out.stderr[-800:]}")
    return json.loads(out.stdout)


class TestWhereSR16HasSomethingToSay:
    def test_a_species_is_named(self):
        assert label(FOREST, SPRUCE) == "Gran"

    def test_a_species_is_named_even_off_forest(self):
        """If SR16 has a species, it is a species, whatever AR50 thinks."""
        assert label(BUILT, SPRUCE) == "Gran"

    def test_forest_without_a_species_is_a_finding(self):
        assert label(FOREST, None) == "ikke produktiv skog"


class TestWhereItWasNeverAsked:
    @pytest.mark.parametrize("ground,name", [
        (WATER, "water"), (BUILT, "a street"), (MIRE, "a bog"), (None, "no data"),
    ])
    def test_it_says_nothing_rather_than_something_wrong(self, ground, name):
        got = label(ground, None)
        assert got == "—", f"over {name} the card said {got!r}"
