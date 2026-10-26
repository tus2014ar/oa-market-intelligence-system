"""Tests for the batch demonstration (src/.../modeling/demonstration.py): the cut-off is respected,
the six months follow it, and the model type of each step is the plan's."""

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import make_inputs, make_series
from oa_market_intelligence.modeling import segment_models
from oa_market_intelligence.modeling.demonstration import (
    evaluated_horizon,
    national_batch,
    segment_batch,
)
from oa_market_intelligence.modeling.horizons import month_index


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])


def test_each_step_uses_the_nearest_evaluated_horizon():
    assert [evaluated_horizon(k) for k in range(1, 7)] == [1, 1, 3, 3, 6, 6]


def test_national_batch_by_hand_for_the_persistence_baseline():
    series = make_series(72)
    serving = {1: "last_month", 3: "last_month", 6: "last_month"}
    out = national_batch(series, 202212, serving)
    assert list(out["month_id"]) == [202301, 202302, 202303, 202304, 202305, 202306]
    cutoff_value = series.loc[202212]
    assert np.allclose(out["point"], cutoff_value)  # the value at the cut-off, every step
    assert np.allclose(out["actual"], series.loc[out["month_id"]].to_numpy())
    assert (out["lo90"] <= out["point"]).all() and (out["point"] <= out["hi90"]).all()


def test_national_batch_across_a_year_boundary_and_with_a_trained_model():
    series = make_series(72)
    serving = {1: "ridge", 3: "ets_damped", 6: "same_month_last_year"}
    out = national_batch(series, 202312, serving)
    assert list(out["month_id"]) == [202401, 202402, 202403, 202404, 202405, 202406]
    assert list(out["model"]) == ["ridge", "ridge", "ets_damped", "ets_damped",
                                  "same_month_last_year", "same_month_last_year"]
    assert np.isfinite(out[["point", "lo90", "hi90"]].to_numpy()).all()


def test_a_forecast_never_uses_values_after_the_cutoff():
    series = make_series(72)
    tampered = series.copy()
    tampered[tampered.index > 202212] += 10.0
    serving = {1: "ridge", 3: "ets_damped", 6: "last_month"}
    a = national_batch(series, 202212, serving)
    b = national_batch(tampered, 202212, serving)
    assert np.allclose(a["point"], b["point"])  # the forecast cannot see the change
    assert not np.allclose(a["actual"], b["actual"])  # the actuals can


def test_cutoff_must_exist_in_the_series():
    with pytest.raises(ValueError):
        national_batch(make_series(30), 209912, {1: "last_month", 3: "last_month", 6: "last_month"})


def test_segment_batch_summarises_by_specialty_from_the_cutoff_only():
    rows, outcomes, _ = make_inputs(n_months=44)
    cutoff = 202108  # 25 months into the data
    serving = {1: "last_month", 3: "logistic", 6: "market"}
    out = segment_batch(rows, outcomes, cutoff, serving)
    assert set(out["step"]) == {1, 2, 3, 4, 5, 6}
    first = out[out["step"] == 1]
    assert set(first["month_id"]) == {202109}
    assert set(first["model"]) == {"last_month"}
    assert set(out[out["step"] == 3]["model"]) == {"logistic"}
    assert set(out[out["step"] == 6]["model"]) == {"market"}
    assert (out["month_id"].map(month_index) - month_index(cutoff) == out["step"]).all()
    assert ((out["predicted_share"] >= 0) & (out["predicted_share"] <= 1)).all()
    assert ((out["actual_share"] >= 0) & (out["actual_share"] <= 1)).all()
    assert set(out["specialty_name"]) <= {"ORTHOPEDIC SURGERY", "RHEUMATOLOGY",
                                          "FAMILY MEDICINE", "PEDIATRICS"}
    # the persistence prediction of a specialty at step 1 is the visit-weighted last-month share
    assert out.loc[first.index, "visits"].gt(0).all()


def test_segment_batch_does_not_move_when_the_future_is_rewritten():
    rows, outcomes, _ = make_inputs(n_months=44)
    cutoff = 202108
    serving = {1: "logistic", 3: "logistic", 6: "last_month"}
    tampered_rows = rows.copy()
    tampered_outcomes = outcomes.copy()
    # the features of the cut-off origin sit in the row of the next month; later rows are the future
    later_rows = month_index(tampered_rows["month_id"].to_numpy()) > month_index(cutoff) + 1
    for column in ("seg_share_lag1", "seg_share_roll3", "seg_prior_z", "seg_prior_t"):
        tampered_rows.loc[later_rows, column] = tampered_rows.loc[later_rows, column] * 6 + 1
    later_outcomes = tampered_outcomes["month_id"] > cutoff
    tampered_outcomes.loc[later_outcomes, "y_share"] = 0.5
    a = segment_batch(rows, outcomes, cutoff, serving)
    b = segment_batch(tampered_rows, tampered_outcomes, cutoff, serving)
    pd.testing.assert_series_equal(a["predicted_share"], b["predicted_share"])
    assert not np.allclose(a["actual_share"], b["actual_share"])
