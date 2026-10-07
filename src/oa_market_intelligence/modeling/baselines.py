"""Baselines a trained classifier has to beat.

All follow the scikit-learn `fit` / `predict` shape so the walk-forward harness treats
them like any other model. Under that harness each test month is predicted right after
the training rows, so "the last training label" is exactly last month's actual direction.

From weakest to strongest idea: random-by-class-mix (what chance looks like), always-
majority (the lazy floor), persistence (the proposal's mandatory bar), and a seasonal rule
(the strongest simple baseline: it uses only the calendar, which the EDA found to be the
clearest signal).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Ties between equally common labels resolve toward Flat (the no-change guess), then Down
# (the class the brand manager cares most about, PROPOSAL.md §17.2).
TIE_ORDER = ("Flat", "Down", "Up")


def _most_common(labels: pd.Series) -> str:
    counts = pd.Series(labels).value_counts()
    top = counts.max()
    return next(c for c in TIE_ORDER if counts.get(c, 0) == top)


class MajorityClassBaseline:
    """Always predicts the most common label in the training months."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> MajorityClassBaseline:
        self.label_ = _most_common(y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), self.label_, dtype=object)


class PersistenceBaseline:
    """Predicts that next month's direction repeats the most recent known one
    (PROPOSAL.md §6.1). The mandatory bar for any trained model."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> PersistenceBaseline:
        self.label_ = pd.Series(y).iloc[-1]
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), self.label_, dtype=object)


class StratifiedRandomBaseline:
    """Draws each prediction at random in the training months' class proportions: what
    chance looks like. A single run is noisy, so judge it over many seeds
    (`evaluation.simulate_scores`).

    The generator is seeded by (`random_state`, number of training months), so every fold
    of a walk-forward run draws independently while a whole run stays reproducible."""

    def __init__(self, random_state: int = 0):
        self.random_state = random_state

    def fit(self, X: pd.DataFrame, y: pd.Series) -> StratifiedRandomBaseline:
        shares = pd.Series(y).value_counts(normalize=True).sort_index()
        self.classes_ = shares.index.to_numpy(dtype=object)
        self.probabilities_ = shares.to_numpy()
        self._rng = np.random.default_rng([self.random_state, len(y)])
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._rng.choice(self.classes_, size=len(X), p=self.probabilities_)


class SeasonalBaseline:
    """Predicts, for each calendar month, the label that month most often had in the
    training months (ties as in `TIE_ORDER`). A calendar month never seen in training falls
    back to the overall most common label.

    Expects `X` indexed by `month_id` (YYYYMM), as the walk-forward harness provides."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> SeasonalBaseline:
        labels = pd.Series(np.asarray(y, dtype=object), index=np.asarray(X.index) % 100)
        self.by_month_ = {month: _most_common(group) for month, group in labels.groupby(level=0)}
        self.fallback_ = _most_common(labels)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        months = np.asarray(X.index) % 100
        return np.array([self.by_month_.get(m, self.fallback_) for m in months], dtype=object)
