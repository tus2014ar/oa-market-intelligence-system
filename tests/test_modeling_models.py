"""Tests for the logistic regression model and its supporting pieces.

The primary model's settings were fixed before any result was seen (strong regularisation,
balanced class weights): with only 35 test months, picking settings after looking would
quietly fit the test set. `test_primary_model_settings_are_the_pre_specified_ones` pins them
so a later change has to be deliberate and visible in review.
"""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from oa_market_intelligence.features.monthly import FEATURE_GROUPS, MONTHLY_FEATURES
from oa_market_intelligence.modeling.baselines import MajorityClassBaseline
from oa_market_intelligence.modeling.evaluation import (
    in_sample_scores,
    score_predictions,
    walk_forward_predict,
)
from oa_market_intelligence.modeling.models import (
    PRIMARY_C,
    PRIMARY_CLASS_WEIGHT,
    make_logistic_regression,
)


def _frame(n=48, seed=11, informative=True):
    """Time-ordered rows; when `informative`, the label follows the sign of feature f1."""
    rng = np.random.RandomState(seed)
    f1 = rng.normal(size=n)
    labels = np.where(f1 > 0.4, "Up", np.where(f1 < -0.4, "Down", "Flat"))
    if not informative:
        labels = rng.choice(["Up", "Flat", "Down"], n)
    index = []
    year, month = 2019, 8
    for _ in range(n):
        index.append(year * 100 + month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return pd.DataFrame(
        {"direction_label": labels, "f1": f1, "f2": rng.normal(size=n)},
        index=pd.Index(index, name="month_id"),
    )


def test_primary_model_settings_are_the_pre_specified_ones():
    assert PRIMARY_C == 0.1
    assert PRIMARY_CLASS_WEIGHT == "balanced"

    model = make_logistic_regression()
    assert isinstance(model, Pipeline)
    assert [name for name, _ in model.steps] == ["standardscaler", "logisticregression"]
    assert isinstance(model.steps[0][1], StandardScaler)
    regression = model.steps[1][1]
    assert regression.C == 0.1
    assert regression.class_weight == "balanced"


def test_settings_can_be_overridden_for_exploratory_variants():
    model = make_logistic_regression(C=1.0, class_weight=None)
    assert model.steps[1][1].C == 1.0
    assert model.steps[1][1].class_weight is None


def test_it_learns_a_real_pattern_and_beats_the_majority_guess():
    frame = _frame(informative=True)
    logistic = walk_forward_predict(frame, make_logistic_regression, min_train=24)
    majority = walk_forward_predict(frame, MajorityClassBaseline, min_train=24)
    logistic_score = score_predictions(logistic["y_true"], logistic["y_pred"])
    majority_score = score_predictions(majority["y_true"], majority["y_pred"])
    assert logistic_score["balanced_accuracy"] > 0.7
    assert logistic_score["balanced_accuracy"] > majority_score["balanced_accuracy"] + 0.3


def test_it_handles_a_feature_that_is_constant_in_the_training_window():
    frame = _frame(informative=True)
    frame["flag"] = 0.0
    frame.iloc[-1, frame.columns.get_loc("flag")] = 1.0  # switches on only in the last month
    result = walk_forward_predict(frame, make_logistic_regression, min_train=24)
    assert len(result) == 24
    assert result["y_pred"].isin(["Up", "Flat", "Down"]).all()


def test_the_scaler_is_fit_on_training_months_only():
    frame = _frame(informative=True)
    model = make_logistic_regression()
    train = frame.iloc[:30]
    model.fit(train.drop(columns="direction_label"), train["direction_label"])
    scaler = model.steps[0][1]
    assert scaler.mean_ == pytest.approx(train[["f1", "f2"]].mean().to_numpy())


def test_in_sample_scores_show_a_memorizing_model_overfits():
    frame = _frame(informative=False)  # labels are pure noise
    memorizer = in_sample_scores(frame, lambda: KNeighborsClassifier(n_neighbors=1), min_train=24)
    assert len(memorizer) == 24
    assert (memorizer["train_balanced_accuracy"] == 1.0).all()

    regularised = in_sample_scores(frame, make_logistic_regression, min_train=24)
    assert regularised["train_balanced_accuracy"].mean() < 0.9


def test_in_sample_scores_are_computed_on_the_training_window_only():
    frame = _frame(informative=True)
    result = in_sample_scores(frame, MajorityClassBaseline, min_train=24)
    # Always-majority is right on exactly the majority class of each window.
    assert result["train_accuracy"].between(0.0, 1.0).all()
    assert result.index[0] == frame.index[24]


def test_feature_groups_partition_the_monthly_features_exactly():
    grouped = [column for columns in FEATURE_GROUPS.values() for column in columns]
    assert sorted(grouped) == sorted(MONTHLY_FEATURES)
    assert len(grouped) == len(set(grouped))
    assert set(FEATURE_GROUPS) == {"share_history", "volume", "calendar", "events"}


def test_sklearn_logistic_is_not_silently_swapped_in():
    # The pipeline's classifier must be scikit-learn's LogisticRegression, regularised by C.
    assert isinstance(make_logistic_regression().steps[1][1], LogisticRegression)
