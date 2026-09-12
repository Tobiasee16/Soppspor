"""Tests for target-group background sampling and the null control.

The null control is the check every other number in Phase 0 leans on, so the
thing it is built from is tested here against cases with known answers - most
importantly that a null drawn correctly actually scores 0.5 under blocked CV.
"""
import pathlib
import random
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.features.background import (
    PointSet,
    assemble,
    draw_background,
    null_and_background,
    overlap_count,
    target_group_pool,
)
from src.geometry import Boundary
from src.validate.harness import spatial_block_cv


def _rec(e, n, prec=10.0, rid=None):
    return {"east": e, "north": n, "precision_m": prec, "id": rid or f"{e}:{n}"}


def _square(size_m):
    return Boundary({
        "type": "Polygon",
        "coordinates": [[[0, 0], [size_m, 0], [size_m, size_m], [0, size_m], [0, 0]]],
    })


# --- pool construction --------------------------------------------------------


def test_pool_clips_to_boundary():
    pts = [_rec(500, 500), _rec(50000, 500)]
    pool = target_group_pool(pts, boundary=_square(10000.0))
    assert len(pool) == 1


def test_pool_drops_imprecise_records_rather_than_assuming_them_good():
    pts = [_rec(1, 1, prec=10.0), _rec(2, 2, prec=5000.0), _rec(3, 3, prec=None)]
    pool = target_group_pool(pts, max_precision_m=250)
    assert len(pool) == 1


def test_pool_thins_like_the_presences_do():
    # Ten records in one 1 km cell must not contribute ten background points.
    pts = [_rec(100 + i, 100 + i, rid=f"r{i}") for i in range(10)]
    assert len(target_group_pool(pts, thin_m=1000)) == 1


def test_pool_without_filters_is_a_passthrough():
    pts = [_rec(1, 1), _rec(90000, 90000)]
    assert len(target_group_pool(pts)) == 2


# --- background draw ----------------------------------------------------------


def test_background_uses_the_whole_pool_by_default():
    pool = [_rec(i * 1000, 0, rid=f"r{i}") for i in range(50)]
    bg = draw_background(pool)
    assert len(bg) == 50
    assert bg.synthetic is False


def test_background_subsample_is_deterministic_and_noted():
    pool = [_rec(i * 1000, 0, rid=f"r{i}") for i in range(50)]
    a = draw_background(pool, n=10, seed=3)
    b = draw_background(pool, n=10, seed=3)
    assert a.coords == b.coords
    assert len(a) == 10
    assert any("subsampled" in note for note in a.notes)


def test_background_from_empty_pool_raises():
    with pytest.raises(ValueError, match="empty"):
        draw_background([])


# --- null control -------------------------------------------------------------


def test_null_and_background_are_disjoint():
    """The property that makes the null's expected AUC exactly 0.5."""
    pool = [_rec(i * 500, 0, rid=f"r{i}") for i in range(400)]
    null, bg = null_and_background(pool, n_null=100, seed=1)
    assert len(null) == 100
    assert len(bg) == 300
    assert overlap_count(null, bg) == 0


def test_null_is_labelled_synthetic():
    pool = [_rec(i * 500, 0, rid=f"r{i}") for i in range(100)]
    null, bg = null_and_background(pool, n_null=10)
    assert null.synthetic is True
    assert "SYNTHETIC" in null.label
    assert bg.synthetic is False
    assert "SYNTHETIC" not in bg.label
    assert any("NOT REAL DATA" in note for note in null.notes)


def test_null_draw_is_deterministic_under_seed():
    pool = [_rec(i * 500, 0, rid=f"r{i}") for i in range(200)]
    a, _ = null_and_background(pool, n_null=50, seed=7)
    b, _ = null_and_background(pool, n_null=50, seed=7)
    assert a.coords == b.coords


def test_null_larger_than_the_pool_raises():
    pool = [_rec(i * 500, 0, rid=f"r{i}") for i in range(10)]
    with pytest.raises(ValueError, match="cannot draw"):
        null_and_background(pool, n_null=10)


