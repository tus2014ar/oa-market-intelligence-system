"""Tests for the gap diagnosis (protocol section 9): D1 population, D2 setting, D3 value per
visit, D4 concentration, the overall row and the sensitivity grid.

Synthetic inputs with planted answers; thresholds are pinned to the protocol text. Nothing here
touches the real data.
"""

import pandas as pd
import pytest

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.gap import (
    annual_sales,
    change,
    combined_value_change,
    d1_result,
    d2_result,
    d3_result,
    d4_result,
    grid_verdicts,
    hospital_share,
    iqvia_metrics,
    medicare_metrics,
    overall_gap,
    verdicts_flip,
)
from oa_market_intelligence.external.common import REFERENCE_DIR

PROTOCOL = (REFERENCE_DIR.parent.parent / "docs" / "external_data_protocol.md").read_text(
    encoding="utf-8"
)


# ---------- settings are the protocol's ----------


def test_the_gap_settings_match_the_protocol_text():
    assert (config.GAP_BASE_YEAR, config.GAP_LAST_YEAR) == (2021, 2024)
    assert config.GAP_ALT_BASE_YEAR == 2022
    for phrase in (
        "Calendar years 2021 and 2024",
        "within 15 percentage points",
        "at least 15 points lower",
        "rose by 10 points or more",
        "rose by 25% or more",
        "rose by 10% or more",
        "at least 50% of the net fall",
        "10 / **15** / 20 points",
        "5 / **10** / 15 points",
        "15% / **25%** / 35%",
        "5% / **10%** / 15%",
        "40% / **50%** / 60%",
        "base year 2022",
    ):
        assert phrase in PROTOCOL, phrase
    assert config.D1_WINDOW == 0.15 and config.D2_SHIFT == 0.10 and config.D4_TOP2 == 0.50
    assert (config.D3_SALES, config.D3_MEDICARE) == (0.25, 0.10)
    assert config.D1_GRID[1] == config.D1_WINDOW and config.D2_GRID[1] == config.D2_SHIFT
    assert config.D3_GRID[1] == (config.D3_SALES, config.D3_MEDICARE)
    assert config.D4_GRID[1] == config.D4_TOP2


def test_change_is_last_over_base_minus_one():
    assert change(100, 60) == pytest.approx(-0.40)
    assert change(50, 75) == pytest.approx(0.50)
    with pytest.raises(ZeroDivisionError):
        change(0, 5)


# ---------- D1 population ----------


def test_d1_is_supported_when_iqvia_65_plus_mirrors_medicare_and_the_fall_sits_under_65():
    out = d1_result(iqvia_65=(100, 60), iqvia_u65=(100, 30), medicare=(10, 6.5))
    assert out["verdict"] == "supported" and out["part_a"] and out["part_b"]
    assert out["gap"] == pytest.approx(-0.05)


def test_d1_fails_part_a_when_iqvia_65_plus_falls_much_more_than_medicare():
    out = d1_result(iqvia_65=(100, 50), iqvia_u65=(100, 20), medicare=(10, 8.3))
    assert out["verdict"] == "not_supported" and not out["part_a"] and out["part_b"]


def test_d1_fails_part_b_when_the_fall_is_not_concentrated_under_65():
    out = d1_result(iqvia_65=(100, 60), iqvia_u65=(100, 55), medicare=(10, 6.5))
    assert out["verdict"] == "not_supported" and out["part_a"] and not out["part_b"]


def test_d1_boundaries_count_exactly_at_the_window():
    # 65+ -30% against Medicare -15% is exactly 15 points; under-65 -45% is exactly 15 lower
    out = d1_result(iqvia_65=(100, 70), iqvia_u65=(100, 55), medicare=(100, 85))
    assert out["part_a"] and out["part_b"] and out["verdict"] == "supported"


