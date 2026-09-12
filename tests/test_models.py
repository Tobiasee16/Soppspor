"""Tests for the models.

These do not test that the models are good. They test that they are honest:
seeded and reproducible, refitting from scratch every call, scaling learned on
training data only, and a floor model that scores exactly 0.5 so the metrics
themselves can be trusted.
"""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.model.models import Constant, LightGBM, MaxentStyle, permutation_importance
from src.validate.metrics import roc_auc

MODELS = [MaxentStyle, LightGBM]


def separable(n=300, seed=0):
    """A clean two-cluster problem: any working model must score well above 0.5."""
    rng = np.random.default_rng(seed)
    pos = rng.normal(2.0, 1.0, (n, 3))
    neg = rng.normal(-2.0, 1.0, (n, 3))
    X = np.vstack([pos, neg]).tolist()
    y = [1] * n + [0] * n
    return X, y


def noise(n=300, seed=0):
    """No signal at all. Anything much above 0.5 here is a leak."""
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, (2 * n, 3)).tolist()
    y = [1] * n + [0] * n
    return X, y


@pytest.mark.parametrize("cls", MODELS)
def test_a_model_learns_a_separable_problem(cls):
    X, y = separable()
    scores = cls()(X, y, X)
    assert roc_auc(y, scores) > 0.9


@pytest.mark.parametrize("cls", MODELS)
def test_a_model_is_reproducible(cls):
    X, y = separable()
    assert cls()(X, y, X) == cls()(X, y, X), "an unseeded model is not a result"


@pytest.mark.parametrize("cls", MODELS)
def test_nothing_survives_between_calls(cls):
    """A reused instance must give the same answer as a fresh one.

    The harness refits inside every fold and calls the same object each time.
    If anything at all were carried over - a fitted estimator, a scaler, a
    warm start - the second fold would be trained on the first fold's test
    data, which is leakage of the plainest kind.
    """
    Xn, yn = noise()
    fresh = cls()(Xn, yn, Xn)

    reused = cls()
    Xs, ys = separable()
    reused(Xs, ys, Xs)               # fit on a completely different problem first
    assert reused(Xn, yn, Xn) == pytest.approx(fresh)


@pytest.mark.parametrize("cls", MODELS)
def test_one_score_is_returned_per_test_row(cls):
    X, y = separable(n=60)
    assert len(cls()(X, y, X[:17])) == 17


@pytest.mark.parametrize("cls", MODELS)
def test_scores_are_probabilities(cls):
    X, y = separable(n=60)
    assert all(0.0 <= s <= 1.0 for s in cls()(X, y, X))


def test_the_constant_model_scores_exactly_one_half():
    """The floor the metrics are checked against."""
    X, y = separable(n=50)
    assert roc_auc(y, Constant()(X, y, X)) == pytest.approx(0.5)


def test_the_maxent_scaler_is_learned_on_training_data_only():
    """A test fold shifted far away must not be silently re-centred onto the model.

    If the scaler were fitted on the test data, a uniform shift would be
    subtracted away and the scores would be unchanged. They must not be.
    """
    X, y = separable(n=200)
    model = MaxentStyle()
    predict = model.fit(X, y)
    here = predict(X[:40])
    shifted = predict((np.asarray(X[:40]) + 50.0).tolist())
    assert not np.allclose(here, shifted), "the shift was absorbed, so the scaler leaked"


def test_fit_returns_a_predictor_that_matches_calling_the_model():
    X, y = separable(n=80)
    model = MaxentStyle()
    assert model.fit(X, y)(X[:10]) == pytest.approx(model(X, y, X[:10]))


# --- permutation importance ---------------------------------------------------


def test_importance_finds_the_feature_that_carries_the_signal():
    """Only column 0 separates the classes; only column 0 should matter."""
    rng = np.random.default_rng(1)
    n = 250
    X = rng.normal(0, 1, (2 * n, 3))
    X[:n, 0] += 3.0
    y = [1] * n + [0] * n
    imp = permutation_importance(MaxentStyle(), X.tolist(), y, X.tolist(), y,
                                 ["signal", "noise1", "noise2"], roc_auc, n_repeats=3)
    assert imp["signal"]["mean_drop"] > 0.1
    assert imp["noise1"]["mean_drop"] < 0.05
    assert imp["noise2"]["mean_drop"] < 0.05


def test_importance_reports_one_entry_per_named_feature():
    X, y = separable(n=60)
    imp = permutation_importance(MaxentStyle(), X, y, X, y, ["a", "b", "c"],
                                 roc_auc, n_repeats=2)
    assert set(imp) == {"a", "b", "c"}
    assert all("mean_drop" in v and "sd" in v for v in imp.values())


def test_importance_is_reproducible():
    X, y = separable(n=60)
    args = (MaxentStyle(), X, y, X, y, ["a", "b", "c"], roc_auc)
    assert (permutation_importance(*args, n_repeats=3)
            == permutation_importance(*args, n_repeats=3))
