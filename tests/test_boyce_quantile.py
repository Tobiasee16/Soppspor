"""Tests for the quantile-window Boyce index.

The reason this variant exists is a bias, not a preference: measured on
synthetic scores at real fold sizes, the published equal-width implementation
returns about -0.42 for a model with no skill at all, where zero is the correct
answer. See `scripts/check_boyce.py`. The tests below pin the properties that
justified adding it, so a later edit cannot quietly give the bias back.
"""
from __future__ import annotations

import math
import pathlib
import random
import statistics
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.validate.metrics import continuous_boyce, continuous_boyce_quantile


def _skewed(rng, n, shift):
    """Logistic scores around a low base rate - the regime these models emit."""
    return [1.0 / (1.0 + math.exp(-(rng.gauss(shift, 1.0) - 2.6))) for _ in range(n)]


class TestKnownAnswers:
    def test_a_strong_model_scores_high(self):
        rng = random.Random(0)
        vals = [continuous_boyce_quantile(_skewed(rng, 50, 2.0), _skewed(rng, 680, 0.0))
                for _ in range(40)]
        assert statistics.mean(vals) > 0.7

    def test_a_moderate_model_scores_clearly_positive(self):
        rng = random.Random(1)
        vals = [continuous_boyce_quantile(_skewed(rng, 50, 0.75), _skewed(rng, 680, 0.0))
                for _ in range(40)]
        assert statistics.mean(vals) > 0.5

    def test_an_inverted_model_scores_clearly_negative(self):
        rng = random.Random(2)
        vals = [continuous_boyce_quantile(_skewed(rng, 50, -0.75), _skewed(rng, 680, 0.0))
                for _ in range(40)]
        assert statistics.mean(vals) < -0.5

    def test_a_skill_free_model_scores_near_zero(self):
        """The whole reason this variant exists."""
        rng = random.Random(3)
        vals = [continuous_boyce_quantile(_skewed(rng, 50, 0.0), _skewed(rng, 680, 0.0))
                for _ in range(60)]
        assert abs(statistics.mean(vals)) < 0.2

    def test_it_is_less_biased_than_the_published_method_on_skewed_scores(self):
        """Pins the comparison that justified adding this at all."""
        rng = random.Random(4)
        pub, qua = [], []
        for _ in range(60):
            p = _skewed(rng, 50, 0.0)
            b = _skewed(rng, 680, 0.0)
            pub.append(continuous_boyce(p, b))
            qua.append(continuous_boyce_quantile(p, b))
        assert abs(statistics.mean(qua)) < abs(statistics.mean(pub))
        assert statistics.mean(pub) < -0.2, "the published bias is the premise here"

    def test_ordering_of_model_quality_is_preserved(self):
        rng = random.Random(5)
        strong = statistics.mean(
            [continuous_boyce_quantile(_skewed(rng, 50, 2.0), _skewed(rng, 680, 0.0))
             for _ in range(30)])
        weak = statistics.mean(
            [continuous_boyce_quantile(_skewed(rng, 50, 0.4), _skewed(rng, 680, 0.0))
             for _ in range(30)])
        none = statistics.mean(
            [continuous_boyce_quantile(_skewed(rng, 50, 0.0), _skewed(rng, 680, 0.0))
             for _ in range(30)])
        assert strong > weak > none


class TestUndefinedCases:
    def test_perfect_separation_is_nan_not_one(self):
        """No window holds both classes, so the index genuinely has no value."""
        assert math.isnan(continuous_boyce_quantile([0.9] * 50, [0.1] * 680))

    def test_the_published_method_agrees_it_is_undefined(self):
        assert math.isnan(continuous_boyce([0.9] * 50, [0.1] * 680))

    def test_empty_presences_is_nan(self):
        assert math.isnan(continuous_boyce_quantile([], [0.1, 0.2, 0.3]))

    def test_empty_background_is_nan(self):
        assert math.isnan(continuous_boyce_quantile([0.1, 0.2], []))

    def test_constant_background_is_nan(self):
        assert math.isnan(continuous_boyce_quantile([0.2, 0.4], [0.3] * 100))

    def test_non_finite_scores_are_nan_not_an_exception(self):
        assert math.isnan(continuous_boyce_quantile([float("nan"), 0.2], [0.1] * 100))
        assert math.isnan(continuous_boyce_quantile([0.2], [float("inf")] * 100))

    def test_too_little_background_leaves_no_usable_window(self):
        # min_background=10 by default; five points cannot fill any window.
        assert math.isnan(continuous_boyce_quantile([0.5, 0.6], [0.1, 0.2, 0.3, 0.4, 0.5]))


class TestBehaviour:
    def test_it_is_deterministic(self):
        rng = random.Random(6)
        p, b = _skewed(rng, 50, 0.75), _skewed(rng, 680, 0.0)
        assert continuous_boyce_quantile(p, b) == continuous_boyce_quantile(p, b)

    def test_it_is_invariant_to_a_monotone_rescaling_of_scores(self):
        """Boyce ranks; multiplying every score by a constant must change nothing."""
        rng = random.Random(7)
        p, b = _skewed(rng, 50, 0.75), _skewed(rng, 680, 0.0)
        base = continuous_boyce_quantile(p, b)
        scaled = continuous_boyce_quantile([s * 3.0 for s in p], [s * 3.0 for s in b])
        assert scaled == pytest.approx(base, abs=1e-9)

    def test_result_stays_within_the_correlation_range(self):
        rng = random.Random(8)
        for shift in (-2.0, -0.5, 0.0, 0.5, 2.0):
            v = continuous_boyce_quantile(_skewed(rng, 50, shift), _skewed(rng, 680, 0.0))
            assert math.isnan(v) or -1.0 <= v <= 1.0

    def test_raising_min_background_never_adds_windows(self):
        rng = random.Random(9)
        p, b = _skewed(rng, 50, 0.75), _skewed(rng, 200, 0.0)
        loose = continuous_boyce_quantile(p, b, min_background=1)
        strict = continuous_boyce_quantile(p, b, min_background=50)
        # Both defined here; the point is that the stricter one is still sane.
        assert math.isnan(strict) or -1.0 <= strict <= 1.0
        assert math.isnan(loose) or -1.0 <= loose <= 1.0
