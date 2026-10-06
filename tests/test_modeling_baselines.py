"""Tests for the baselines (src/.../modeling/baselines.py).

Persistence is the proposal's mandatory bar (PROPOSAL.md §6.1: "last month's direction
repeated"); the always-majority guess is the floor beneath it. Any trained model has to
beat both before it earns a place.
"""

import numpy as np
import pandas as pd

from oa_market_intelligence.modeling.baselines import MajorityClassBaseline, PersistenceBaseline
from oa_market_intelligence.modeling.evaluation import walk_forward_predict


def _x(n):
    return pd.DataFrame({"f": np.zeros(n)})


def test_majority_predicts_the_most_common_training_label():
    model = MajorityClassBaseline().fit(_x(5), pd.Series(["Flat", "Up", "Flat", "Down", "Flat"]))
    assert model.predict(_x(3)).tolist() == ["Flat", "Flat", "Flat"]


def test_majority_ties_resolve_toward_flat():
    model = MajorityClassBaseline().fit(_x(4), pd.Series(["Up", "Up", "Flat", "Flat"]))
    assert model.predict(_x(1)).tolist() == ["Flat"]
    model = MajorityClassBaseline().fit(_x(4), pd.Series(["Up", "Up", "Down", "Down"]))
    assert model.predict(_x(1)).tolist() == ["Down"]


def test_persistence_repeats_the_last_training_label():
    model = PersistenceBaseline().fit(_x(4), pd.Series(["Up", "Flat", "Down", "Up"]))
    assert model.predict(_x(2)).tolist() == ["Up", "Up"]


def test_in_a_walk_forward_run_persistence_is_last_months_actual_label():
    labels = ["Flat", "Up", "Flat", "Flat", "Down", "Flat", "Up", "Down"] * 5
    frame = pd.DataFrame(
        {"direction_label": labels, "f": np.arange(len(labels), dtype=float)},
        index=pd.Index(range(len(labels)), name="month_id"),
    )
    result = walk_forward_predict(frame, PersistenceBaseline, min_train=24)

    for month_id in result.index:
        assert result.loc[month_id, "y_pred"] == labels[month_id - 1]


def test_in_a_walk_forward_run_majority_is_the_mode_of_the_months_before():
    labels = (["Flat"] * 6 + ["Up"] * 2 + ["Down"]) * 5
    frame = pd.DataFrame(
        {"direction_label": labels, "f": np.arange(len(labels), dtype=float)},
        index=pd.Index(range(len(labels)), name="month_id"),
    )
    result = walk_forward_predict(frame, MajorityClassBaseline, min_train=24)

    for month_id in result.index:
        history = pd.Series(labels[:month_id])
        assert result.loc[month_id, "y_pred"] == history.value_counts().idxmax()
