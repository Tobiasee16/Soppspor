"""Tests for the validation harness.

The harness is what every Phase 0 conclusion will rest on, so it gets checked
against cases where the right answer is known by construction, not by running a
model and eyeballing the output.
"""
import math
import pathlib
import random
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.validate.blocks import assign_blocks, fold_adequacy_warnings, make_folds
from src.validate.harness import (
    compare_schemes,
    random_cv_untrustworthy,
    spatial_block_cv,
)
from src.validate.metrics import continuous_boyce, roc_auc, spearman

# --- AUC ----------------------------------------------------------------------


def test_auc_perfect_separation():
    assert roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0


def test_auc_perfectly_inverted():
    assert roc_auc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]) == 0.0


def test_auc_all_ties_is_half():
    assert roc_auc([0, 1, 0, 1], [0.5] * 4) == 0.5


def test_auc_single_class_is_nan_not_half():
    # A broken fold must not masquerade as a null result.
    assert math.isnan(roc_auc([1, 1, 1], [0.1, 0.2, 0.3]))


def test_auc_known_value_with_one_swap():
    # 2 positives, 2 negatives, exactly one discordant pair out of four.
    assert roc_auc([0, 1, 0, 1], [0.1, 0.2, 0.3, 0.4]) == pytest.approx(0.75)


def test_auc_rejects_length_mismatch():
    with pytest.raises(ValueError):
        roc_auc([0, 1], [0.5])


# --- Spearman -----------------------------------------------------------------


def test_spearman_monotonic():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_reversed():
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_nonlinear_but_monotonic_is_still_one():
    assert spearman([1, 2, 3, 4], [1, 4, 9, 16]) == pytest.approx(1.0)


def test_spearman_constant_input_is_nan():
    assert math.isnan(spearman([1, 1, 1, 1], [1, 2, 3, 4]))


# --- Continuous Boyce ---------------------------------------------------------


def test_boyce_rewards_a_good_model():
    rng = random.Random(0)
    presences = [rng.uniform(0.6, 1.0) for _ in range(300)]
    background = [rng.uniform(0.0, 1.0) for _ in range(3000)]
    assert continuous_boyce(presences, background) > 0.7


def test_boyce_near_zero_for_a_useless_model():
    rng = random.Random(1)
    presences = [rng.uniform(0.0, 1.0) for _ in range(500)]
    background = [rng.uniform(0.0, 1.0) for _ in range(5000)]
    assert abs(continuous_boyce(presences, background)) < 0.4


def test_boyce_goes_negative_for_an_inverted_model():
    rng = random.Random(2)
    presences = [rng.uniform(0.0, 0.4) for _ in range(300)]
    background = [rng.uniform(0.0, 1.0) for _ in range(3000)]
    assert continuous_boyce(presences, background) < -0.7


def test_boyce_empty_input_is_nan():
    assert math.isnan(continuous_boyce([], [0.1, 0.2]))
    assert math.isnan(continuous_boyce([0.1], []))


# --- Blocking -----------------------------------------------------------------


def test_blocks_align_to_crs_origin_not_data_extent():
    assert assign_blocks([(0.0, 0.0), (4999.0, 4999.0)], 5000) == [(0, 0), (0, 0)]
    assert assign_blocks([(5000.0, 0.0)], 5000) == [(1, 0)]


def test_blocks_handle_negative_easting():
    # Western Agder falls west of the UTM33 central meridian.
    assert assign_blocks([(-1.0, 100.0)], 5000) == [(-1, 0)]


def test_blocks_reject_nonpositive_size():
    with pytest.raises(ValueError):
        assign_blocks([(0.0, 0.0)], 0)


def _grid_points(n_side=10, spacing=6000.0, per_cell=4):
    coords, y = [], []
    for i in range(n_side):
        for j in range(n_side):
            for k in range(per_cell):
                coords.append((i * spacing + k, j * spacing + k))
                y.append(1 if k == 0 else 0)
    return coords, y


def test_no_block_is_split_across_folds():
    """The property the whole scheme depends on."""
    coords, y = _grid_points()
    blocks = assign_blocks(coords, 5000)
    fa = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=7)
    seen: dict = {}
    for b, f in zip(blocks, fa.fold_of_point):
        if b in seen:
            assert seen[b] == f, f"block {b} appears in folds {seen[b]} and {f}"
        seen[b] = f


def test_fold_assignment_is_deterministic_under_seed():
    coords, y = _grid_points()
    blocks = assign_blocks(coords, 5000)
    a = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=3)
    b = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=3)
    assert a.fold_of_point == b.fold_of_point


def test_different_seeds_can_differ():
    coords, y = _grid_points()
    blocks = assign_blocks(coords, 5000)
    a = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=1)
    b = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=99)
    assert a.presences_per_fold == b.presences_per_fold or a.fold_of_point != b.fold_of_point


