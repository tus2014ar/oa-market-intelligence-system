"""Candidate models for the Up / Down / Flat classifier.

Each `make_*` function returns a fresh, unfitted scikit-learn estimator with sensible
defaults, so it can be handed straight to the walk-forward harness as a model factory. The
harness builds a new model for every fold, which keeps scaling and any other learning
strictly inside that fold's training months.

Primary logistic regression: settings fixed BEFORE any result was seen
-----------------------------------------------------------------------
With 24 to 58 training months, about 16 Up or Down months in all, and 22 features,
overfitting is the main risk, and choosing settings by looking at 35 test months would
quietly fit the test set. So the primary model's settings are pre-specified:

- standardised features (so the penalty treats them equally),
- L2 regularisation with strong shrinkage, C = 0.1 (smaller C means stronger shrinkage),
- balanced class weights, because the priority metric is recall on the rare Down class and
  accuracy alone rewards always saying Flat.

Other values of C, other class weights and feature ablations are run only as exploratory
sensitivity checks and reported in full, never used to pick a winner.
"""

from __future__ import annotations

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

PRIMARY_C = 0.1
PRIMARY_CLASS_WEIGHT = "balanced"


def make_logistic_regression(
    *, C: float = PRIMARY_C, class_weight: str | dict | None = PRIMARY_CLASS_WEIGHT
):
    """Standardise the features, then an L2-regularised multinomial logistic regression."""
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=C, class_weight=class_weight, max_iter=2000),
    )
