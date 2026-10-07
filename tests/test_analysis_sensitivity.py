"""Tests for the Step 5 sensitivity checks (src/.../analysis/sensitivity.py).

The verdict rules were written into the plan before the real run; these tests pin each rule to
small numbers, and check the exclusions and the orchestration on simulated data.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.analysis.sensitivity import (
    EXCLUSIONS,
    apply_exclusion,
    rule_a1,
    rule_a2,
    rule_a3,
    rule_d1,
    rule_t1,
    run_sensitivity,
    share_series,
    side_of_overall,
    verdict,
)


def _frame(rows):
    return pd.DataFrame(rows, columns=["month_id", "specialty", "zilretta", "category"])


def test_the_three_exclusions_are_the_ones_in_the_plan():
    assert set(EXCLUSIONS) == {"PEDIATRICS", "COVID months", "2024 dip months"}
    assert EXCLUSIONS["COVID months"] == ("months", (202003, 202005))
    assert EXCLUSIONS["2024 dip months"] == ("months", (202403, 202407))
    assert EXCLUSIONS["PEDIATRICS"] == ("specialty", "PEDIATRICS")


def test_a_specialty_exclusion_removes_only_that_specialty():
    df = _frame([(202001, "A", 1, 10), (202001, "PEDIATRICS", 5, 10), (202002, "A", 2, 10)])
    out = apply_exclusion(df, "PEDIATRICS")
    assert out["specialty"].tolist() == ["A", "A"]
    assert apply_exclusion(df, "PEDIATRICS", specialty_column="specialty").shape[0] == 2


def test_a_month_exclusion_removes_only_months_inside_the_window():
    df = _frame([(m, "A", 1, 10) for m in (202002, 202003, 202004, 202005, 202006)])
    out = apply_exclusion(df, "COVID months")
    assert out["month_id"].tolist() == [202002, 202006]


def test_the_baseline_exclusion_changes_nothing():
    df = _frame([(202001, "A", 1, 10), (202002, "B", 2, 10)])
    pd.testing.assert_frame_equal(apply_exclusion(df, "baseline"), df)


def test_the_share_series_interpolates_excluded_months_linearly():
    months = [1, 2, 3, 4, 5]
    df = _frame([(1, "A", 10, 100), (2, "A", 20, 100), (5, "A", 50, 100)])  # months 3, 4 missing
    series = share_series(df, months)
    assert series["month_id"].tolist() == months
    assert series["share"].tolist() == pytest.approx([0.10, 0.20, 0.30, 0.40, 0.50])
    assert series["interpolated"].tolist() == [False, False, True, True, False]


def test_the_share_series_sums_specialties_within_a_month():
    df = _frame([(1, "A", 10, 100), (1, "B", 30, 100), (2, "A", 20, 100), (2, "B", 20, 100)])
    series = share_series(df, [1, 2])
    assert series["share"].tolist() == pytest.approx([0.20, 0.20])


@pytest.mark.parametrize(
    "low, high, overall, expected",
    [(0.03, 0.04, 0.025, "above"), (0.01, 0.02, 0.025, "below"), (0.02, 0.03, 0.025, "not clear")],
)
def test_the_side_of_the_overall_share_needs_the_whole_interval_on_one_side(
    low, high, overall, expected
):
    assert side_of_overall(low, high, overall) == expected


def test_t1_needs_a_significant_break_inside_the_baseline_interval():
    interval = [(202105, 202207)]
    assert rule_t1(p_first=0.001, break_months=[202203], baseline_intervals=interval)
    assert not rule_t1(p_first=0.20, break_months=[202203], baseline_intervals=interval)
    assert not rule_t1(p_first=0.001, break_months=[202008], baseline_intervals=interval)
    assert not rule_t1(p_first=0.001, break_months=[], baseline_intervals=interval)
    assert rule_t1(0.01, [202008, 202203], interval)  # any break inside counts


def _effects(first, third):
    return {"first_to_last": first, "third_to_last": third}


def test_d1_needs_a_negative_rate_effect_at_least_twice_the_mix_effect():
    good = _effects({"mix": 0.08, "rate": -0.40}, {"mix": 0.08, "rate": -1.20})
    assert rule_d1(good)
    weak = _effects({"mix": 0.08, "rate": -0.40}, {"mix": 0.70, "rate": -1.20})
    assert not rule_d1(weak)  # 1.20 is less than twice 0.70
    wrong_sign = _effects({"mix": 0.01, "rate": 0.40}, {"mix": 0.08, "rate": -1.20})
    assert not rule_d1(wrong_sign)


def test_a1_needs_both_a_large_share_and_a_small_p_value():
    assert rule_a1(share=0.51, p=1e-12)
    assert not rule_a1(share=0.20, p=1e-12)
    assert not rule_a1(share=0.51, p=0.02)


def test_a2_checks_only_specialties_that_were_clear_at_baseline_and_skips_the_rare_group():
    base = pd.DataFrame(
        {"estimate": [0.05, 0.026, 0.01, 0.02], "low": [0.04, 0.02, 0.008, 0.015],
         "high": [0.06, 0.03, 0.012, 0.03]},
        index=["HIGH", "MIDDLE", "LOW", "RARE (grouped)"],
    )
    same = base.copy()
    assert rule_a2(base, same, 0.025, 0.025) == {"HIGH": True, "LOW": True}
    moved = base.copy()
    moved.loc["HIGH", "low"] = 0.02  # interval now reaches the overall share
    assert rule_a2(base, moved, 0.025, 0.025)["HIGH"] is False
    missing = base.drop(index="LOW")
    assert rule_a2(base, missing, 0.025, 0.025)["LOW"] is False


def test_a3_needs_a_split_half_correlation_of_at_least_point_six():
    assert rule_a3(0.78) and rule_a3(0.6)
    assert not rule_a3(0.59)


def test_a_verdict_is_robust_only_if_every_run_holds():
    assert verdict({"baseline": True, "COVID months": True}) == {"label": "robust", "failed": []}
    out = verdict({"baseline": True, "COVID months": False, "PEDIATRICS": False})
    assert out["label"] == "fragile"
    assert out["failed"] == ["COVID months", "PEDIATRICS"]


def _calendar(n=48):
    """n consecutive months starting Aug 2019 as YYYYMM ids."""
    return [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(n)]


def _simulated(months=48, seed=0):
    rng = np.random.default_rng(seed)
    effects = {"S1": 0.6, "S2": 0.2, "S3": 0.0, "S4": -0.4, "S5": -0.8}
    rows = []
    for m, month_id in enumerate(_calendar(months)):
        for s, e in effects.items():
            for a in ("A1", "A2", "A3"):
                for g in ("F", "M"):
                    t = int(rng.integers(300, 700))
                    p = 1 / (1 + np.exp(-(-3.4 + e + 0.1 * (a == "A1") - 0.01 * m)))
                    rows.append((month_id, s, a, g, int(rng.binomial(t, p)), t))
    return pd.DataFrame(
        rows, columns=["month_id", "specialty", "age_band", "gender", "zilretta", "category"]
    )


def test_the_orchestration_returns_a_verdict_for_every_headline_conclusion():
    result = run_sensitivity(_simulated(), n_null=19, n_boot=8, n_boot_stability=8, seed=0)
    assert set(result["runs"]) == {"baseline", "PEDIATRICS", "COVID months", "2024 dip months"}
    assert set(result["verdicts"]) >= {"T1", "D1", "A1", "A2", "A3"}
    for key in ("T1", "D1", "A1", "A3"):
        assert result["verdicts"][key]["label"] in {"robust", "fragile"}
    # the simulated data has no PEDIATRICS, so that run must equal the baseline exactly
    base = result["runs"]["baseline"]["adoption"]["specialty_share"]
    assert result["runs"]["PEDIATRICS"]["adoption"]["specialty_share"] == pytest.approx(base)
    # the COVID months are inside the simulated calendar, so that run removes three of them
    assert result["runs"]["COVID months"]["n_months"] == 45
    assert result["runs"]["2024 dip months"]["n_months"] == 48  # outside the simulated range
