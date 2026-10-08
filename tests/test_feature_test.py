"""Tests for the runner of the pre-registered feature test (modeling/feature_test.py).

The rules are checked on synthetic data with planted effects: a column that carries the answer must
pass and a column of noise must not; the percentiles are the plan's; fragile and robust are told
apart; the combined model follows its rule; identical seeds give identical results.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.candidate_families import KEYS, task_a_family_columns
from oa_market_intelligence.features.segment_task import build_task_a_rows
from oa_market_intelligence.modeling.feature_test import (
    ALPHA,
    LOWER_A,
    LOWER_B,
    SCENARIOS,
    TASK_A_FAMILIES,
    TASK_B_FAMILIES,
    logistic_predictor,
    run_task_a,
    run_task_b,
    verdict,
)

# ---------- the plan's numbers ----------


def test_the_percentiles_are_the_plans_bonferroni_quotients():
    assert ALPHA == 0.05
    assert len(TASK_A_FAMILIES) == 6 and LOWER_A == pytest.approx(100 * 0.05 / 6)
    assert len(TASK_B_FAMILIES) == 2 and LOWER_B == pytest.approx(2.5)
    assert SCENARIOS["covid_removed"] == (202003, 202005)
    assert SCENARIOS["dip_2024_removed"] == (202403, 202407)


def test_the_plan_document_states_the_same_percentiles():
    from oa_market_intelligence.external.common import REFERENCE_DIR

    plan = (REFERENCE_DIR.parent.parent / "docs" / "feature_engineering_plan.md").read_text("utf-8")
    assert "0.83th percentile in Task A" in plan and "2.5th percentile in Task B" in plan
    for family in (*TASK_A_FAMILIES, "FB1"):
        assert f"**{family}" in plan


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
def test_a_family_is_robust_only_if_it_passes_every_scenario(passes, expected):
    assert verdict(passes) == expected


def test_task_b_has_a_main_and_a_dip_scenario_only_so_two_passes_make_it_robust():
    assert verdict({"main": True, "dip_2024_removed": True}) == "robust"
    assert verdict({"main": True, "dip_2024_removed": False}) == "fragile"


# ---------- the predictor ----------


def test_fa1_adds_the_shrinkage_k_to_the_tuned_setting_and_the_others_do_not():
    fa1 = logistic_predictor(("FA1",))
    assert len(fa1.configs) == 9 and {c["k"] for c in fa1.configs} == {5, 20, 80}
    plain = logistic_predictor(("FA2",))
    assert len(plain.configs) == 3 and all("k" not in c for c in plain.configs)


# ---------- Task A on synthetic data ----------


def _synthetic(months=36, seed=0):
    rng = np.random.default_rng(seed)
    month_ids = [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(months)]
    rows = []
    for month in month_ids:
        for specialty in ("S1", "S2", "S3"):
            for age in ("40 TO 59", "65 TO 74"):
                for gender in ("FEMALE", "MALE"):
                    t = int(rng.integers(60, 400))
                    rows.append((month, specialty, age, gender, int(rng.binomial(t, 0.03)), t))
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


def _rows_with_families():
    seg = _synthetic()
    seg["segment_visit_share"] = seg["branded_injectable_visits"] / seg["total_category_visits"]
    rows = build_task_a_rows(seg)
    columns = task_a_family_columns(seg, families=("FA1", "FA2", "FA3", "FA4"))
    return rows.merge(columns, on=KEYS, how="left")


def _run(rows, families, **kwargs):
    return run_task_a(rows, families, n_boot=200, seed=0, **kwargs)


def test_a_column_that_carries_the_answer_passes_and_a_noise_column_does_not():
    rows = _rows_with_families()
    rows["fa2_log_cum_branded"] = rows["y_share"].to_numpy(float)  # the answer, planted
    result = _run(rows, ("FA2", "FA3"))
    assert result["verdicts"]["FA2"] == "robust"
    assert result["verdicts"]["FA3"] in ("no_pass", "fragile")
    main = result["scenarios"]["main"]["families"]["FA2"]
    assert main["low"] > 0 and main["estimate"] > 0 and main["months_better"] > 0
    assert set(result["scenarios"]) == set(SCENARIOS)


def test_a_percentile_comes_from_the_number_of_families_tested():
    rows = _rows_with_families()
    result = _run(rows, ("FA2", "FA3", "FA4"), scenarios={"main": None})
    assert result["lower_percentile"] == pytest.approx(100 * 0.05 / 3)


def test_every_family_reports_its_effect_size_even_when_it_fails():
    rows = _rows_with_families()
    result = _run(rows, ("FA3",), scenarios={"main": None})
    fa3 = result["scenarios"]["main"]["families"]["FA3"]
    assert {
        "estimate",
        "low",
        "high",
        "share_positive",
        "log_loss",
        "months_better",
        "passes",
    } <= set(fa3)
    assert result["scenarios"]["main"]["n_test_months"] == rows["month_id"].nunique() - 24


def test_the_combined_model_is_run_only_when_two_families_are_robust():
    rows = _rows_with_families()
    rows["fa2_log_cum_branded"] = rows["y_share"].to_numpy(float)
    one = _run(rows, ("FA2", "FA3"))
    assert one["combined"] is None  # only one robust family
    rows["fa4_seasonal_gap"] = rows["y_share"].to_numpy(float) * 0.9  # a second planted column
    two = _run(rows, ("FA2", "FA4"))
    assert two["verdicts"] == {"FA2": "robust", "FA4": "robust"}
    combined = two["combined"]
    assert set(combined["families"]) == {"FA2", "FA4"} and "adopted" in combined


def test_identical_seeds_give_identical_results():
    rows = _rows_with_families()
    first = _run(rows, ("FA3",), scenarios={"main": None})
    second = _run(rows, ("FA3",), scenarios={"main": None})
    assert first == second


# ---------- Task B on synthetic data ----------


def _series(n=60, seed=1):
    rng = np.random.default_rng(seed)
    values = 2.5 + np.cumsum(rng.normal(scale=0.15, size=n))
    months = [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(n)]
    return pd.Series(values, index=months)


def test_task_b_passes_a_planted_column_and_not_noise_and_reports_both_comparisons():
    series = _series()
    rng = np.random.default_rng(5)
    extras = {
        "PLANT": pd.DataFrame(
            {"month_id": series.index, "v": series.to_numpy()}
        ),  # the month's own value
        "NOISE": pd.DataFrame({"month_id": series.index, "v": rng.normal(size=len(series))}),
    }
    result = run_task_b(series, extras, n_boot=200)
    assert result["verdicts"]["PLANT"] == "robust" or result["verdicts"]["PLANT"] == "fragile"
    main = result["scenarios"]["main"]["families"]
    assert main["PLANT"]["passes"] and not main["NOISE"]["passes"]
    assert {"against_last_month", "against_plain_ridge"} <= set(main["PLANT"])
    assert result["lower_percentile"] == LOWER_B
    assert result["scenarios"]["main"]["n_test_months"] == 60 - 24


def test_task_b_dip_scenario_scores_without_the_dip_months():
    series = _series(n=60)  # 2019-08 to 2024-07: the dip months are the last five
    extras = {"NOISE": pd.DataFrame({"month_id": series.index, "v": np.zeros(len(series))})}
    result = run_task_b(series, extras, n_boot=100)
    assert (
        result["scenarios"]["dip_2024_removed"]["n_test_months"]
        == result["scenarios"]["main"]["n_test_months"] - 5
    )