def test_d1_window_can_be_changed_for_the_sensitivity_grid():
    # 65+ is 12 points from Medicare (part a needs a window of 12 or more); the under-65 fall is
    # 30 points below 65+ (part b holds for any window up to 30)
    args = {"iqvia_65": (100, 60), "iqvia_u65": (100, 30), "medicare": (10, 7.2)}
    assert d1_result(**args, window=0.10)["verdict"] == "not_supported"
    assert d1_result(**args, window=0.15)["verdict"] == "supported"
    assert d1_result(**args, window=0.20)["verdict"] == "supported"


# ---------- D2 setting ----------


def test_d2_is_supported_when_the_facility_share_rises_by_ten_points():
    out = d2_result(facility=(20, 40), office=(80, 60))
    assert out["share_base"] == pytest.approx(0.20) and out["share_last"] == pytest.approx(0.40)
    assert out["shift"] == pytest.approx(0.20) and out["verdict"] == "supported"


def test_d2_counts_exactly_ten_points_and_rejects_less():
    assert d2_result(facility=(10, 20), office=(90, 80))["verdict"] == "supported"
    assert d2_result(facility=(10, 19), office=(90, 81))["verdict"] == "not_supported"


# ---------- D3 value per visit ----------


def _d3(sales=(100, 125), visits=(10, 10), services=(100, 110), benes=(50, 50), pay=(10, 10), **k):
    return d3_result(sales=sales, visits=visits, services=services, benes=benes, payment=pay, **k)


def test_d3_needs_sales_per_visit_up_and_a_medicare_measure_up():
    out = _d3()
    assert out["sales_per_visit"] == pytest.approx(0.25)
    assert out["services_per_patient"] == pytest.approx(0.10)
    assert out["verdict"] == "supported"


def test_d3_by_payment_per_service_alone_also_counts():
    out = _d3(services=(100, 100), pay=(10, 11))
    assert out["payment_per_service"] == pytest.approx(0.10) and out["verdict"] == "supported"


def test_d3_is_not_supported_without_a_medicare_measure():
    out = _d3(services=(100, 100), pay=(10, 10))
    assert out["verdict"] == "not_supported" and out["unexplained"] is True


def test_d3_is_not_supported_without_the_sales_rise():
    out = _d3(sales=(100, 110))
    assert out["verdict"] == "not_supported" and out["unexplained"] is False


def test_combined_value_change_multiplies_intensity_and_price():
    assert combined_value_change(0.10, 0.10) == pytest.approx(0.21)
    assert combined_value_change(0.152, -0.042) == pytest.approx(1.152 * 0.958 - 1)


# ---------- D4 concentration ----------


def test_d4_is_concentrated_when_the_top_two_groups_hold_half_of_the_fall():
    groups = {"A": (100, 40), "B": (100, 60), "C": (100, 90), "D": (100, 100)}
    out = d4_result(groups)  # falls 60, 40, 10, 0 -> net 110; top two 100
    assert out["top2"] == ["A", "B"] and out["top2_share"] == pytest.approx(100 / 110)
    assert out["verdict"] == "supported"


def test_d4_counts_exactly_half_and_rejects_less():
    even = {k: (100, 80) for k in "ABCD"}  # four equal falls: top two are exactly half
    assert d4_result(even)["verdict"] == "supported"
    spread = {k: (100, 80) for k in "ABCDE"}  # five equal falls: top two are 40%
    assert d4_result(spread)["verdict"] == "not_supported"


def test_d4_handles_a_rising_group_and_a_net_rise_without_dividing_by_zero():
    mixed = {"A": (100, 40), "B": (100, 130), "C": (100, 90)}  # falls 60, -30, 10: net 40
    assert d4_result(mixed)["top2_share"] == pytest.approx((60 + 10) / 40)
    rising = {"A": (100, 120), "B": (100, 130)}
    out = d4_result(rising)
    assert out["verdict"] == "not_supported" and out["top2_share"] is None


# ---------- overall ----------


