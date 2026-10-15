"""Tests for the runner of the pre-registered feature test (features/validate.py).

The rules are checked on synthetic data with planted effects: a column that carries the answer must
pass and a column of noise must not; the percentiles are the plan's; robust, fragile and the
flag group are told apart; the t-2 sensitivity uses only older data; identical seeds give
identical results.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.candidate_families import KEYS
from oa_market_intelligence.features.groups import T1_GROUPS, T2_GROUPS, task_a_group_columns
from oa_market_intelligence.features.segment_task import build_task_a_rows
from oa_market_intelligence.features.validate import (
    ALPHA,
    LOWER_T1,
    LOWER_T2,
    NO_LAG,
    SCENARIOS,
    TwoStepRidgeForecaster,
    lag_rows_one_month,
    logistic_predictor,
    naive_two_step,
    run_t1,
    run_t2,
    verdict,
)

# ---------- the plan's numbers ----------


def test_the_percentiles_are_the_plans_bonferroni_quotients():
    assert ALPHA == 0.05
    assert len(T2_GROUPS) == 7 and LOWER_T2 == pytest.approx(100 * 0.05 / 7)
    assert len(T1_GROUPS) == 5 and LOWER_T1 == pytest.approx(1.0)
    assert SCENARIOS["covid_removed"] == (202003, 202005)
    assert SCENARIOS["dip_2024_removed"] == (202403, 202407)


def test_the_plan_document_states_the_same_rules():
    from pathlib import Path

    plan = (Path(__file__).resolve().parents[1] / "docs" / "feature_engineering_plan.md").read_text(
        "utf-8"
    )
    assert "0.71th percentile" in plan and "1.0th percentile" in plan
    for group in T2_GROUPS:
        assert f"**{group} " in plan


# ---------- the verdict ----------


@pytest.mark.parametrize(
    ("passes", "expected"),
    [
        ({"main": True, "covid_removed": True, "dip_2024_removed": True}, "robust"),
        ({"main": True, "covid_removed": False, "dip_2024_removed": True}, "fragile"),
        ({"main": True, "covid_removed": False, "dip_2024_removed": False}, "fragile"),
        ({"main": False, "covid_removed": True, "dip_2024_removed": True}, "no_pass"),
    ],
)
def test_a_group_is_robust_only_if_it_passes_every_scenario(passes, expected):
    assert verdict(passes) == expected


def test_the_flag_group_is_judged_on_the_main_run_only():
    assert verdict({"main": True}, robustness_testable=False) == "pass_main_only"
    assert verdict({"main": False}, robustness_testable=False) == "no_pass"


# ---------- the predictor ----------


def test_g3_adds_the_shrinkage_k_to_the_tuned_setting_and_the_others_do_not():
    g3 = logistic_predictor(("G3",))
    assert len(g3.configs) == 9 and {c["k"] for c in g3.configs} == {5, 20, 80}
    plain = logistic_predictor(("G2",))
    assert len(plain.configs) == 3 and all("k" not in c for c in plain.configs)


# ---------- the t-2 sensitivity ----------


def test_the_one_month_lag_gives_each_row_the_previous_months_features():
    rows = pd.DataFrame(
        {
            "segment": ["a", "a", "a", "b", "b"],
            "month_id": [201901, 201902, 201903, 201902, 201903],
            "g1_share_chg1": [0.1, 0.2, 0.3, 0.7, 0.8],
            "month_sin": [0.5, 0.6, 0.7, 0.6, 0.7],
            "y_share": [0.01, 0.02, 0.03, 0.04, 0.05],
        }
    )
    out = lag_rows_one_month(rows, ["g1_share_chg1"])
    # the first month of a segment has no previous row and is dropped
    assert list(zip(out["segment"], out["month_id"], strict=True)) == [
        ("a", 201902),
        ("a", 201903),
        ("b", 201903),
    ]
    a_feb = out[(out["segment"] == "a") & (out["month_id"] == 201902)].iloc[0]
    assert a_feb["g1_share_chg1"] == pytest.approx(0.1)  # January's value
    assert a_feb["y_share"] == pytest.approx(0.02)  # the target stays February's
    assert a_feb["month_sin"] == pytest.approx(0.6)  # untouched
    b_mar = out[(out["segment"] == "b") & (out["month_id"] == 201903)].iloc[0]
    assert b_mar["g1_share_chg1"] == pytest.approx(0.7)


def test_calendar_and_static_columns_are_not_lagged():
    assert {"month_sin", "month_cos", "g2_is_dec", "g7_covid", "g7_dip", "g7_pediatrics"} <= NO_LAG


def test_the_two_step_forecasters_use_lags_two_to_four_only():
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 100.0])
    model = TwoStepRidgeForecaster()
    row = model._row(values, 202003, 5)  # target position 5: lags are positions 3, 2, 1
    assert row[:3] == [4.0, 3.0, 2.0]
    assert row[3] == pytest.approx(3.0)
    assert 100.0 not in row  # a later value never enters
    history = pd.Series([1.0, 2.0, 4.0, 7.0, 11.0], index=[1, 2, 3, 4, 5])
    out = naive_two_step(history, 6)
    assert out["point"] == 7.0  # two months before the target


# ---------- T2 on synthetic data ----------


def _synthetic(months=36, seed=0):
    rng = np.random.default_rng(seed)
    ids, (year, month) = [], (2019, 8)
    for _ in range(months):
        ids.append(year * 100 + month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    rows = []
    for m in ids:
        for specialty in ("S1", "S2", "S3"):
            for age in ("40 TO 59", "65 TO 74"):
                for gender in ("FEMALE", "MALE"):
                    t = int(rng.integers(60, 400))
                    rows.append((m, specialty, age, gender, int(rng.binomial(t, 0.03)), t))
    return pd.DataFrame(
        rows,
        columns=[
            "month_id",
            "specialty_name",
            "age_band",
            "gender",
            "branded_injectable_visits",
            "total_category_visits",
        ],
    )


def _rows(groups=("G1", "G2")):
    seg = _synthetic()
    seg["segment_visit_share"] = seg["branded_injectable_visits"] / seg["total_category_visits"]
    base = build_task_a_rows(seg)
    return base.merge(task_a_group_columns(seg, groups=groups), on=KEYS, how="left")


def _run(rows, groups, **kwargs):
    return run_t2(rows, groups, n_boot=200, seed=0, **kwargs)


def test_a_column_that_carries_the_answer_passes_and_noise_does_not():
    rows = _rows(("G1", "G2"))
    rows["g1_share_chg1"] = rows["y_share"].to_numpy(float)  # the answer, planted
    result = _run(rows, ("G1", "G2"))
    assert result["verdicts"]["G1"] == "robust"
    assert result["verdicts"]["G2"] in ("no_pass", "fragile")
    main = result["scenarios"]["main"]["groups"]["G1"]
    assert main["low"] > 0 and main["estimate"] > 0 and main["months_better"] > 0
    assert set(result["scenarios"]) == set(SCENARIOS)
    assert result["lower_percentile"] == pytest.approx(100 * 0.05 / 2)


def test_the_flag_group_is_reported_on_the_main_run_and_never_called_robust():
    rows = _rows(("G1", "G2")).assign(g7_covid=0.0, g7_dip=0.0, g7_pediatrics=0.0)
    rows["g7_dip"] = rows["y_share"].to_numpy(float)  # planted
    result = _run(rows, ("G7",))
    assert result["verdicts"]["G7"] == "pass_main_only"


def test_identical_seeds_give_identical_results():
    rows = _rows(("G2",))
    first = _run(rows, ("G2",), scenarios={"main": None})
    second = _run(rows, ("G2",), scenarios={"main": None})
    assert first == second


def test_the_sensitivity_run_scores_the_lagged_rows():
    rows = _rows(("G1",))
    columns = [c for c in rows.columns if c.startswith("g1_")]
    lagged = lag_rows_one_month(rows, columns)
    assert len(lagged) < len(rows)
    result = _run(lagged, ("G1",), scenarios={"main": None})
    assert result["scenarios"]["main"]["n_rows"] == len(lagged)


# ---------- T1 on synthetic data ----------


def _series(n=72, seed=1):
    rng = np.random.default_rng(seed)
    values = 2.5 + np.cumsum(rng.normal(scale=0.15, size=n))
    ids, (year, month) = [], (2019, 8)
    for _ in range(n):
        ids.append(year * 100 + month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return pd.Series(values, index=ids)


def test_t1_passes_a_planted_column_and_not_noise_and_reports_both_comparisons():
    series = _series()
    rng = np.random.default_rng(5)
    extras = {
        "G1": pd.DataFrame({"month_id": series.index, "v": series.to_numpy()}),  # the answer
        "G2": pd.DataFrame({"month_id": series.index, "v": rng.normal(size=len(series))}),
    }
    result = run_t1(series, extras, n_boot=200)
    main = result["scenarios"]["main"]["groups"]
    assert main["G1"]["passes"] and not main["G2"]["passes"]
    assert {"against_last_month", "against_plain_ridge"} <= set(main["G1"])
    assert result["lower_percentile"] == pytest.approx(LOWER_T1)
    assert result["scenarios"]["main"]["n_test_months"] == 72 - 24


def test_t1_dip_scenario_scores_without_the_dip_months():
    series = _series()
    extras = {"G2": pd.DataFrame({"month_id": series.index, "v": np.zeros(len(series))})}
    result = run_t1(series, extras, n_boot=100)
    removed = [m for m in series.index[24:] if 202403 <= m <= 202407]
    assert result["scenarios"]["dip_2024_removed"]["n_test_months"] == result["scenarios"]["main"][
        "n_test_months"
    ] - len(removed)


def test_t1_sensitivity_run_does_not_see_the_planted_same_month_value():
    series = _series()
    extras = {"G1": pd.DataFrame({"month_id": series.index, "v": series.to_numpy()})}
    normal = run_t1(series, extras, n_boot=200)
    lagged = run_t1(series, extras, n_boot=200, lag_two=True)
    # in the main run the planted column is the answer; with the one-month lag it is last
    # month's value, so the gain over last month's two-step baseline is far smaller
    gain = lambda r: r["scenarios"]["main"]["groups"]["G1"]["against_last_month"]["estimate"]  # noqa: E731
    assert gain(normal) > gain(lagged)
