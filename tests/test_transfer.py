"""Tests for the held-out-extent transferability evaluation.

This is the scheme CLAUDE.md's study-area section actually asks for: fit on
Agder, evaluate once on Kristiansand. It is not cross-validation, and the tests
here are mostly about making sure it can never quietly behave as though it
were - no folds, no fabricated spread, and no training point sitting inside or
beside the held-out area.
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.validate.harness import TransferResult, held_out_extent


def _perfect(X_tr, y_tr, X_te):
    """Scores the first feature directly - separates when that feature is the label."""
    return [row[0] for row in X_te]


def _constant(X_tr, y_tr, X_te):
    return [0.5] * len(X_te)


def _remembers_training_size(seen):
    def fit_predict(X_tr, y_tr, X_te):
        seen.append((len(X_tr), sum(y_tr)))
        return [row[0] for row in X_te]
    return fit_predict


def _grid(n_train_cols=6, n_test_cols=3, spacing=1000.0):
    """Points on a line: training on the left, held-out extent on the right.

    Returns coords, y, X, in_test. Every point's first feature equals its
    label, so a model that reads feature 0 scores a perfect AUC and one that
    ignores it scores 0.5. That makes the harness's own behaviour visible
    without a real model in the way.
    """
    coords, y, X, in_test = [], [], [], []
    for i in range(n_train_cols):
        for label in (1, 0):
            coords.append((i * spacing, 0.0))
            y.append(label)
            X.append([float(label)])
            in_test.append(False)
    for j in range(n_test_cols):
        for label in (1, 0):
            coords.append(((n_train_cols + j) * spacing, 0.0))
            y.append(label)
            X.append([float(label)])
            in_test.append(True)
    return coords, y, X, in_test


class TestSplitting:
    def test_only_flagged_points_are_tested_on(self):
        coords, y, X, in_test = _grid()
        seen = []
        res = held_out_extent(coords, y, X, _remembers_training_size(seen), in_test)
        assert res.n_test == sum(in_test)
        assert res.n_train == len(y) - sum(in_test)
        assert seen[0][0] == res.n_train

    def test_a_training_point_inside_the_extent_is_impossible(self):
        # The split is defined by the flag, so this is really a guard against a
        # future refactor deciding to be clever about it.
        coords, y, X, in_test = _grid()
        seen = []
        held_out_extent(coords, y, X, _remembers_training_size(seen), in_test)
        assert seen[0][0] + sum(in_test) == len(y)

    def test_buffer_removes_training_points_near_the_boundary(self):
        coords, y, X, in_test = _grid(spacing=1000.0)
        wide = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=2500.0)
        none = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=0.0)
        assert wide.n_buffered > 0
        assert wide.n_train < none.n_train
        assert wide.n_train + wide.n_buffered == none.n_train

    def test_a_bigger_buffer_never_keeps_more_training_points(self):
        coords, y, X, in_test = _grid(n_train_cols=10)
        sizes = [held_out_extent(coords, y, X, _perfect, in_test, buffer_m=b).n_train
                 for b in (0.0, 1500.0, 3500.0, 5500.0)]
        assert sizes == sorted(sizes, reverse=True)

    def test_no_buffer_warns_because_the_boundary_leaks(self):
        coords, y, X, in_test = _grid()
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=0.0)
        assert any("no exclusion buffer" in w for w in res.warnings)

    def test_a_buffer_that_eats_the_training_set_raises(self):
        coords, y, X, in_test = _grid(n_train_cols=3)
        with pytest.raises(ValueError, match="removed the entire training set"):
            held_out_extent(coords, y, X, _perfect, in_test, buffer_m=100_000.0)

    def test_empty_held_out_extent_raises(self):
        coords, y, X, _ = _grid()
        with pytest.raises(ValueError, match="no points"):
            held_out_extent(coords, y, X, _perfect, [False] * len(y))

    def test_holding_out_everything_raises(self):
        coords, y, X, _ = _grid()
        with pytest.raises(ValueError, match="nothing left to train on"):
            held_out_extent(coords, y, X, _perfect, [True] * len(y))

    def test_mismatched_flag_length_raises(self):
        coords, y, X, in_test = _grid()
        with pytest.raises(ValueError, match="in_test has"):
            held_out_extent(coords, y, X, _perfect, in_test[:-1])

    def test_wrong_number_of_scores_raises(self):
        coords, y, X, in_test = _grid()
        with pytest.raises(ValueError, match="scores for"):
            held_out_extent(coords, y, X, lambda a, b, c: [0.5], in_test)


class TestScoring:
    def test_a_separating_model_scores_one(self):
        coords, y, X, in_test = _grid()
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
        assert res.auc == pytest.approx(1.0)

    def test_a_constant_model_scores_exactly_half(self):
        coords, y, X, in_test = _grid()
        res = held_out_extent(coords, y, X, _constant, in_test, buffer_m=1500.0)
        assert res.auc == pytest.approx(0.5)

    def test_presence_and_background_counts_are_reported(self):
        coords, y, X, in_test = _grid(n_train_cols=6, n_test_cols=3)
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
        assert res.n_test_presence == 3
        assert res.n_test == 6

    def test_too_few_test_presences_warns(self):
        coords, y, X, in_test = _grid(n_test_cols=2)
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
        assert any("presences in the held-out extent" in w for w in res.warnings)

    def test_enough_test_presences_does_not_warn(self):
        coords, y, X, in_test = _grid(n_train_cols=30, n_test_cols=25)
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
        assert not any("presences in the held-out extent" in w for w in res.warnings)


class TestBootstrapInterval:
    def test_interval_brackets_the_point_estimate(self):
        coords, y, X, in_test = _grid(n_train_cols=20, n_test_cols=15)
        res = held_out_extent(coords, y, X, _constant, in_test, buffer_m=1500.0)
        assert res.auc_lo <= res.auc <= res.auc_hi

    def test_a_perfect_model_has_a_degenerate_interval(self):
        coords, y, X, in_test = _grid(n_train_cols=20, n_test_cols=15)
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
        assert res.auc_lo == pytest.approx(1.0)
        assert res.auc_hi == pytest.approx(1.0)

    def test_the_interval_is_seeded_and_reproducible(self):
        coords, y, X, in_test = _grid(n_train_cols=20, n_test_cols=15)
        kw = dict(buffer_m=1500.0, seed=7)
        a = held_out_extent(coords, y, X, _constant, in_test, **kw)
        b = held_out_extent(coords, y, X, _constant, in_test, **kw)
        assert (a.auc_lo, a.auc_hi) == (b.auc_lo, b.auc_hi)

    def test_fewer_test_points_give_a_wider_interval(self):
        # The interval has to react to sample size, or it is decoration.
        def noisy(X_tr, y_tr, X_te):
            return [row[0] * 0.5 + 0.25 * ((i * 7) % 3) for i, row in enumerate(X_te)]

        small = held_out_extent(*_grid(n_train_cols=30, n_test_cols=5)[:3], noisy,
                                _grid(n_train_cols=30, n_test_cols=5)[3],
                                buffer_m=1500.0)
        large = held_out_extent(*_grid(n_train_cols=30, n_test_cols=60)[:3], noisy,
                                _grid(n_train_cols=30, n_test_cols=60)[3],
                                buffer_m=1500.0)
        assert (small.auc_hi - small.auc_lo) > (large.auc_hi - large.auc_lo)


class TestItIsNotCrossValidation:
    def test_the_result_type_carries_no_folds(self):
        coords, y, X, in_test = _grid()
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
        assert isinstance(res, TransferResult)
        assert not hasattr(res, "folds")
        assert not hasattr(res, "n_folds")

    def test_no_standard_deviation_is_offered(self):
        """A single evaluation has no spread. Reporting +/- 0.000 would be a lie."""
        coords, y, X, in_test = _grid()
        res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
        assert not hasattr(res, "sd_auc")
        assert not hasattr(res, "sd_boyce")

    def test_the_model_is_fitted_exactly_once(self):
        coords, y, X, in_test = _grid()
        seen = []
        held_out_extent(coords, y, X, _remembers_training_size(seen), in_test,
                        buffer_m=1500.0)
        assert len(seen) == 1

    def test_the_report_names_the_scheme_and_shows_the_interval(self):
        coords, y, X, in_test = _grid(n_train_cols=20, n_test_cols=15)
        text = held_out_extent(coords, y, X, _constant, in_test,
                               buffer_m=1500.0).report()
        assert "transferability" in text
        assert "bootstrap 95% CI" in text
        assert "+/-" not in text


def test_boyce_is_finite_or_nan_never_silently_zero():
    coords, y, X, in_test = _grid(n_train_cols=20, n_test_cols=15)
    res = held_out_extent(coords, y, X, _perfect, in_test, buffer_m=1500.0)
    assert math.isnan(res.boyce) or -1.0 <= res.boyce <= 1.0
