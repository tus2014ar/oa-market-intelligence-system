"""Tests for the Q4 monitoring tools (src/.../modeling/monitoring.py).

Two monitors: the direction classifier's rolling accuracy against a data-based review
threshold, and an alarm when a forecast's interval is missed in too many recent months. Every
rule has a worked example, and the simulated scenarios behave as designed: a stable series
rarely alarms and a clearly degraded one does.
"""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from oa_market_intelligence.modeling.forecast import naive_forecaster, walk_forward_forecast
from oa_market_intelligence.modeling.monitoring import (
    RULES,
    choose_rule,
    detection_experiment,
    empirical_false_alarm,
    false_alarm_probability,
    hit_series,
    interval_misses,
    perturb_drift,
    perturb_volatility,
    random_guess_line,
    rolling_accuracy,
    stable_threshold,
    window_alarm,
    write_backtest_predictions,
)
from tiny_gold import build_tiny_gold


def _series(values, start=201908):
    months, year, month = [], start // 100, start % 100
    for _ in values:
        months.append(year * 100 + month)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return pd.Series(np.asarray(values, dtype=float), index=months)


def test_a_hit_is_1_when_the_predicted_direction_matches_the_actual_one():
    hits = hit_series(["Up", "Flat", "Down", "Flat"], ["Up", "Down", "Down", "Flat"])
    assert hits.tolist() == [1, 0, 1, 1]


def test_rolling_accuracy_is_the_hit_rate_over_each_window_of_months():
    assert rolling_accuracy(np.array([1, 1, 0, 0, 1]), 3) == pytest.approx([2 / 3, 1 / 3, 1 / 3])
    assert len(rolling_accuracy(np.ones(10), 6)) == 5


def test_the_review_threshold_matches_the_binomial_value_for_independent_hits():
    rng = np.random.default_rng(0)
    hits = (rng.random(600) < 0.7).astype(int)
    out = stable_threshold(hits, window=6, block_length=1, n_boot=400, seed=1)
    # for independent hits at 0.7, the 5th percentile of hits in 6 months is 2 of 6
    assert out["threshold"] == pytest.approx(2 / 6, abs=1 / 6 + 1e-9)
    assert out["threshold"] <= out["match_rate"]
    assert out["match_rate"] == pytest.approx(hits.mean())
    assert out["rate_low"] <= out["match_rate"] <= out["rate_high"]


def test_the_threshold_is_reproducible_and_collapses_for_a_perfect_record():
    hits = np.array([1, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1, 0] * 3)
    a = stable_threshold(hits, window=6, block_length=3, n_boot=200, seed=2)
    b = stable_threshold(hits, window=6, block_length=3, n_boot=200, seed=2)
    assert a == b
    perfect = stable_threshold(np.ones(30, dtype=int), window=6, block_length=3, n_boot=50, seed=0)
    assert perfect["threshold"] == 1.0


def test_the_random_guessing_line_sits_below_the_mean_hit_rate_of_guessing():
    labels = np.array(["Flat"] * 25 + ["Down"] * 6 + ["Up"] * 4)
    line = random_guess_line(labels, window=6, n_runs=500, seed=0)
    chance = (25**2 + 6**2 + 4**2) / 35**2  # probability a class-mix guess is right
    assert 0 <= line["line"] < chance
    assert line["mean_accuracy"] == pytest.approx(chance, abs=0.05)
    assert random_guess_line(labels, window=6, n_runs=500, seed=0) == line


def test_a_miss_is_an_actual_value_outside_the_interval():
    frame = pd.DataFrame(
        {"actual": [1.0, 2.0, 3.0, 4.0], "lo90": [0.5, 0.0, 4.0, 3.5], "hi90": [1.5, 1.5, 6.0, 4.5],
         "lo80": [0.6, 0.2, 4.2, 3.6], "hi80": [1.4, 1.3, 5.8, 4.4]}
    )
    assert interval_misses(frame, "90").tolist() == [False, True, True, False]
    assert interval_misses(frame, "80").tolist() == [False, True, True, False]


def test_the_window_alarm_needs_k_misses_among_the_last_n_months():
    misses = np.array([0, 1, 1, 0, 1, 1, 0], dtype=bool)
    alarm = window_alarm(misses, k=3, n=4)  # windows end at months 3 to 6
    assert alarm.tolist() == [False, True, True, False]
    assert len(window_alarm(misses, k=1, n=7)) == 1