def test_presences_are_reasonably_balanced_across_folds():
    coords, y = _grid_points()
    blocks = assign_blocks(coords, 5000)
    fa = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=0)
    assert sum(fa.presences_per_fold) == sum(y)
    assert max(fa.presences_per_fold) - min(fa.presences_per_fold) <= 2


def test_too_few_blocks_for_folds_raises():
    coords = [(0.0, 0.0), (1.0, 1.0)]
    blocks = assign_blocks(coords, 100000)
    with pytest.raises(ValueError, match="distinct blocks"):
        make_folds(blocks, [1, 0], n_folds=5, block_size_m=100000)


def test_thin_folds_produce_warnings():
    coords, y = _grid_points(n_side=4, spacing=6000.0, per_cell=2)
    blocks = assign_blocks(coords, 5000)
    fa = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=0)
    assert fold_adequacy_warnings(fa, min_presences=50)


# --- Harness ------------------------------------------------------------------


def _constant_model(X_tr, y_tr, X_te):
    return [0.5] * len(X_te)


def test_random_cv_is_always_flagged_untrustworthy():
    coords, y = _grid_points(n_side=6)
    X = [[c[0]] for c in coords]
    r = random_cv_untrustworthy(coords, y, X, _constant_model)
    assert r.trustworthy is False
    assert "UNTRUSTWORTHY" in r.report()


def test_spatial_cv_is_trustworthy_and_labelled():
    coords, y = _grid_points(n_side=6)
    X = [[c[0]] for c in coords]
    r = spatial_block_cv(coords, y, X, _constant_model, block_size_m=5000)
    assert r.trustworthy is True
    assert "UNTRUSTWORTHY" not in r.report()


def test_block_size_outside_mandated_range_warns():
    coords, y = _grid_points(n_side=6, spacing=3000.0)
    X = [[c[0]] for c in coords]
    r = spatial_block_cv(coords, y, X, _constant_model, block_size_m=2000)
    assert any("5-10 km" in w for w in r.warnings)


def test_harness_rejects_wrong_length_predictions():
    coords, y = _grid_points(n_side=6)
    X = [[c[0]] for c in coords]
    with pytest.raises(ValueError, match="scores for"):
        spatial_block_cv(coords, y, X, lambda a, b, c: [0.5], block_size_m=5000)


def test_compare_returns_both_schemes():
    coords, y = _grid_points(n_side=6)
    X = [[c[0]] for c in coords]
    blocked, naive = compare_schemes(coords, y, X, _constant_model, block_size_m=5000)
    assert blocked.trustworthy and not naive.trustworthy


# --- Exclusion buffer ---------------------------------------------------------

from src.validate.harness import _excluded_by_buffer


def test_buffer_excludes_only_points_within_range():
    coords = [(0.0, 0.0), (1000.0, 0.0), (5000.0, 0.0)]
    excluded = _excluded_by_buffer(coords, train_idx=[1, 2], test_idx=[0], buffer_m=2000.0)
    assert excluded == {1}


def test_zero_buffer_excludes_nothing():
    coords = [(0.0, 0.0), (1.0, 0.0)]
    assert _excluded_by_buffer(coords, [1], [0], 0.0) == set()


def test_buffer_is_a_true_radius_not_a_grid_cell():
    # Diagonal distance ~1414 m must fall inside a 2000 m buffer, and a point
    # at 2500 m due east must not, even though both sit in neighbouring cells.
    coords = [(0.0, 0.0), (1000.0, 1000.0), (2500.0, 0.0)]
    assert _excluded_by_buffer(coords, [1, 2], [0], 2000.0) == {1}


def test_buffered_training_set_has_no_point_near_any_test_point():
    """The property the buffer exists to guarantee."""
    coords, y = _grid_points(n_side=8, spacing=6000.0, per_cell=3)
    blocks = assign_blocks(coords, 5000)
    fa = make_folds(blocks, y, n_folds=5, block_size_m=5000, seed=0)
    buffer_m = 4000.0
    for f in range(5):
        tr = [i for i, g in enumerate(fa.fold_of_point) if g != f]
        te = [i for i, g in enumerate(fa.fold_of_point) if g == f]
        dropped = _excluded_by_buffer(coords, tr, te, buffer_m)
        kept = [i for i in tr if i not in dropped]
        for i in kept:
            for j in te:
                d2 = (coords[i][0] - coords[j][0]) ** 2 + (coords[i][1] - coords[j][1]) ** 2
                assert d2 > buffer_m ** 2, f"train {i} is within the buffer of test {j}"


def test_unbuffered_run_warns_about_edge_leakage():
    coords, y = _grid_points(n_side=6)
    X = [[c[0]] for c in coords]
    r = spatial_block_cv(coords, y, X, _constant_model, block_size_m=5000, buffer_m=0)
    assert any("no exclusion buffer" in w for w in r.warnings)
