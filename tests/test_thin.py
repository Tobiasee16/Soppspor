"""Validation before the thing it checks (CLAUDE.md working agreement)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.features.thin import thin
from src.geometry import Boundary


def _p(e, n, prec=10.0, pid="x"):
    return {"east": e, "north": n, "precision_m": prec, "id": pid}


def test_same_cell_collapses_to_one():
    pts = [_p(100, 100, 10, "a"), _p(900, 900, 10, "b")]
    assert len(thin(pts, 1000)) == 1


def test_adjacent_cells_both_kept():
    pts = [_p(100, 100, 10, "a"), _p(1100, 100, 10, "b")]
    assert len(thin(pts, 1000)) == 2


def test_zero_cell_is_passthrough():
    pts = [_p(1, 1), _p(2, 2), _p(3, 3)]
    assert len(thin(pts, 0)) == 3


def test_keeps_most_precise_record_in_cell():
    pts = [_p(100, 100, 707, "coarse"), _p(200, 200, 5, "fine")]
    assert thin(pts, 1000)[0]["id"] == "fine"


def test_thinning_is_deterministic():
    pts = [_p(100, 100, 10, "b"), _p(200, 200, 10, "a"), _p(300, 300, 10, "c")]
    forward = sorted(r["id"] for r in thin(pts, 1000))
    backward = sorted(r["id"] for r in thin(list(reversed(pts)), 1000))
    assert forward == backward


def test_negative_coordinates_do_not_alias():
    # floor division, not truncation: -100 and +100 must land in different cells
    pts = [_p(-100, 100, 10, "neg"), _p(100, 100, 10, "pos")]
    assert len(thin(pts, 1000)) == 2


SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [0, 100], [100, 100], [100, 0], [0, 0]]]}
WITH_HOLE = {
    "type": "Polygon",
    "coordinates": [
        [[0, 0], [0, 100], [100, 100], [100, 0], [0, 0]],
        [[40, 40], [40, 60], [60, 60], [60, 40], [40, 40]],
    ],
}


def test_point_inside_and_outside():
    b = Boundary(SQUARE)
    assert (50, 50) in b
    assert (150, 50) not in b


def test_hole_is_outside():
    b = Boundary(WITH_HOLE)
    assert (50, 50) not in b
    assert (10, 10) in b


def test_multipolygon():
    mp = {
        "type": "MultiPolygon",
        "coordinates": [
            SQUARE["coordinates"],
            [[[200, 0], [200, 100], [300, 100], [300, 0], [200, 0]]],
        ],
    }
    b = Boundary(mp)
    assert (50, 50) in b
    assert (250, 50) in b
    assert (150, 50) not in b


# --- Artskart record handling -------------------------------------------------

from src.ingest.artskart import usable_points


def _obs(oid, east=1000.0, north=2000.0, prec="10", muni="Kristiansand"):
    return {"Id": oid, "East": east, "North": north, "Precision": prec, "Municipality": muni}


def test_admin_boundary_duplicates_collapse():
    # One observation straddling a municipality line comes back once per unit.
    obs = [_obs("urn:a", muni="Oslo"), _obs("urn:a", muni="Nordre Follo"), _obs("urn:b")]
    assert len(usable_points(obs)) == 2


def test_records_without_coordinates_are_dropped():
    obs = [_obs("urn:a"), {"Id": "urn:b", "East": None, "North": None}, {"Id": "urn:c", "East": 0, "North": 0}]
    assert len(usable_points(obs)) == 1


def test_unparseable_precision_becomes_none_not_zero():
    pts = usable_points([_obs("urn:a", prec="")])
    assert pts[0]["precision_m"] is None
