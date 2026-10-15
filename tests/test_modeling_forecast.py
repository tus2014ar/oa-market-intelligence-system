"""Tests for the Task B share forecast (src/.../modeling/forecast.py).

One value per month, so the guarantees are about time: a forecast for month t sees only the
months before it, intervals come from a stated method with a hand-checkable answer, scores
have worked values, and the block bootstrap keeps neighbouring errors together.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.modeling.forecast import (
    RidgeForecaster,
    block_bootstrap_mae,
    coverage_gate,
    empirical_interval,
    ets_forecaster,
    forecast_scores,
    improvement_from_draws,
    load_share_series,
    naive_forecaster,
    run_task_b,
    seasonal_naive_forecaster,
    serve_with_tie_break_b,
    walk_forward_forecast,
)


def _months(n, start=201908):
    out, year, month = [], start // 100, start % 100
    for _ in range(n):
        out.append(year * 100 + month)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _series(values):
    return pd.Series(np.asarray(values, dtype=float), index=_months(len(values)))


def test_the_empirical_interval_adds_percentiles_of_past_errors_to_the_forecast():
    out = empirical_interval(7.0, np.array([1.0, 2.0, 3.0]))
    # linear percentiles of [1, 2, 3]: P10 = 1.2, P90 = 2.8, P5 = 1.1, P95 = 2.9
    assert out["lo80"] == pytest.approx(8.2) and out["hi80"] == pytest.approx(9.8)
    assert out["lo90"] == pytest.approx(8.1) and out["hi90"] == pytest.approx(9.9)
    assert out["point"] == 7.0


def test_the_last_month_forecast_and_its_interval_follow_the_one_step_errors():
    history = _series([1, 2, 4, 7])
    out = naive_forecaster(history, 201912)
    assert out["point"] == 7.0  # last value
    # one-step errors are [1, 2, 3], as in the interval example above
    assert out["lo80"] == pytest.approx(8.2) and out["hi90"] == pytest.approx(9.9)
    assert out["fallback"] is False


def test_the_same_month_last_year_forecast_uses_the_value_twelve_months_back():
    history = _series(np.arange(14.0))  # 14 values; the target is index 14
    out = seasonal_naive_forecaster(history, 202010)
    assert out["point"] == 2.0  # index 14 - 12
    # every year-on-year error is 12, so the interval collapses on point + 12
    assert out["lo80"] == pytest.approx(14.0) and out["hi90"] == pytest.approx(14.0)


def test_walk_forward_forecasts_one_month_at_a_time_from_earlier_data_only():
    values = _series(np.arange(40.0))
    seen = []

    def spy(history, target):
        seen.append((len(history), history.index[-1], target))
        return naive_forecaster(history, target)

    out = walk_forward_forecast(values, spy, min_train=24)
    assert len(out) == 16 and out["month_id"].tolist() == list(values.index[24:])
    assert [n for n, _, _ in seen] == list(range(24, 40))
    assert all(last < target for _, last, target in seen)
    assert out["actual"].tolist() == list(values.iloc[24:])
    assert not out["fallback"].any()


def test_a_forecaster_that_fails_falls_back_to_last_month_and_is_counted():
    values = _series(np.arange(30.0))

    def flaky(history, target):
        if len(history) % 2:
            raise ValueError("did not converge")
        return naive_forecaster(history, target)

    out = walk_forward_forecast(values, flaky, min_train=24)
    assert out["fallback"].sum() == 3  # history lengths 25, 27, 29
    assert out.loc[out["fallback"], "point"].tolist() == [24.0, 26.0, 28.0]  # last month


def _noise(seed, n, sd):
    return np.random.default_rng(seed).normal(0, sd, n)


def test_damped_ets_tracks_a_trend_and_gives_an_interval_around_it():
    n = 40
    values = _series(1.0 + 0.05 * np.arange(n + 1) + _noise(0, n + 1, 0.02))
    out = ets_forecaster(seasonal=False)(values.iloc[:n], values.index[n])
    assert out["point"] == pytest.approx(values.iloc[n], abs=0.15)
    assert out["lo90"] < out["lo80"] < out["point"] < out["hi80"] < out["hi90"]
    assert out["fallback"] is False


def test_seasonal_ets_beats_last_month_on_a_strongly_seasonal_series():
    n = 48
    t = np.arange(n + 1)
    values = _series(2.0 + np.sin(2 * np.pi * t / 12) + _noise(1, n + 1, 0.02))
    seasonal = ets_forecaster(seasonal=True)(values.iloc[:n], values.index[n])
    naive = naive_forecaster(values.iloc[:n], values.index[n])
    assert abs(seasonal["point"] - values.iloc[n]) < abs(naive["point"] - values.iloc[n])


def test_ridge_beats_last_month_on_a_mean_reverting_series_and_retunes_every_six_months():
    rng = np.random.default_rng(2)
    n, level, phi = 72, 2.5, 0.2
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, 0.2)
    values = _series(level + x)
    ridge = RidgeForecaster()
    ridge_out = walk_forward_forecast(values, ridge, min_train=24)
    naive_out = walk_forward_forecast(values, naive_forecaster, min_train=24)
    ridge_mae = (ridge_out["actual"] - ridge_out["point"]).abs().mean()
    naive_mae = (naive_out["actual"] - naive_out["point"]).abs().mean()
    assert ridge_mae < naive_mae
    assert [entry["retuned"] for entry in ridge.log][:7] == [True] + [False] * 5 + [True]
    assert all(entry["alpha"] in (0.1, 1.0, 10.0) for entry in ridge.log)


def _forecast_frame():
    return pd.DataFrame(
        {
            "month_id": [1, 2, 3, 4],
            "actual": [1.0, 2.0, 3.0, 4.0],
            "point": [1.0, 1.0, 5.0, 4.0],
            "lo80": [0.6, 0.2, 4.2, 3.6], "hi80": [1.4, 1.3, 5.8, 4.4],
            "lo90": [0.5, 0.0, 4.0, 3.5], "hi90": [1.5, 1.5, 6.0, 4.5],
            "scale": [1.0, 1.0, 2.0, 2.0],
            "fallback": False,
        }
    )


def test_scores_match_hand_computed_values():
    scores = forecast_scores(_forecast_frame())
    assert scores["mae"] == pytest.approx(0.75)  # errors 0, 1, 2, 0
    assert scores["rmse"] == pytest.approx(np.sqrt(5 / 4))
    assert scores["mase"] == pytest.approx(0.5)  # scaled errors 0, 1, 1, 0
    assert scores["cover90"] == pytest.approx(0.5)  # rows 1 and 4 inside their 90% intervals
    assert scores["width90"] == pytest.approx((1.0 + 1.5 + 2.0 + 1.0) / 4)
    # Winkler score at 90%: widths plus 20 x the distance outside (row 2: 0.5, row 3: 1.0)
    assert scores["score90"] == pytest.approx((1.0 + (1.5 + 10.0) + (2.0 + 20.0) + 1.0) / 4)


def test_the_block_bootstrap_is_reproducible_and_pools_identical_errors_to_no_difference():
    errors = np.abs(np.random.default_rng(3).normal(0, 1, 48))
    a = block_bootstrap_mae({"base": errors, "same": errors}, block_length=6, n_boot=200, seed=1)
    b = block_bootstrap_mae({"base": errors, "same": errors}, block_length=6, n_boot=200, seed=1)
    assert (a["base"] == b["base"]).all() and (a["base"] == a["same"]).all()
    summary = improvement_from_draws(errors.mean(), errors.mean(), a["base"], a["same"])
    assert summary["estimate"] == 0 and summary["low"] == 0


def test_one_block_as_long_as_the_series_reproduces_the_series_every_draw():
    errors = np.abs(np.random.default_rng(4).normal(0, 1, 20))
    draws = block_bootstrap_mae({"m": errors}, block_length=20, n_boot=50, seed=0)
    assert draws["m"] == pytest.approx(np.full(50, errors.mean()))


def test_a_model_with_uniformly_smaller_errors_has_an_interval_above_zero():
    rng = np.random.default_rng(5)
    base = np.abs(rng.normal(0, 1, 48)) + 0.5
    better = base - 0.3
    draws = block_bootstrap_mae({"base": base, "better": better}, block_length=6, n_boot=400,
                                seed=0)
    summary = improvement_from_draws(base.mean(), better.mean(), draws["base"], draws["better"])
    assert summary["estimate"] == pytest.approx(0.3)
    assert summary["low"] > 0 and summary["share_positive"] == 1.0


def test_the_tie_break_serves_the_simplest_promoted_model_unless_a_complex_one_is_clearly_better():
    rng = np.random.default_rng(6)
    base = np.abs(rng.normal(0, 1, 48)) + 1.0
    errors = {"ets_damped": base - 0.2, "ridge": base - 0.2 + rng.normal(0, 0.01, 48),
              "ets_seasonal": base - 0.2 + rng.normal(0, 0.01, 48)}
    draws = block_bootstrap_mae(errors, block_length=6, n_boot=300, seed=0)
    pooled_mae = {k: v.mean() for k, v in errors.items()}
    out = serve_with_tie_break_b(list(errors), pooled_mae, draws)
    assert out["serving"] == "ets_damped"  # the others are not clearly better
    clearly = {**errors, "ets_seasonal": base - 0.6}
    draws2 = block_bootstrap_mae(clearly, block_length=6, n_boot=300, seed=0)
    out2 = serve_with_tie_break_b(list(clearly), {k: v.mean() for k, v in clearly.items()}, draws2)
    assert out2["serving"] == "ets_seasonal"
    assert serve_with_tie_break_b([], pooled_mae, draws)["serving"] is None


def test_the_coverage_gate_is_two_binomial_standard_errors_around_nominal():
    assert coverage_gate(0.80, nominal=0.80, n=48)["ok"]
    assert coverage_gate(0.70, nominal=0.80, n=48)["ok"]  # the lower edge is about 0.685
    assert not coverage_gate(0.60, nominal=0.80, n=48)["ok"]
    gate = coverage_gate(0.98, nominal=0.90, n=48)  # the upper edge is about 0.987
    assert gate["ok"] is True and gate["low"] == pytest.approx(0.9 - 2 * np.sqrt(0.09 / 48))


def test_the_share_series_is_loaded_in_percentage_points(tiny_gold_engine):
    series = load_share_series(tiny_gold_engine)
    assert series.index.tolist() == [201908, 201909]
    assert series.tolist() == pytest.approx([10.0, 10.0])  # 0.10 in the tiny database


def test_a_full_task_b_run_returns_scores_summaries_and_a_decision():
    rng = np.random.default_rng(7)
    n, x = 60, np.zeros(60)
    for i in range(1, n):
        x[i] = 0.3 * x[i - 1] + rng.normal(0, 0.2)
    result = run_task_b(_series(2.5 + x), n_boot=50, block_length=6, seed=0)
    assert {"last_month", "same_month_last_year", "ets_damped", "ets_seasonal", "ridge"} <= set(
        result["scores"].index
    )
    assert {"mae", "rmse", "mase", "cover80", "cover90", "width80", "width90"} <= set(
        result["scores"].columns
    )
    assert result["best_baseline"] in {"last_month", "same_month_last_year"}
    assert set(result["summaries"]) == {"ets_damped", "ets_seasonal", "ridge"}
    assert result["decision"]["serving"] in {
        "ets_damped", "ets_seasonal", "ridge", result["best_baseline"]
    }
    assert set(result["fallbacks"]) >= {"ets_damped", "ets_seasonal"}
