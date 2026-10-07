"""Random forest and gradient boosting for the monthly direction task (Q2).

Both use the same 22 monthly features and the same walk-forward as the logistic regression and
the baselines. Each picks its own settings inside every training window: the last 12 months are
held out as a validation block, each setting is fitted on the months before it and scored by
balanced accuracy, the best wins (a tie goes to the simpler setting, which comes first in each
grid), and the winner is refitted on the whole window. A training window with a single class
just predicts that class.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.utils.class_weight import compute_sample_weight

from oa_market_intelligence.modeling.evaluation import score_predictions

SEED = 0
# simplest first, so that a tie in validation balanced accuracy keeps the simpler setting
RF_CONFIGS = [{"max_depth": d, "min_samples_leaf": leaf} for d in (2, 4) for leaf in (6, 3)]
GBM_CONFIGS = [{"max_depth": d, "learning_rate": lr} for d in (1, 2) for lr in (0.05, 0.1)]


class _Guarded:
    """An estimator that predicts the only class it saw instead of failing on one class."""

    def __init__(self, build: Callable[[], object], balanced_weights: bool):
        self._build = build
        self._weights = balanced_weights

    def fit(self, X: pd.DataFrame, y: pd.Series) -> _Guarded:
        classes = pd.unique(y)
        self._constant = classes[0] if len(classes) == 1 else None
        self._model = None
        if self._constant is None:
            self._model = self._build()
            if self._weights:
                self._model.fit(X, y, sample_weight=compute_sample_weight("balanced", y))
            else:
                self._model.fit(X, y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._model is None:
            return np.full(len(X), self._constant, dtype=object)
        return np.asarray(self._model.predict(X), dtype=object)


class TunedDirectionClassifier:
    """Chooses among `configs` by balanced accuracy on the last `val_months` months of whatever
    it is fitted on, then refits the winner on everything."""

    def __init__(self, build: Callable[[dict], object], configs: list[dict], val_months: int = 12):
        self._build = build
        self._configs = configs
        self._val = val_months

    def fit(self, X: pd.DataFrame, y: pd.Series) -> TunedDirectionClassifier:
        self.chosen = 0
        if len(X) > self._val:
            cut = len(X) - self._val
            scores = []
            for config in self._configs:
                model = self._build(config).fit(X.iloc[:cut], y.iloc[:cut])
                predicted = model.predict(X.iloc[cut:])
                scores.append(score_predictions(y.iloc[cut:], predicted)["balanced_accuracy"])
            self.chosen = int(np.argmax(scores))  # the first maximum, so ties keep the simpler one
        self._model = self._build(self._configs[self.chosen]).fit(X, y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._model.predict(X)


def make_random_forest(n_estimators: int = 300) -> TunedDirectionClassifier:
    def build(config: dict) -> _Guarded:
        return _Guarded(
            lambda: RandomForestClassifier(
                n_estimators=n_estimators, class_weight="balanced", random_state=SEED,
                n_jobs=1, **config
            ),
            balanced_weights=False,
        )

    return TunedDirectionClassifier(build, RF_CONFIGS)


def make_gbm(n_estimators: int = 100) -> TunedDirectionClassifier:
    def build(config: dict) -> _Guarded:
        return _Guarded(
            lambda: GradientBoostingClassifier(
                n_estimators=n_estimators, random_state=SEED, **config
            ),
            balanced_weights=True,
        )

    return TunedDirectionClassifier(build, GBM_CONFIGS)