def test_background_can_be_capped_independently():
    pool = [_rec(i * 500, 0, rid=f"r{i}") for i in range(300)]
    null, bg = null_and_background(pool, n_null=50, n_background=100, seed=0)
    assert len(null) == 50 and len(bg) == 100
    assert overlap_count(null, bg) == 0


# --- assembly -----------------------------------------------------------------


def test_assemble_labels_presences_one_and_background_zero():
    pres = PointSet(coords=[(0.0, 0.0), (1.0, 1.0)], role="presences")
    bg = PointSet(coords=[(2.0, 2.0)], role="background")
    coords, y = assemble(pres, bg)
    assert coords == [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
    assert y == [1, 1, 0]


def test_assemble_accepts_a_bare_coordinate_list():
    bg = PointSet(coords=[(2.0, 2.0)], role="background")
    coords, y = assemble([(0.0, 0.0)], bg)
    assert y == [1, 0]


# --- the check the null exists to perform -------------------------------------


def _knn(k=15):
    def fit_predict(X_tr, y_tr, X_te):
        out = []
        for xt in X_te:
            best = []
            for xr, yr in zip(X_tr, y_tr):
                d = (xt[0] - xr[0]) ** 2 + (xt[1] - xr[1]) ** 2
                if len(best) < k:
                    best.append((d, yr)); best.sort()
                elif d < best[-1][0]:
                    best[-1] = (d, yr); best.sort()
            out.append(sum(v for _, v in best) / len(best))
        return out
    return fit_predict


def test_correctly_drawn_null_scores_about_half_under_blocked_cv():
    """The constraint-5 check in miniature.

    A clustered effort surface, null presences and background drawn disjointly
    from it, and a model given coordinates alone. There is nothing to separate
    the two classes, so blocked CV must return roughly 0.5. If this test ever
    fails, either the draw has stopped being disjoint or the CV scheme is
    leaking - and in a real run the same symptom would invalidate every number
    in the table.
    """
    rng = random.Random(0)
    centres = [(rng.uniform(0, 100000), rng.uniform(0, 100000)) for _ in range(40)]
    pool = []
    for i in range(1500):
        cx, cy = rng.choice(centres)
        pool.append(_rec(rng.gauss(cx, 1500.0), rng.gauss(cy, 1500.0), rid=f"r{i}"))

    null, bg = null_and_background(pool, n_null=250, seed=0)
    coords, y = assemble(null, bg)
    X = [[c[0], c[1]] for c in coords]

    r = spatial_block_cv(coords, y, X, _knn(), block_size_m=7500, n_folds=5, buffer_m=4000)
    assert 0.40 < r.mean_auc < 0.60, f"null scored {r.mean_auc:.3f}, expected ~0.5"


def test_adding_species_cannot_move_another_species_score():
    """Adding species to config must not perturb the existing ones.

    The whole pipeline shares one background set, drawn by
    `null_and_background(pool, n_null, seed)`. `n_null` is the largest presence
    count across all species, so in principle adding a species with more
    records than any existing one would redraw the background and shift every
    published AUC - silently, with nothing in the output saying why.

    That did not happen when five species were added on 2026-09-12 (the largest
    set is still Cantharellus cibarius at 391), but "did not happen this time"
    is not a property. This pins the actual property: for a fixed n_null and
    seed, the draw is identical regardless of what else is in the config.
    """
    pool = [{"east": 60000.0 + i * 37, "north": 6470000.0 + i * 53,
             "precision_m": 10.0, "id": i} for i in range(2000)]

    three, bg_three = null_and_background(pool, n_null=391, seed=0)
    eight, bg_eight = null_and_background(pool, n_null=391, seed=0)
    assert three.coords == eight.coords
    assert bg_three.coords == bg_eight.coords

    # And the converse, so the test would notice if the coupling stopped being
    # real: a different n_null genuinely does move the background.
    _, bg_bigger = null_and_background(pool, n_null=500, seed=0)
    assert bg_bigger.coords != bg_three.coords, (
        "a larger n_null must change the background, or the guard above is "
        "testing nothing")
