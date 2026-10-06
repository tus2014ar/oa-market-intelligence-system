"""Baselines a trained classifier has to beat.

Both follow the scikit-learn `fit` / `predict` shape so the walk-forward harness treats
them like any other model. Under that harness each test month is predicted right after
the training rows, so "the last training label" is exactly last month's actual direction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Ties between equally common labels resolve toward Flat (the no-change guess), then Down
# (the class the brand manager cares most about, PROPOSAL.md §17.2).
TIE_ORDER = ("Flat", "Down", "Up")


class MajorityClassBaseline:
    """Always predicts the most common label in the training months."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> MajorityClassBaseline:
        counts = pd.Series(y).value_counts()
        top = counts.max()
        self.label_ = next(c for c in TIE_ORDER if counts.get(c, 0) == top)
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