def test_the_two_rules_have_the_false_alarm_rates_the_plan_states():
    assert RULES["R90"] == {"level": "90", "k": 3, "n": 6}
    assert RULES["R80"] == {"level": "80", "k": 4, "n": 6}
    assert false_alarm_probability(0.10, 3, 6) == pytest.approx(0.015850, abs=1e-6)
    assert false_alarm_probability(0.20, 4, 6) == pytest.approx(0.016960, abs=1e-6)


def test_the_bootstrap_false_alarm_rate_is_zero_when_there_are_almost_no_misses():
    misses = np.zeros(48, dtype=bool)
    misses[[10, 30]] = True  # two isolated misses cannot make 3 in 6 months
    out = empirical_false_alarm(misses, k=3, n=6, block_length=3, n_boot=300, seed=0)
    assert out < 0.02


def test_perturbations_leave_the_history_untouched_and_change_the_rest_as_described():
    series = _series(np.full(20, 2.0))
    start = 10
    drifted = perturb_drift(series, start, 0.1)
    assert (drifted.iloc[:start] == 2.0).all()
    assert drifted.iloc[start:].tolist() == pytest.approx(
        [2.0 - 0.1 * (k + 1) for k in range(10)]
    )
    noisy = perturb_volatility(series, start, 0.5, np.random.default_rng(0))
    assert (noisy.iloc[:start] == 2.0).all() and noisy.iloc[start:].std() > 0.2
    same = perturb_volatility(series, start, 0.0, np.random.default_rng(0))
    assert (same == series).all()


def _stable(n=72, seed=0, sd=0.08):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.9 * x[i - 1] + rng.normal(0, sd)
    return _series(2.5 + x)


def test_clear_degradation_is_detected_far_more_often_than_it_alarms_on_a_stable_series():
    series = _stable()
    results = detection_experiment(
        series, scenarios={"noise 1.0": ("volatility", 1.0)}, rules=("R80",), n_seeds=3, seed=0
    )
    row = results[(results["scenario"] == "noise 1.0") & (results["rule"] == "R80")].iloc[0]
    assert row["detection_rate"] > 0.8
    assert row["n_starts"] >= 30
    # on the unperturbed series the rule rarely alarms
    forecast = walk_forward_forecast(series, naive_forecaster, min_train=24)
    alarms = window_alarm(interval_misses(forecast, "80").to_numpy(), k=4, n=6)
    assert alarms.mean() < 0.2


def test_choose_rule_prefers_the_better_detector_unless_it_alarms_in_the_real_backtest():
    results = pd.DataFrame(
        {"scenario": ["a", "b", "a", "b"], "rule": ["R90", "R90", "R80", "R80"],
         "detection_rate": [0.4, 0.6, 0.7, 0.8], "median_delay": [3, 3, 2, 2], "n_starts": 37}
    )
    assert choose_rule(results, {"R90": 0, "R80": 0})["rule"] == "R80"
    assert choose_rule(results, {"R90": 0, "R80": 2})["rule"] == "R90"
    tied = results.assign(detection_rate=[0.5, 0.5, 0.5, 0.5])
    assert choose_rule(tied, {"R90": 0, "R80": 0})["rule"] == "R90"


def test_backtest_predictions_are_written_to_the_previous_months_gold_row():
    engine = create_engine("sqlite:///:memory:")
    build_tiny_gold(engine)
    frame = pd.DataFrame(
        {"month_id": [201909], "predicted": ["Flat"], "actual": ["Up"], "probability": [np.nan]}
    )
    write_backtest_predictions(engine, frame, "backtest walk-forward: seasonal")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT month_id, predicted_direction, prediction_probability, actual_direction, "
                 "model_version FROM gold_visit_share_monthly ORDER BY month_id")
        ).fetchall()
    assert rows[0] == (201908, "Flat", None, "Up", "backtest walk-forward: seasonal")
    assert rows[1] == (201909, None, None, None, None)  # the last row has nothing to compare yet


def test_an_unknown_direction_label_is_rejected_by_the_gold_constraint():
    engine = create_engine("sqlite:///:memory:")
    build_tiny_gold(engine)
    bad = pd.DataFrame(
        {"month_id": [201909], "predicted": ["Sideways"], "actual": ["Up"], "probability": [0.5]}
    )
    with pytest.raises(IntegrityError):
        write_backtest_predictions(engine, bad, "backtest")
