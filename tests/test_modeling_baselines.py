"""Tests for the baselines (src/.../modeling/baselines.py).

Persistence is the proposal's mandatory bar (PROPOSAL.md §6.1: "last month's direction
repeated"); the always-majority guess is the floor beneath it. The random-by-class-mix
guess shows what chance looks like, and the seasonal rule is the strongest simple
baseline: it uses only the calendar. Any trained model has to beat all of them.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.modeling.baselines import (
    MajorityClassBaseline,
    PersistenceBaseline,
    SeasonalBaseline,
    StratifiedRandomBaseline,
)
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


# ---------- StratifiedRandomBaseline ----------


def test_random_baseline_is_reproducible_for_a_seed_and_varies_across_seeds():
    y = pd.Series(["Flat"] * 12 + ["Up"] * 4 + ["Down"] * 4)
    X = _x(200)
    first = StratifiedRandomBaseline(random_state=1).fit(_x(20), y).predict(X)
    again = StratifiedRandomBaseline(random_state=1).fit(_x(20), y).predict(X)
    other = StratifiedRandomBaseline(random_state=2).fit(_x(20), y).predict(X)
    assert first.tolist() == again.tolist()
    assert first.tolist() != other.tolist()


def test_random_baseline_draws_in_the_training_class_proportions():
    y = pd.Series(["Flat"] * 60 + ["Up"] * 20 + ["Down"] * 20)
    drawn = pd.Series(StratifiedRandomBaseline(random_state=0).fit(_x(100), y).predict(_x(20000)))
    shares = drawn.value_counts(normalize=True)
    assert shares["Flat"] == pytest.approx(0.6, abs=0.02)
    assert shares["Up"] == pytest.approx(0.2, abs=0.02)
    assert shares["Down"] == pytest.approx(0.2, abs=0.02)


def test_random_baseline_never_predicts_a_class_it_has_not_seen():
    y = pd.Series(["Flat"] * 10 + ["Up"] * 5)
    drawn = StratifiedRandomBaseline(random_state=0).fit(_x(15), y).predict(_x(500))
    assert set(drawn) <= {"Flat", "Up"}


# ---------- SeasonalBaseline ----------


def _monthly_frame(labels_by_calendar_month, years=3, start_year=2019):
    """Rows indexed by month_id (YYYYMM), labelled by calendar month, for `years` full years."""
    index, labels = [], []
    for year in range(start_year, start_year + years):
        for month in range(1, 13):
            index.append(year * 100 + month)
            labels.append(labels_by_calendar_month.get(month, "Flat"))
    X = pd.DataFrame({"f": np.zeros(len(index))}, index=pd.Index(index, name="month_id"))
    return X, pd.Series(labels, index=X.index)


def test_seasonal_baseline_predicts_what_each_calendar_month_usually_did():
    X, y = _monthly_frame({12: "Up", 1: "Down"})
    model = SeasonalBaseline().fit(X, y)
    future = pd.DataFrame(
        {"f": [0.0, 0.0, 0.0]}, index=pd.Index([202212, 202301, 202306], name="month_id")
    )
    assert model.predict(future).tolist() == ["Up", "Down", "Flat"]


def test_seasonal_baseline_uses_the_most_common_label_for_that_month():
    X, y = _monthly_frame({12: "Up"}, years=3)
    y = y.copy()
    y.loc[201912] = "Down"  # one dissenting December out of three
    model = SeasonalBaseline().fit(X, y)
    future = pd.DataFrame({"f": [0.0]}, index=pd.Index([202212], name="month_id"))
    assert model.predict(future).tolist() == ["Up"]


def test_seasonal_baseline_falls_back_to_the_overall_majority_for_an_unseen_month():
    X, y = _monthly_frame({}, years=1)
    X, y = X.iloc[:6], y.iloc[:6]  # only January to June seen
    model = SeasonalBaseline().fit(X, y)
    future = pd.DataFrame({"f": [0.0]}, index=pd.Index([202012], name="month_id"))
    assert model.predict(future).tolist() == ["Flat"]
