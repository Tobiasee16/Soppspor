"""Loose contour segments have to come back as the right closed rings.

`buildZones` walks marching squares and emits segments in no particular order;
`pathFromSegments` is what turns that heap into rings the map can fill. It was
rewritten for speed - vertex identity moved from a string key to a packed
number, which took a county-wide window commit from 605 ms to 291 ms - and the
rewrite was checked against the old one vertex for vertex over the real
national tiles at four zooms. This file is what keeps it honest from here.

What can go wrong is specific. Two vertices that should join, left apart,
leave a gap in a filled zone. Two that should *not* join, merged by a key
collision, staple two unrelated woods into one ring. Neither throws, and both
draw something a reader would accept.
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


def stitch(segs, gh: int = 64):
    """Run the interface's own stitcher over `segs`, returning its path ops."""
    driver = f"""
const GH = {gh};
class Path2D {{
  constructor(){{ this.ops = []; }}
  moveTo(x,y){{ this.ops.push(["M",x,y]); }}
  lineTo(x,y){{ this.ops.push(["L",x,y]); }}
  closePath(){{ this.ops.push(["Z"]); }}
}}
{extract('simplifyRing')}
{extract('pathFromSegments')}
const p = pathFromSegments({json.dumps(segs)});
process.stdout.write(JSON.stringify(p ? p.ops : null));
"""
    out = subprocess.run([NODE, "-e", driver], capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    if out.returncode != 0:
        raise AssertionError(f"node failed: {out.stderr[-900:]}")
    return json.loads(out.stdout)


def rings(ops):
    """Split path ops into rings of vertices."""
    out, cur = [], None
    for op in ops or []:
        if op[0] == "M":
            cur = [(op[1], op[2])]
        elif op[0] == "L":
            cur.append((op[1], op[2]))
        else:
            out.append(cur)
            cur = None
    return out


def square(x, y, s=1.0, shuffle=True):
    """The four sides of a square, as separate segments in scrambled order."""
    c = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    segs = [[list(c[i]), list(c[(i + 1) % 4])] for i in range(4)]
    if shuffle:
        segs = [segs[2], segs[0], segs[3], segs[1]]
    return segs


class TestItClosesWhatShouldClose:
    def test_a_scrambled_square_comes_back_as_one_ring(self):
        got = rings(stitch(square(3, 5)))
        assert len(got) == 1, got
        assert len(got[0]) >= 4
        assert set(got[0]) == {(3, 5), (4, 5), (4, 6), (3, 6)}

    def test_two_separate_squares_stay_separate(self):
        """Merged, they would fill the ground between them."""
        got = rings(stitch(square(2, 2) + square(20, 30)))
        assert len(got) == 2, f"{len(got)} rings, expected 2"
        xs = [min(p[0] for p in r) for r in got]
        assert sorted(xs) == [2, 20]

    def test_segments_meeting_at_a_shared_corner_still_close(self):
        """Marching squares emits a saddle as two segments through one point."""
        got = rings(stitch(square(0, 0) + square(1, 1)))
        assert len(got) == 2, f"{len(got)} rings"
        assert all(len(r) >= 4 for r in got)

    def test_a_ring_of_fewer_than_four_points_is_dropped(self):
        assert stitch([[[0, 0], [1, 0]], [[1, 0], [0, 0]]]) is None

    def test_no_segments_at_all(self):
        assert stitch([]) is None


class TestItDoesNotJoinWhatIsApart:
    def test_far_apart_vertices_are_not_confused(self):
        """The key packs two coordinates into one number. A packing that wrapped
        would staple unrelated ground together, and the map would look fine."""
        got = rings(stitch(square(0, 0) + square(0, 63) + square(63, 0), gh=64))
        assert len(got) == 3, f"{len(got)} rings, expected 3"
        corners = sorted((min(p[0] for p in r), min(p[1] for p in r)) for r in got)
        assert corners == [(0, 0), (0, 63), (63, 0)]

    def test_a_tall_narrow_window_packs_without_collision(self):
        """Window shape drives the packing multiplier, so a tall one is its own
        case: the national grid is 12,005 cells tall at the finest level."""
        segs = square(0, 0) + square(2, 4000) + square(4, 11000)
        got = rings(stitch(segs, gh=12005))
        assert len(got) == 3, f"{len(got)} rings, expected 3"

    def test_half_cell_vertices_on_one_edge_are_one_point(self):
        """Contour ends land on cell edges and are rounded to 1/512 of a cell
        so that the two sides of a shared edge agree. Neighbours half a cell
        apart must still be distinct."""
        a = [[[0.0, 0.5], [1.0, 0.5]]]
        b = [[[1.0, 0.5], [1.0, 1.5]]]
        c = [[[1.0, 1.5], [0.0, 1.5]]]
        d = [[[0.0, 1.5], [0.0, 0.5]]]
        got = rings(stitch(a + b + c + d))
        assert len(got) == 1 and len(got[0]) >= 4


class TestSimplification:
    def test_a_straight_run_keeps_only_its_ends(self):
        """Contour vertices sit on cell edges, so a straight coastline carries
        one per cell; dropping the collinear ones is most of the path length."""
        segs = [[[float(i), 0.0], [float(i + 1), 0.0]] for i in range(10)]
        segs += [[[10.0, 0.0], [10.0, 3.0]], [[10.0, 3.0], [0.0, 3.0]],
                 [[0.0, 3.0], [0.0, 0.0]]]
        got = rings(stitch(segs))
        assert len(got) == 1
        # Four corners, give or take the duplicated start point.
        assert len(got[0]) <= 6, got[0]
        assert (0.0, 0.0) in got[0] and (10.0, 3.0) in got[0]

    def test_it_does_not_flatten_a_real_corner(self):
        got = rings(stitch(square(0, 0, s=4)))
        assert set(got[0]) == {(0, 0), (4, 0), (4, 4), (0, 4)}