def test_the_overall_row_is_supported_if_any_of_d1_to_d3_is():
    assert overall_gap(["not_supported", "supported", "not_supported"]) == "supported"
    assert overall_gap(["not_supported"] * 3) == "not_supported"


# ---------- sensitivity grid ----------


def test_the_grid_reports_a_verdict_per_setting_and_flags_a_flip():
    def d3_at(setting):
        sales, medicare = setting
        out = d3_result(
            sales=(100, 120),
            visits=(10, 10),
            services=(100, 108),
            benes=(50, 50),
            payment=(10, 10),
            sales_threshold=sales,
            medicare_threshold=medicare,
        )
        return out["verdict"]

    verdicts = grid_verdicts(d3_at, config.D3_GRID)
    assert [v for _, v in verdicts] == ["supported", "not_supported", "not_supported"]
    assert verdicts_flip(verdicts) is True
    assert verdicts_flip([(0.1, "supported"), (0.2, "supported")]) is False


# ---------- metric builders ----------


def test_medicare_metrics_sum_settings_weight_payment_by_services_and_scale_by_ffs():
    partb = pd.DataFrame(
        {
            "year": [2021, 2021],
            "setting": ["F", "O"],
            "benes": [10, 30],
            "services": [20, 60],
            "avg_payment_amt": [100.0, 200.0],
        }
    )
    geovar = pd.DataFrame({"year": [2021], "benes_original_medicare": [1000]})
    both = medicare_metrics(partb, geovar).set_index("year").loc[2021]
    assert both["benes"] == 40 and both["services"] == 80
    assert both["payment_per_service"] == pytest.approx(175.0)
    assert both["facility_services"] == 20 and both["office_services"] == 60
    assert both["benes_per_1000_ffs"] == pytest.approx(40.0)
    office = medicare_metrics(partb, geovar, settings=("O",)).set_index("year").loc[2021]
    assert office["benes"] == 30 and office["payment_per_service"] == pytest.approx(200.0)


def test_iqvia_metrics_split_ages_at_65_leave_unspecified_out_of_both_and_group_specialties():
    visits = pd.DataFrame(
        {
            "year": [2021] * 5,
            "age_band": ["60 TO 64", "65 TO 74", "85 +", "UNSPECIFIED", "20 TO 39"],
            "specialty_name": [
                "ORTHOPEDIC SURGERY",
                "ORTHOPEDIC SURGERY",
                "DERMATOLOGY",
                "ORTHOPEDIC SURGERY",
                "DERMATOLOGY",
            ],
            "visits": [10, 20, 30, 5, 7],
        }
    )
    m = iqvia_metrics(visits, groups=["ORTHOPEDIC SURGERY"])
    assert m["total"][2021] == 72
    assert m["age65"][2021] == 50 and m["under65"][2021] == 17
    assert m["by_group"].loc["ORTHOPEDIC SURGERY", 2021] == 35
    assert m["by_group"].loc["other", 2021] == 37


def test_hospital_share_is_hospital_visits_over_all_visits():
    pos = pd.DataFrame(
        {
            "year": [2021] * 3 + [2024] * 3,
            "place_of_service": ["HOSPITAL", "OFFICE", "OTHER"] * 2,
            "visits": [10, 80, 10, 5, 90, 5],
        }
    )
    out = hospital_share(pos)
    assert out[2021] == pytest.approx(0.10) and out[2024] == pytest.approx(0.05)


def test_annual_sales_takes_the_calendar_year_rows_only():
    revenue = pd.DataFrame(
        {
            "period_end": ["2021-12-31", "2021-09-30", "2021-12-31", "2024-12-31"],
            "period_type": ["year", "nine_months", "quarter", "year"],
            "net_sales_usd": [102.7, 74.0, 28.6, 118.1],
        }
    )
    out = annual_sales(revenue)
    assert out == {2021: 102.7, 2024: 118.1}
