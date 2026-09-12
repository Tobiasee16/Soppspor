"""The two models, as callables the validation harness can run.

Each model is a small callable object: calling it with `(X_train, y_train,
X_test)` fits from scratch and returns scores, which is exactly the contract
the harness expects. Nothing survives between calls. That is deliberate -
anything carried from one fold to the next would be leakage, and the shape of
the code should make it impossible rather than merely discouraged.

`fit()` is the same fit exposed separately, returning a predict-only callable.
Permutation importance needs to shuffle a column and re-score many times
against one fixed model; without this it would refit for every shuffle, which
is both slow and conceptually wrong - permutation importance asks what a
*trained* model relies on.

CLAUDE.md asks for `lightgbm` compared against a MaxEnt-style baseline. The two
are chosen to fail differently, not to be equally good:

* **LightGBM** finds interactions and thresholds. Given effort-biased
  presence-only data it is equally capable of finding the shape of the road
  network, which is why it is never read without the null control beside it.
* **The MaxEnt-style baseline** is regularised logistic regression on linear
  and quadratic terms - essentially what MaxEnt does for presence-background
  data with linear and quadratic features, minus the software. It can only
  express smooth unimodal responses. That restriction is the point: if the
  flexible model beats it by a lot, the difference is either real interaction
  structure or overfitting, and blocked CV is what separates the two.

Every model is seeded. Two runs on the same inputs must produce the same table,
or a "result" is partly a draw from a random number generator.
"""
from __future__ import annotations

import numpy as np

SEED = 0


def _as_array(rows) -> np.ndarray:
    return np.asarray(rows, dtype="float64").reshape(len(rows), -1)


class MaxentStyle:
    """Regularised logistic regression on linear and quadratic terms.

    Features are standardised on the TRAINING fold only, and that scaling is
    then applied to the test fold. Fitting the scaler on everything first is a
    small, popular and entirely genuine leak: the test fold's mean and spread
    would be baked into the training data.
    """

    name = "maxent-style (L1 logistic, linear + quadratic)"

    def __init__(self, l1_c: float = 1.0, quadratic: bool = True):
        self.l1_c = l1_c
        self.quadratic = quadratic

    def _expand(self, Z: np.ndarray) -> np.ndarray:
        return np.hstack([Z, Z ** 2]) if self.quadratic else Z

    def fit(self, X_tr, y_tr):
        from sklearn.linear_model import LogisticRegression

        Xtr = _as_array(X_tr)
        mu = Xtr.mean(axis=0)
        sd = Xtr.std(axis=0)
        sd[sd < 1e-12] = 1.0            # a constant feature carries no information

        # `l1_ratio=1.0` rather than the deprecated `penalty="l1"`: scikit-learn
        # 1.8 warns on the old spelling and removes it in 1.10. Verified
        # 2026-09-09 that the two give identical coefficients here, so this is
        # a rename and not a change of model - worth checking rather than
        # assuming, since a silent slide from L1 to L2 would change the
        # registered baseline without changing anything visible.
        model = LogisticRegression(l1_ratio=1.0, C=self.l1_c, solver="liblinear",
                                   max_iter=5000, random_state=SEED)
        model.fit(self._expand((Xtr - mu) / sd), np.asarray(y_tr))

        def predict(X_te):
            return list(model.predict_proba(
                self._expand((_as_array(X_te) - mu) / sd))[:, 1])

        return predict

    def __call__(self, X_tr, y_tr, X_te):
        return self.fit(X_tr, y_tr)(X_te)


class LightGBM:
    """Gradient-boosted trees, deliberately small.

    The training sets here are a few hundred presences against a few thousand
    background points. Defaults tuned for far larger data would memorise the
    training folds and report it as skill, so `num_leaves` and
    `min_child_samples` are held down: each tree has to describe a region of
    environmental space rather than an individual record.

    These values were set once, before any result was seen, and are not
    adjusted afterwards. Tuning them against the blocked-CV score would make
    the validation set part of the training procedure and the reported AUC an
    optimistic one.
    """

    name = "lightgbm (gradient-boosted trees)"

    def __init__(self, n_estimators: int = 300, learning_rate: float = 0.05,
                 num_leaves: int = 15, min_child_samples: int = 20):
        self.params = dict(
            n_estimators=n_estimators, learning_rate=learning_rate,
            num_leaves=num_leaves, min_child_samples=min_child_samples,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            random_state=SEED, n_jobs=1, verbose=-1,
        )

    def fit(self, X_tr, y_tr):
        from lightgbm import LGBMClassifier

        model = LGBMClassifier(**self.params)
        model.fit(_as_array(X_tr), np.asarray(y_tr))
        return lambda X_te: list(model.predict_proba(_as_array(X_te))[:, 1])

    def __call__(self, X_tr, y_tr, X_te):
        return self.fit(X_tr, y_tr)(X_te)


class Constant:
    """Predicts the same score everywhere.

    A floor to measure the metrics against: AUC must come out at exactly 0.5.
    If a scoring pipeline ever reports this as better than chance, the fault is
    in the scoring, not in the model.
    """

    name = "constant (floor)"

    def __init__(self, value: float = 0.5):
        self.value = value

    def fit(self, X_tr, y_tr):
        return lambda X_te: [self.value] * len(X_te)

    def __call__(self, X_tr, y_tr, X_te):
        return self.fit(X_tr, y_tr)(X_te)


def permutation_importance(model, X_tr, y_tr, X_te, y_te, names, metric,
                           n_repeats: int = 5, seed: int = SEED) -> dict:
    """Drop in metric when each feature is shuffled, against one fitted model.

    This is how the accusation registered in FINDINGS section 6 gets tested:
    autumn rainfall follows the same coast-inland gradient as the population,
    so a model leaning on it may be reading accessibility rather than ecology.

    Importance is computed on the HELD-OUT fold. Measured on training data it
    would describe what the model memorised rather than what it uses to
    generalise, which is the opposite of the question being asked.
    """
    rng = np.random.default_rng(seed)
    predict = model.fit(X_tr, y_tr)
    Xte = _as_array(X_te)
    base = metric(list(y_te), list(predict(Xte)))

    out = {}
    for j, name in enumerate(names):
        drops = []
        for _ in range(n_repeats):
            shuffled = Xte.copy()
            shuffled[:, j] = rng.permutation(shuffled[:, j])
            drops.append(base - metric(list(y_te), list(predict(shuffled))))
        out[name] = {"mean_drop": float(np.mean(drops)), "sd": float(np.std(drops))}
    return out
