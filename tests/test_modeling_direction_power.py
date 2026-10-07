"""Tests for the extra direction classifiers and the power simulation (Step 12).

The classifiers tune their own settings inside each training window; the power simulation
asks how big an accuracy gain over persistence 35 test months could detect. Its answer at one
point is known exactly (when the better classifier never loses a month the McNemar test is a
binomial), which pins the simulation to an analytic value.
"""

from functools import partial

import numpy as np
import pandas as pd
import pytest
from scipy.stats import binom

from oa_market_intelligence.modeling.direction_models import (
    GBM_CONFIGS,
    RF_CONFIGS,
    TunedDirectionClassifier,
    make_gbm,
    make_random_forest,
)
from oa_market_intelligence.modeling.evaluation import score_predictions, walk_forward_predict
from oa_market_intelligence.modeling.power import mcnemar_power, smallest_detectable_gain


class FixedLabel:
    """An estimator that always predicts one label, for checking the tuning logic."""

    def __init__(self, label):
        self.label = label

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.full(len(X), self.label, dtype=object)


def _frame(labels):
    X = pd.DataFrame({"x": np.arange(len(labels), dtype=float)})
    return X, pd.Series(labels, dtype=object)


def test_the_grids_are_the_ones_fixed_in_the_plan():
    assert {(c["max_depth"], c["min_samples_leaf"]) for c in RF_CONFIGS} == {
        (2, 3), (2, 6), (4, 3), (4, 6)
    }
    assert {(c["max_depth"], c["learning_rate"]) for c in GBM_CONFIGS} == {
        (1, 0.05), (1, 0.1), (2, 0.05), (2, 0.1)
    }
    assert RF_CONFIGS[0]["max_depth"] == 2 and RF_CONFIGS[0]["min_samples_leaf"] == 6  # simplest
    assert GBM_CONFIGS[0]["max_depth"] == 1  # simplest first, for tie-breaking


def test_tuning_picks_the_setting_with_the_better_balanced_accuracy_on_the_last_12_months():
    # the last 12 months are all Up, so the setting that predicts Up wins
    X, y = _frame(["Flat"] * 12 + ["Up"] * 12)
    configs = [{"label": "Flat"}, {"label": "Up"}]
    model = TunedDirectionClassifier(lambda c: FixedLabel(c["label"]), configs, val_months=12)
    model.fit(X, y)
    assert model.chosen == 1
    assert model.predict(X.iloc[:3]).tolist() == ["Up", "Up", "Up"]


def test_a_tie_in_validation_balanced_accuracy_goes_to_the_first_setting():
    X, y = _frame(["Flat"] * 24)
    configs = [{"label": "Flat"}, {"label": "Flat"}]
    model = TunedDirectionClassifier(lambda c: FixedLabel(c["label"]), configs, val_months=12)
    model.fit(X, y)
    assert model.chosen == 0


def test_settings_are_fitted_on_months_before_the_validation_block_only():
    seen = []

    class Spy(FixedLabel):
        def fit(self, X, y):
            seen.append(len(X))
            return self

    X, y = _frame(["Flat"] * 24)
    TunedDirectionClassifier(lambda c: Spy("Flat"), [{}, {}], val_months=12).fit(X, y)
    assert seen[:2] == [12, 12]  # each setting is fitted on the first 12 months...
    assert seen[2] == 24  # ...and the winner is refitted on all 24


def test_a_training_window_with_one_class_predicts_that_class_without_failing():
    X, y = _frame(["Flat"] * 24)
    for make in (partial(make_random_forest, 20), partial(make_gbm, 20)):
        model = make()
        model.fit(X, y)
        assert model.predict(X.iloc[:2]).tolist() == ["Flat", "Flat"]


FAST = {"rf": partial(make_random_forest, 20), "gbm": partial(make_gbm, 20)}


def _signal_frame(n=45, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, n)
    label = np.where(x > 0.6, "Up", np.where(x < -0.6, "Down", "Flat"))
    frame = pd.DataFrame({"x": x, "noise": rng.normal(0, 1, n)})
    frame["direction_label"] = label
    return frame


@pytest.mark.parametrize("name", ["rf", "gbm"])
def test_the_classifiers_learn_a_clear_signal(name):
    frame = _signal_frame()
    run = walk_forward_predict(frame, FAST[name], min_train=24)
    assert score_predictions(run["y_true"], run["y_pred"])["balanced_accuracy"] > 0.7


@pytest.mark.parametrize("name", ["rf", "gbm"])
def test_fitting_twice_gives_identical_predictions(name):
    frame = _signal_frame()
    first = walk_forward_predict(frame, FAST[name], min_train=24)
    again = walk_forward_predict(frame, FAST[name], min_train=24)
    assert (first["y_pred"] == again["y_pred"]).all()


def test_power_equals_a_binomial_tail_when_the_better_classifier_never_loses_a_month():
    n, d = 35, 0.30
    table = mcnemar_power(n=n, accuracy=0.63, discordance=d, deltas=[d], n_sim=4000, seed=0)
    # every discordant month favours B; the exact test is significant when at least 6 do
    expected = binom.sf(5, n, d)
    assert table["power"].iloc[0] == pytest.approx(expected, abs=0.025)


def test_power_is_small_with_no_true_gain_and_rises_with_the_gain():
    table = mcnemar_power(n=35, accuracy=0.63, discordance=0.4,
                          deltas=[0.0, 0.1, 0.2, 0.3], n_sim=2000, seed=1)
    power = table["power"].to_numpy()
    assert power[0] < 0.06  # no gain: the false-positive rate is at most the 5% level
    assert (np.diff(power) > 0).all()


def test_with_no_discordance_nothing_can_be_detected_and_impossible_gains_are_marked():
    table = mcnemar_power(n=35, accuracy=0.63, discordance=0.0, deltas=[0.0, 0.1],
                          n_sim=500, seed=0)
    assert table.loc[table["delta"] == 0.0, "power"].iloc[0] == 0.0
    assert not table.loc[table["delta"] == 0.1, "feasible"].iloc[0]  # a gain larger than the
    assert np.isnan(table.loc[table["delta"] == 0.1, "power"].iloc[0])  # discordance is impossible


def test_the_smallest_detectable_gain_is_the_first_delta_with_enough_power():
    table = pd.DataFrame(
        {"delta": [0.05, 0.10, 0.15, 0.20], "power": [0.1, 0.5, 0.82, 0.95],
         "feasible": [True] * 4}
    )
    assert smallest_detectable_gain(table, power=0.8) == 0.15
    assert smallest_detectable_gain(table.assign(power=[0.1, 0.2, 0.3, 0.4]), power=0.8) is None
