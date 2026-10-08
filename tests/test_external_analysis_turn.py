"""Tests for the 6c analyses: the E4 hypotheses (H1 to H4) and E2b.

Everything is synthetic, with planted effects, so each rule is checked on a case whose answer is
known. Nothing here touches the real data.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.turn import (
    block_permutation_lags,
    direction,
    e2b_table,
    e2b_verdict,
    e5_trigger,
    h1_verdict,
    h2_verdict,
    h3_verdict,
    h4_result,
    lagged_first_difference_corr,
    months_between,
    price_per_mg,
    quarterly_visits,
    rolling_change,
)


def _months(first, count):
    out, year, month = [], first // 100, first % 100
    for _ in range(count):
        out.append(year * 100 + month)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _series(values, first=201901):
    return pd.Series(values, index=_months(first, len(values)), dtype=float)


# ---------- pinned settings ----------


def test_the_new_settings_match_the_protocol():
    assert config.H2_J3301_MG_PER_UNIT == 10  # "J3301 is quoted per 10 mg"
    assert config.H4_MONTHS == (3, 7)  # March to July
    assert (config.H4_YEAR, config.H4_BASE_YEAR) == (2024, 2023)
    assert config.E2B_QUARTERS == ("2020Q3", "2025Q2")


# ---------- month arithmetic ----------


def test_months_between_crosses_year_ends():
    assert months_between(202104, 202203) == 11
    assert months_between(202203, 202104) == -11
    assert months_between(202012, 202101) == 1


# ---------- H1 ----------


def test_rolling_change_compares_the_latest_six_months_with_the_six_before():
    s = _series([10] * 6 + [4] * 6)
    out = rolling_change(s).set_index("month_id")
    assert out.loc[201912, "change"] == pytest.approx(-0.6)
    assert out.loc[201906, "change"] != out.loc[201906, "change"]  # not enough history: missing


def test_h1_is_supported_when_a_six_month_mean_falls_25_percent_or_more_inside_the_window():
    values = [100] * 24 + [70] * 6 + [100] * 30
    out = h1_verdict(_series(values), window=(202101, 202312))
    assert out["verdict"] == "supported" and out["worst_change"] < -0.25
    assert 202101 <= out["worst_month"] <= 202312


def test_a_fall_of_exactly_25_percent_counts():
    values = [100] * 6 + [75] * 6
    out = h1_verdict(_series(values), window=(201912, 201912))
    assert out["verdict"] == "supported" and out["worst_change"] == pytest.approx(-0.25)


def test_a_fall_outside_the_window_does_not_count():
    values = [100] * 24 + [10] * 24
    out = h1_verdict(_series(values), window=(201912, 202006))
    assert out["verdict"] == "not_supported"


def test_h1_is_not_supported_for_a_flat_series():
    out = h1_verdict(_series([100.0] * 48), window=(201912, 202212))
    assert out["verdict"] == "not_supported" and out["worst_change"] == pytest.approx(0.0)


# ---------- lagged correlations ----------


def _planted(lag=3, n=80, noise=0.3, seed=1):
    rng = np.random.default_rng(seed)
    promo = _series(rng.normal(size=n).cumsum() + 50)
    d = promo.diff()
    share = d.shift(lag).fillna(0) + rng.normal(scale=noise, size=n)
    return promo, _series(share.cumsum() + 5)


def test_the_correlation_is_taken_on_first_differences_and_peaks_at_the_planted_lag():
    promo, share = _planted(lag=3)
    out = lagged_first_difference_corr(promo, share, max_lag=6).set_index("lag")
    assert out["r"].idxmax() == 3 and out.loc[3, "r"] > 0.7
    assert len(out) == 7


def test_a_shared_trend_alone_is_not_a_correlation_once_differenced():
    rng = np.random.default_rng(2)
    n = 80
    a = _series(np.arange(n) * 1.0 + rng.normal(scale=0.5, size=n))
    b = _series(np.arange(n) * 2.0 + rng.normal(scale=0.5, size=n))
    out = lagged_first_difference_corr(a, b, max_lag=6)
    assert out["r"].abs().max() < 0.4  # the levels correlate near 1; the differences do not


def test_block_permutation_flags_only_the_planted_lag_after_bonferroni():
    promo, share = _planted(lag=3, noise=0.2)
    out = block_permutation_lags(promo, share, max_lag=6, block=6, n_perm=500, seed=0)
    out = out.set_index("lag")
    assert bool(out.loc[3, "significant"]) and out["significant"].sum() == 1
    assert out["p_value"].min() >= 1 / 501  # a permutation p-value is never zero
    assert out.loc[3, "alpha"] == pytest.approx(0.05 / 7)


def test_independent_series_give_no_significant_lag():
    rng = np.random.default_rng(7)
    promo = _series(rng.normal(size=80).cumsum())
    share = _series(rng.normal(size=80).cumsum())
    out = block_permutation_lags(promo, share, max_lag=6, block=6, n_perm=300, seed=0)
    assert not out["significant"].any()


def test_block_permutation_is_reproducible_and_depends_on_the_seed():
    promo, share = _planted()
    a = block_permutation_lags(promo, share, max_lag=6, block=6, n_perm=100, seed=0)
    b = block_permutation_lags(promo, share, max_lag=6, block=6, n_perm=100, seed=0)
    c = block_permutation_lags(promo, share, max_lag=6, block=6, n_perm=100, seed=1)
    pd.testing.assert_frame_equal(a, b)
    assert not a["p_value"].equals(c["p_value"])


def test_a_gap_in_the_months_is_refused():
    promo, share = _planted()
    with pytest.raises(ValueError, match="consecutive"):
        lagged_first_difference_corr(promo.drop(promo.index[10]), share, max_lag=3)


def test_e5_runs_only_if_some_lag_is_significant():
    yes = pd.DataFrame({"lag": [0, 3], "significant": [False, True]})
    no = pd.DataFrame({"lag": [0, 3], "significant": [False, False]})
    assert e5_trigger(yes)[0] is True and e5_trigger(yes)[1] == [3]
    assert e5_trigger(no) == (False, [])


# ---------- H2 ----------


def test_the_j3301_limit_is_converted_from_per_10_mg_to_per_mg():
    assert price_per_mg(1.5, 10) == pytest.approx(0.15)
    assert price_per_mg(18.0, 1) == pytest.approx(18.0)


def test_h2_is_supported_when_the_price_ratio_moves_10_percent_or_more():
    ratio = {"2020Q2": 100.0, "2021Q2": 110.0}
    assert h2_verdict(ratio)["verdict"] == "supported"
    assert h2_verdict({"2020Q2": 100.0, "2021Q2": 90.0})["verdict"] == "supported"  # either way
    out = h2_verdict({"2020Q2": 100.0, "2021Q2": 109.0})
    assert out["verdict"] == "not_supported" and out["change"] == pytest.approx(0.09)


# ---------- H3 ----------


def test_h3_compares_the_stored_break_with_the_end_of_pass_through():
    out = h3_verdict(202203)
    assert out["distance_months"] == 11 and out["verdict"] == "not_supported"
    assert h3_verdict(202107)["verdict"] == "supported"
    assert h3_verdict(202110)["verdict"] == "supported"  # exactly 6 months is inside
    assert h3_verdict(202111)["verdict"] == "not_supported"


# ---------- H4 ----------


def _visits(drops):
    rows = []
    for year, scale in ((2023, 1.0), (2024, None)):
        for month in range(1, 13):
            row = {"month_id": year * 100 + month}
            for name, drop in drops.items():
                base = 1000.0
                inside = 3 <= month <= 7
                row[name] = base if year == 2023 else base * (1 - drop if inside else 1.0)
            rows.append(row)
    return pd.DataFrame(rows)


def test_h4_is_supported_only_if_all_three_categories_are_10_percent_or_more_below():
    names = ["branded_injectable_visits", "generic_corticosteroid_visits", "nsaid_otc_visits"]
    all_down = h4_result(_visits(dict.fromkeys(names, 0.2)))
    assert all_down["verdict"] == "supported"
    assert all(v == pytest.approx(-0.2) for v in all_down["changes"].values())
    one_up = h4_result(_visits({names[0]: 0.5, names[1]: 0.2, names[2]: 0.05}))
    assert one_up["verdict"] == "not_supported"
    exactly = h4_result(_visits(dict.fromkeys(names, 0.1)))
    assert exactly["verdict"] == "supported"  # exactly 10% counts


def test_h4_uses_only_march_to_july():
    names = ["branded_injectable_visits", "generic_corticosteroid_visits", "nsaid_otc_visits"]
    frame = _visits(dict.fromkeys(names, 0.0))
    frame.loc[frame["month_id"] == 202412, names] = 1.0  # a collapse in December must not matter
    assert h4_result(frame)["verdict"] == "not_supported"


# ---------- E2b ----------


def test_direction_has_a_flat_band_of_one_percent():
    assert direction(100, 102) == "up"
    assert direction(100, 98) == "down"
    assert direction(100, 100.9) == "flat" and direction(100, 99.1) == "flat"
    assert direction(100, 101) == "up"  # a change of exactly 1% is not flat


def test_quarterly_visits_sums_months_and_counts_them():
    monthly = _series([1, 2, 3, 4, 5, 6], first=202001)
    out = quarterly_visits(monthly).set_index("quarter_id")
    assert out.loc["2020Q1", "visits"] == 6 and out.loc["2020Q1", "n_months"] == 3
    assert out.loc["2020Q2", "visits"] == 15


def test_a_year_ago_quarter_with_missing_months_is_matched_on_the_months_present_in_both():
    # Q3 of the earlier year has only two months (as IQVIA's Q3 2019 does)
    early = _series([10, 10], first=201908)  # Aug, Sep 2019
    later = _series([12, 12, 12], first=202007)  # Jul, Aug, Sep 2020
    monthly = pd.concat([early, later])
    out = quarterly_visits(monthly).set_index("quarter_id")
    assert out.loc["2020Q3", "yoy_change"] == pytest.approx(12 / 10 - 1)  # Aug and Sep only


def _quarters():
    return ["2020Q3", "2020Q4", "2021Q1", "2021Q2"]


def test_e2b_agreement_is_the_share_of_quarters_where_both_directions_match():
    sales = pd.DataFrame(
        {
            "quarter_id": _quarters(),
            "company": "Co",
            "net_sales_usd": [110, 110, 90, 90],
            "yoy_sales_change": [0.10, 0.10, -0.10, -0.10],
        }
    )
    visits = pd.DataFrame(
        {
            "quarter_id": _quarters(),
            "visits": [5, 5, 5, 5],
            "yoy_change": [0.2, 0.2, -0.2, 0.2],
        }
    )
    table = e2b_table(sales, visits, ("2020Q3", "2021Q2"))
    assert list(table["directions_agree"]) == [1, 1, 1, 0]
    verdict = e2b_verdict(table)
    assert verdict["agreement"] == pytest.approx(0.75) and verdict["verdict"] == "consistent"


def test_e2b_is_inconsistent_below_70_percent():
    sales = pd.DataFrame(
        {
            "quarter_id": _quarters(),
            "company": "Co",
            "net_sales_usd": 1.0,
            "yoy_sales_change": [0.1, 0.1, 0.1, 0.1],
        }
    )
    visits = pd.DataFrame(
        {"quarter_id": _quarters(), "visits": 1, "yoy_change": [0.1, -0.1, -0.1, 0.1]}
    )
    table = e2b_table(sales, visits, ("2020Q3", "2021Q2"))
    assert e2b_verdict(table)["verdict"] == "inconsistent"  # 2 of 4 agree


def test_quarters_without_a_year_ago_comparison_are_left_out_of_the_agreement():
    sales = pd.DataFrame(
        {
            "quarter_id": _quarters(),
            "company": "Co",
            "net_sales_usd": 1.0,
            "yoy_sales_change": [np.nan, 0.1, 0.1, 0.1],
        }
    )
    visits = pd.DataFrame(
        {"quarter_id": _quarters(), "visits": 1, "yoy_change": [0.1, 0.1, 0.1, 0.1]}
    )
    table = e2b_table(sales, visits, ("2020Q3", "2021Q2"))
    assert e2b_verdict(table)["n_quarters"] == 3


def test_quarterly_sales_uses_quarter_rows_only_and_compares_with_the_year_before():
    from oa_market_intelligence.external.analysis.turn import quarterly_sales

    revenue = pd.DataFrame(
        {
            "period_end": ["2020-03-31", "2020-09-30", "2021-03-31", "2021-03-31"],
            "company": "Co",
            "period_type": ["quarter", "nine_months", "quarter", "year"],
            "net_sales_usd": [100.0, 999.0, 120.0, 888.0],
            "derived": 0,
        }
    )
    out = quarterly_sales(revenue).set_index("quarter_id")
    assert list(out.index) == ["2020Q1", "2021Q1"]
    assert pd.isna(out.loc["2020Q1", "yoy_sales_change"])
    assert out.loc["2021Q1", "yoy_sales_change"] == pytest.approx(0.2)


def test_h4_also_reports_how_many_single_months_have_all_three_categories_down():
    names = ["branded_injectable_visits", "generic_corticosteroid_visits", "nsaid_otc_visits"]
    frame = _visits(dict.fromkeys(names, 0.2))
    out = h4_result(frame)
    assert out["months_all_below"] == 5 and out["n_months"] == 5
    # one weak month: the pooled reading still passes, the month-by-month reading does not
    frame.loc[frame["month_id"] == 202407, "nsaid_otc_visits"] = 980.0
    out = h4_result(frame)
    assert out["verdict"] == "supported" and out["months_all_below"] == 4
