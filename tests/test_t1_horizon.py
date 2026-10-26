"""Tests for T1 at horizons 1, 3 and 6 (src/.../modeling/t1_horizon.py): hand-computed baselines,
the walk-forward counts, the ridge's pairing, and the rewrite-the-future check."""

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import make_series, month_ids
from oa_market_intelligence.modeling.t1_horizon import (
    RidgeDirectForecaster,
    check_series,
    ets_forecaster,
    judge_t1,
    naive_forecaster,
    run_t1_horizon,
    seasonal_naive_forecaster,
    walk_forward_forecast_h,
)


def _ramp(n=40):
    return pd.Series(np.arange(float(n)), index=month_ids(n))


def test_naive_is_the_value_at_the_origin_whatever_the_horizon():
    history = _ramp(30)
    for h in (1, 3, 6):
        out = naive_forecaster(history, 999999, h)
        assert out["point"] == 29.0
        # a ramp changes by exactly h over h steps, so every past error is h
        assert out["lo80"] == pytest.approx(29.0 + h)
        assert out["hi90"] == pytest.approx(29.0 + h)


def test_seasonal_naive_is_the_value_twelve_months_before_the_target():
    history = _ramp(30)  # origin index 29
    for h in (1, 3, 6):
        out = seasonal_naive_forecaster(history, 999999, h)
        assert out["point"] == float(29 + h - 12)  # the value at target index minus 12


@pytest.mark.parametrize("horizon,expected", [(1, 48), (3, 46), (6, 43)])
def test_walk_forward_has_the_plans_test_month_counts(horizon, expected):
    series = _ramp(72)
    frame = walk_forward_forecast_h(series, naive_forecaster, horizon)
    assert len(frame) == expected
    # the first target is 24 months of history plus h
    assert frame["month_id"].iloc[0] == series.index[23 + horizon]
    assert (frame["actual"] == frame["month_id"].map(series)).all()


def test_forecasts_do_not_move_when_the_future_is_rewritten():
    series = make_series(60)
    tampered = series.copy()
    tampered.iloc[40:] = tampered.iloc[40:] + 5.0
    for h in (1, 3):
        for forecaster in (RidgeDirectForecaster(), ets_forecaster(seasonal=False),
                           seasonal_naive_forecaster):
            a = walk_forward_forecast_h(series, forecaster, h)
            b = walk_forward_forecast_h(tampered, type(forecaster)() if isinstance(
                forecaster, RidgeDirectForecaster) else forecaster, h)
            same = a["origin_id"] < series.index[40]  # origins that only saw the unchanged months
            assert same.sum() > 5
            assert np.allclose(a.loc[same, "point"], b.loc[same, "point"])


def test_ridge_pairs_the_target_with_the_lags_at_the_origin():
    ridge = RidgeDirectForecaster()
    values = np.arange(20.0)
    month_ids_ = month_ids(20)
    X, y = ridge._table(values, month_ids_, horizon=3, last_target=10)
    # targets 5..10; the first uses the values at its origin (index 2) and the two before it
    assert list(X[0][:3]) == [2.0, 1.0, 0.0]
    assert y[0] == 5.0 and y[-1] == 10.0 and len(y) == 6
    # no target beyond the last observed index at the origin is ever in the table
    assert y.max() <= 10.0


def test_ridge_prediction_row_uses_the_origin_and_the_target_month():
    ridge = RidgeDirectForecaster()
    values = np.arange(30.0)
    row = ridge._row(values, 202401, origin=20)
    assert row[:3] == [20.0, 19.0, 18.0]
    assert row[3] == pytest.approx(19.0)
    assert row[4] == pytest.approx(np.sin(2 * np.pi / 12))  # January, the target month


def test_ets_forecast_is_h_steps_ahead():
    series = make_series(48)
    point1 = ets_forecaster(seasonal=False)(series, 0, 1)["point"]
    point6 = ets_forecaster(seasonal=False)(series, 0, 6)["point"]
    assert np.isfinite(point1) and np.isfinite(point6)
    assert point1 != point6


def test_the_series_must_be_consecutive_months():
    check_series(_ramp(20))
    broken = _ramp(20).drop(_ramp(20).index[5])
    with pytest.raises(ValueError):
        check_series(broken)


def _frame(months, actual, point, width=1.0):
    return pd.DataFrame(
        {"month_id": months, "origin_id": months, "actual": actual, "point": point,
         "lo80": np.asarray(point) - width, "hi80": np.asarray(point) + width,
         "lo90": np.asarray(point) - width, "hi90": np.asarray(point) + width,
         "scale": 1.0, "fallback": False}
    )


def test_a_planted_better_model_is_promoted_and_a_noise_model_is_not():
    rng = np.random.default_rng(1)
    months = month_ids(48)
    actual = 3 + np.cumsum(rng.normal(0, 0.3, 48))
    baseline = actual + rng.normal(0, 0.5, 48)  # a poor baseline
    forecasts = {
        "last_month": _frame(months, actual, baseline),
        "same_month_last_year": _frame(months, actual, baseline + 0.5),
        "ridge": _frame(months, actual, actual + rng.normal(0, 0.05, 48)),  # planted better
        "ets_damped": _frame(months, actual, baseline + rng.normal(0, 0.5, 48)),  # noise
        "ets_seasonal": _frame(months, actual, baseline + rng.normal(0, 0.5, 48)),  # noise
    }
    out = judge_t1(forecasts, 1, n_boot=500)
    assert out["best_baseline"] == "last_month"
    assert out["models"]["ridge"]["promoted"] and out["models"]["ridge"]["estimate"] > 0
    assert not out["models"]["ets_damped"]["promoted"]
    assert out["serving"] == "ridge"
    assert out["lower_percentile"] == pytest.approx(100 * 0.05 / 3)
    secondary = judge_t1(forecasts, 3, n_boot=500)
    assert secondary["lower_percentile"] == pytest.approx(100 * 0.05 / 6)


def test_no_promotion_leaves_the_baseline_serving():
    rng = np.random.default_rng(2)
    months = month_ids(48)
    actual = 3 + np.cumsum(rng.normal(0, 0.3, 48))
    good = actual + rng.normal(0, 0.05, 48)
    forecasts = {name: _frame(months, actual, good + rng.normal(0, 0.3, 48))
                 for name in ("ridge", "ets_damped", "ets_seasonal")}
    forecasts["last_month"] = _frame(months, actual, good)
    forecasts["same_month_last_year"] = _frame(months, actual, good + 1)
    out = judge_t1(forecasts, 1, n_boot=500)
    assert out["promoted"] == [] and out["serving_is_baseline"]
    assert out["serving"] == "last_month"


def test_run_t1_horizon_is_reproducible_and_reports_both_scenarios():
    series = make_series(60)
    a = run_t1_horizon(series, 3, n_boot=100)
    b = run_t1_horizon(series, 3, n_boot=100)
    assert set(a["scenarios"]) == {"main", "dip_2024_removed"}
    main = a["scenarios"]["main"]
    assert main["n_test_months"] == 60 - 23 - 3  # origins from the 24th month on
    assert main == b["scenarios"]["main"]
    assert all(v == 0 for v in main["fallbacks"].values())
