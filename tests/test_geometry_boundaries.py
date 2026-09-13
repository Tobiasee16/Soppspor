"""A region made of several polygons - the national training extent.

Norway has no single national outline in Kartverket's kommuneinfo API, so the
training extent is fourteen fylker unioned, with the held-out fifteenth left
out. `Boundaries` is that union, and the thing worth testing is that it behaves
like one region: inside any part is inside, a hole in a part is still outside,
and the held-out part being absent actually excludes its ground.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.geometry import Boundaries, Boundary


def square(x0, y0, side, name=""):
    ring = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side), (x0, y0)]
    return Boundary({"type": "Polygon", "coordinates": [ring]}, name=name)


def square_with_hole(x0, y0, side, name=""):
    outer = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side), (x0, y0)]
    q = side / 4
    inner = [(x0 + q, y0 + q), (x0 + q, y0 + 3 * q), (x0 + 3 * q, y0 + 3 * q),
             (x0 + 3 * q, y0 + q), (x0 + q, y0 + q)]
    return Boundary({"type": "Polygon", "coordinates": [outer, inner]}, name=name)


class TestMembership:
    def test_inside_any_part_is_inside(self):
        b = Boundaries([square(0, 0, 10, "a"), square(100, 100, 10, "b")])
        assert (5, 5) in b
        assert (105, 105) in b

    def test_the_gap_between_parts_is_outside(self):
        b = Boundaries([square(0, 0, 10, "a"), square(100, 100, 10, "b")])
        assert (50, 50) not in b

    def test_outside_the_union_bbox_is_outside(self):
        b = Boundaries([square(0, 0, 10), square(100, 100, 10)])
        assert (-1, -1) not in b
        assert (1000, 1000) not in b

    def test_a_hole_in_a_part_stays_outside(self):
        b = Boundaries([square_with_hole(0, 0, 40)])
        assert (5, 5) in b          # between outer ring and hole
        assert (20, 20) not in b    # in the hole

    def test_an_excluded_part_excludes_its_ground(self):
        """The whole premise of the national design: Nordland is not in there."""
        parts = [square(0, 0, 10, "kept"), square(100, 100, 10, "heldout")]
        train = Boundaries([p for p in parts if p.name != "heldout"])
        assert (5, 5) in train
        assert (105, 105) not in train


class TestUnionBox:
    def test_bbox_covers_every_part(self):
        b = Boundaries([square(0, 0, 10), square(100, 100, 10)])
        assert b.bbox == (0.0, 0.0, 110.0, 110.0)

    def test_len_is_the_part_count(self):
        assert len(Boundaries([square(0, 0, 10), square(100, 100, 10)])) == 2

    def test_empty_is_refused(self):
        # An empty region silently contains nothing, which would drop every
        # presence and report a model fitted on no data as a model fitted on
        # no data only at the very end, if at all.
        with pytest.raises(ValueError):
            Boundaries([])


class TestPartLookup:
    def test_part_containing_names_the_fylke(self):
        b = Boundaries([square(0, 0, 10, "agder"), square(100, 100, 10, "nordland")])
        assert b.part_containing((5, 5)).name == "agder"
        assert b.part_containing((105, 105)).name == "nordland"

    def test_part_containing_is_none_outside(self):
        b = Boundaries([square(0, 0, 10, "agder")])
        assert b.part_containing((50, 50)) is None


class TestAgainstTheRealFylker:
    """If the fetched boundaries are on disk, use them. Skip if not."""

    @pytest.fixture
    def fylker(self):
        import json
        d = ROOT / "data" / "interim" / "fylker"
        files = sorted(d.glob("boundary_*_25833.geojson")) if d.exists() else []
        if len(files) < 15:
            pytest.skip("national fylke boundaries not fetched")
        out = {}
        for f in files:
            fc = json.loads(f.read_text(encoding="utf-8"))
            name = f.stem.replace("boundary_", "").replace("_25833", "")
            out[name] = Boundary(fc["features"][0]["geometry"], name=name)
        return out

    def test_nordland_points_are_outside_the_training_union(self, fylker):
        train = Boundaries([b for n, b in fylker.items() if n != "nordland"],
                           name="training")
        nordland = fylker["nordland"]
        # Bodo, Narvik and Mo i Rana, projected from their WGS84 coordinates
        # rather than eyeballed. An earlier version of this file guessed an
        # easting for Kristiansand and was out by 350 km - the guess happened
        # to land inside a different fylke, which is exactly the sort of test
        # that passes while checking nothing.
        for pt in [(473929.3, 7462765.8), (599523.6, 7593715.4), (461572.7, 7355039.6)]:
            assert pt in nordland, f"{pt} should be in Nordland"
            assert pt not in train, f"{pt} leaked into the training extent"

    def test_kristiansand_is_in_the_training_union(self, fylker):
        train = Boundaries([b for n, b in fylker.items() if n != "nordland"])
        assert (88126.6, 6466468.9) in train   # Kristiansand city centre
