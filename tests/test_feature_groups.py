"""Tests for the seven feature groups (G1 to G7) of the feature plan (DL-75).

Hand-computed answers on tiny tables, the generic leakage test (rewrite everything from month t
onward, the features for month t must not move) for every IQVIA-derived group, a deliberately
leaky builder that the same test must catch, and the as-of semantics of the outside groups.
No group is run against a model here.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.candidate_families import KEYS
from oa_market_intelligence.features.groups import (
    COVID_MONTHS,
    DIP_MONTHS,
    GROUP_COLUMNS_T1,
    GROUP_COLUMNS_T2,
    T1_GROUPS,
    T2_GROUPS,
    columns_for,
    competitor_features,
    national_medicare_adoption,
    promotion_features,
    task_a_group_columns,
    task_b_group_columns,
)

SIGNAL_COLUMNS = [
    "signal_id",
    "source_id",
    "specialty_group",
    "grain",
    "period_start_month",
    "period_end_month",
    "value",
    "available_from_month",
]
MONTHS = [201900 + m for m in range(1, 13)]  # 201901 .. 201912


def _seg(rows):
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


def _ramp_table():
    """Segment A: Zilretta visits 1..12 out of 100 each month (share 0.01 x m). Segment B: a
    second segment in the same specialty with a flat 5 of 100."""
    rows = []
    for i, month in enumerate(MONTHS, start=1):
        rows.append((month, "S1", "65 TO 74", "FEMALE", i, 100))
        rows.append((month, "S1", "40 TO 59", "FEMALE", 5, 100))
        rows.append((month, "S2", "65 TO 74", "MALE", 2, 80))
    return _seg(rows)


def _row(frame, month, specialty="S1", age="65 TO 74", gender="FEMALE"):
    hit = frame[
        (frame["month_id"] == month)
        & (frame["specialty_name"] == specialty)
        & (frame["age_band"] == age)
        & (frame["gender"] == gender)
    ]
    assert len(hit) == 1
    return hit.iloc[0]


def _group_visits(overrides=None):
    frame = pd.DataFrame(
        {
            "zilretta": 10.0,
            "triamcinolone_ir": 60.0,
            "other_injectable_corticosteroid": 30.0,
            "other": 5.0,
        },
        index=pd.Index(MONTHS, name="month_id"),
    )
    for (month, column), value in (overrides or {}).items():
        frame.loc[month, column] = value
    return frame


def _signals(rows):
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


# ---------- G1 recent history ----------


def test_g1_segment_history_matches_a_hand_computation():
    out = task_a_group_columns(_ramp_table(), groups=("G1",))
    row = _row(out, 201909)  # month 9: shares of months 1..8 are 0.01 .. 0.08
    assert row["g1_share_lag2"] == pytest.approx(0.07)
    assert row["g1_share_lag3"] == pytest.approx(0.06)
    assert row["g1_share_roll6"] == pytest.approx(np.mean([0.03, 0.04, 0.05, 0.06, 0.07, 0.08]))
    assert row["g1_share_std6"] == pytest.approx(
        np.std([0.03, 0.04, 0.05, 0.06, 0.07, 0.08], ddof=1)
    )
    assert row["g1_share_chg1"] == pytest.approx(0.01)  # 0.08 - 0.07
    assert row["g1_share_chg3"] == pytest.approx(0.03)  # 0.08 - 0.05
    assert row["g1_log_visits_chg"] == pytest.approx(0.0)
    assert row["g1_missing"] == 0.0


def test_g1_is_missing_and_zero_filled_until_there_is_history():
    out = task_a_group_columns(_ramp_table(), groups=("G1",))
    first = _row(out, 201901)
    assert first["g1_missing"] == 1.0
    assert first[GROUP_COLUMNS_T2["G1"][:-1]].abs().sum() == 0.0
    assert not out[GROUP_COLUMNS_T2["G1"]].isna().any().any()


def test_g1_national_series_uses_the_pooled_share_and_the_t1_column_set():
    monthly = pd.DataFrame(
        {
            "month_id": MONTHS,
            "branded_injectable_visits": [10 * (i + 1) for i in range(12)],
            "total_category_visits": [1000] * 12,
        }
    )
    out = task_b_group_columns(monthly, groups=("G1",))
    assert list(out.columns) == ["month_id", *GROUP_COLUMNS_T1["G1"]]
    row = out[out["month_id"] == 201909].iloc[0]  # shares 0.01 .. 0.08 before month 9
    assert row["g1_share_roll6"] == pytest.approx(np.mean([0.03, 0.04, 0.05, 0.06, 0.07, 0.08]))
    assert row["g1_share_chg3"] == pytest.approx(0.03)


# ---------- G2 calendar ----------


def test_g2_flags_the_calendar_months_and_marks_the_first_year_missing():
    out = task_a_group_columns(_ramp_table(), groups=("G2",))
    assert _row(out, 201912)["g2_is_dec"] == 1.0 and _row(out, 201911)["g2_is_dec"] == 0.0
    assert _row(out, 201903)["g2_is_mar_apr"] == 1.0 and _row(out, 201905)["g2_is_mar_apr"] == 0.0
    assert (out["g2_missing"] == 1.0).all()  # one year of data: no earlier year to compare with


def test_g2_seasonal_gap_uses_only_earlier_years():
    rows = []
    for year, level in ((2019, 0.02), (2020, 0.04)):
        for month in range(1, 13):
            bump = 0.01 if month == 3 else 0.0
            rows.append(
                (year * 100 + month, "S1", "65 TO 74", "FEMALE", round(1000 * (level + bump)), 1000)
            )
    out = task_a_group_columns(_seg(rows), groups=("G2",))
    march_2020 = _row(out, 202003)
    # earlier years: March 2019 share 0.03 against the 2019 mean of 0.02 + 0.01/12
    mean_2019 = (11 * 0.02 + 0.03) / 12
    assert march_2020["g2_seasonal_gap"] == pytest.approx(0.03 - mean_2019)
    assert march_2020["g2_missing"] == 0.0
    assert _row(out, 201903)["g2_missing"] == 1.0


# ---------- G3 and G4 ----------


def test_g3_cumulative_share_and_activity_match_a_hand_computation():
    out = task_a_group_columns(_ramp_table(), groups=("G3",))
    row = _row(out, 201905)  # earlier months 1..4: z = 1+2+3+4 = 10 of 400
    assert row["g3_cum_share"] == pytest.approx(10 / 400)
    assert row["g3_months_active"] == 4
    assert row["g3_months_since_zilretta"] == 1
    assert row["g3_log_cum_zilretta"] == pytest.approx(np.log1p(10))
    assert row["g3_weight_k20"] == pytest.approx(400 / 420)
    first = _row(out, 201901)
    assert first["g3_cum_share"] == 0.0 and first["g3_weight_k20"] == 0.0


def test_g4_uses_the_specialtys_share_of_the_previous_month():
    out = task_a_group_columns(_ramp_table(), groups=("G4",))
    row = _row(out, 201906)
    # specialty S1 pools its two segments: month 5 is (5 + 5) of 200, month 2 is (2 + 5) of 200
    share_5, share_2 = 10 / 200, 7 / 200
    assert row["g4_logit_spec_share_lag1"] == pytest.approx(np.log(share_5 / (1 - share_5)))
    assert row["g4_spec_change_3m"] == pytest.approx(share_5 - share_2)
    assert row["g4_missing"] == 0.0
    assert _row(out, 201901)["g4_missing"] == 1.0


# ---------- G5 competitor volumes ----------


def test_g5_uses_the_previous_months_competitor_mix():
    visits = _group_visits({(201905, "triamcinolone_ir"): 120.0})
    out = competitor_features(visits, MONTHS)
    row = out[out["month_id"] == 201906].iloc[0]
    assert row["g5_tri_share_lag1"] == pytest.approx(120 / (10 + 120 + 30))
    assert row["g5_zil_vs_tri_lag1"] == pytest.approx(10 / (10 + 120))
    assert row["g5_steroid_chg3"] == pytest.approx(np.log(160) - np.log(100))
    assert row["g5_missing"] == 0.0
    assert out[out["month_id"] == 201903]["g5_missing"].iloc[0] == 1.0  # no month t-4 yet


def test_g5_enters_both_targets_when_group_visits_are_given():
    visits = _group_visits()
    a = task_a_group_columns(_ramp_table(), group_visits=visits, groups=("G5",))
    b = task_b_group_columns(
        pd.DataFrame(
            {
                "month_id": MONTHS,
                "branded_injectable_visits": 10,
                "total_category_visits": 100,
            }
        ),
        group_visits=visits,
        groups=("G5",),
    )
    assert set(GROUP_COLUMNS_T2["G5"]) <= set(a.columns)
    assert set(GROUP_COLUMNS_T1["G5"]) <= set(b.columns)
    assert len(a) == len(_ramp_table())


# ---------- G6 outside signals, as of the previous month ----------


def test_g6_promotion_needs_six_known_months_and_uses_only_what_was_public():
    rows = [
        ("openpay.total_amount_usd", "openpay", "", "month", m, m, 100.0, m + 2) for m in MONTHS
    ]
    out = promotion_features(_signals(rows), [201908, 201909, 201912])
    by = out.set_index("month_id")
    # month 201908: as of 201907, public through period_end 201905: five months, not six
    assert by.loc[201908, "g6_promo_missing"] == 1.0 and by.loc[201908, "g6_promo_6m_log"] == 0.0
    # month 201909: as of 201908, public through 201906: months 1..6 sum to 600
    assert by.loc[201909, "g6_promo_missing"] == 0.0
    assert by.loc[201909, "g6_promo_6m_log"] == pytest.approx(np.log1p(600.0))
    assert by.loc[201912, "g6_promo_6m_log"] == pytest.approx(np.log1p(600.0))  # still the last 6


def test_g6_promotion_ignores_a_value_published_after_the_as_of_month():
    rows = [("openpay.total_amount_usd", "openpay", "", "month", m, m, 1.0, m + 2) for m in MONTHS]
    base = promotion_features(_signals(rows), [201909])
    planted = rows + [
        ("openpay.total_amount_usd", "openpay", "", "month", 201907, 201907, 1e9, 201909)
    ]
    again = promotion_features(_signals(planted), [201909])
    pd.testing.assert_frame_equal(base, again)  # 201909 is after the as-of month 201908


def test_g6_national_medicare_adoption_appears_only_once_public():
    rows = [
        ("medicare.adoption_rate", "partb_provider", "", "year", 202001, 202012, 0.0106, 202212)
    ]
    out = national_medicare_adoption(_signals(rows), [202212, 202301]).set_index("month_id")
    assert out.loc[202212, "g6_medicare_missing"] == 1.0  # as of 202211: not yet public
    assert out.loc[202301, "g6_medicare_adoption"] == pytest.approx(0.0106)
    assert out.loc[202301, "g6_medicare_missing"] == 0.0


def test_g6_enters_both_targets_with_the_month_level_columns():
    rows = [
        ("asp.price_ratio", "asp", "", "quarter", 201903, 201903, 1.5, 201905),
        ("company.net_sales_usd", "sec", "", "quarter", 201903, 201903, 100.0, 201905),
    ]
    signals = _signals(rows)
    a = task_a_group_columns(_ramp_table(), signals=signals, groups=("G6",))
    b = task_b_group_columns(
        pd.DataFrame(
            {"month_id": MONTHS, "branded_injectable_visits": 10, "total_category_visits": 100}
        ),
        signals=signals,
        groups=("G6",),
    )
    assert set(GROUP_COLUMNS_T2["G6"]) <= set(a.columns)
    assert set(GROUP_COLUMNS_T1["G6"]) <= set(b.columns)
    assert not a[GROUP_COLUMNS_T2["G6"]].isna().any().any()


# ---------- G7 flags ----------


def test_g7_flags_the_covid_and_dip_months_and_the_pediatrics_group():
    rows = []
    for month in (202002, 202003, 202005, 202006, 202402, 202403, 202407, 202408):
        rows.append((month, "PEDIATRICS", "65 TO 74", "FEMALE", 1, 10))
        rows.append((month, "S1", "65 TO 74", "FEMALE", 1, 10))
    out = task_a_group_columns(_seg(rows), groups=("G7",))
    covid = {m: _row(out, m, "S1")["g7_covid"] for m in (202002, 202003, 202005, 202006)}
    dip = {m: _row(out, m, "S1")["g7_dip"] for m in (202402, 202403, 202407, 202408)}
    assert covid == {202002: 0.0, 202003: 1.0, 202005: 1.0, 202006: 0.0}
    assert dip == {202402: 0.0, 202403: 1.0, 202407: 1.0, 202408: 0.0}
    assert _row(out, 202003, "PEDIATRICS")["g7_pediatrics"] == 1.0
    assert _row(out, 202003, "S1")["g7_pediatrics"] == 0.0
    assert COVID_MONTHS == (202003, 202005) and DIP_MONTHS == (202403, 202407)


# ---------- the generic leakage test ----------


def _all_t2(seg, visits):
    return task_a_group_columns(seg, group_visits=visits, groups=("G1", "G2", "G3", "G4", "G5"))


def test_no_iqvia_derived_group_uses_month_t_or_later():
    seg, visits = _ramp_table(), _group_visits()
    cut = 201907
    base = _all_t2(seg, visits)
    altered = seg.copy()
    later = altered["month_id"] >= cut
    altered.loc[later, "branded_injectable_visits"] = (
        altered.loc[later, "total_category_visits"] // 2
    )
    altered.loc[later, "total_category_visits"] = altered.loc[later, "total_category_visits"] + 37
    visits_altered = visits.copy()
    visits_altered.loc[visits_altered.index >= cut, :] = 9999.0
    again = _all_t2(altered, visits_altered)
    columns = [c for c in base.columns if c not in KEYS]
    keep = base["month_id"] <= cut  # month t itself and everything before it must not move
    assert keep.sum() > 0
    np.testing.assert_allclose(
        base.loc[keep, columns].to_numpy(float), again.loc[keep, columns].to_numpy(float)
    )


def test_a_leaky_builder_is_caught_by_the_same_rewrite():
    seg = _ramp_table()
    cut = 201907

    def leaky(frame):  # a "feature" that includes the month's own share
        share = frame["branded_injectable_visits"] / frame["total_category_visits"]
        return share.to_numpy()

    altered = seg.copy()
    later = altered["month_id"] >= cut
    altered.loc[later, "branded_injectable_visits"] = 0
    before = leaky(seg)[(seg["month_id"] == cut).to_numpy()]
    after = leaky(altered)[(altered["month_id"] == cut).to_numpy()]
    assert not np.allclose(before, after)  # the test would flag it


# ---------- coverage and entry points ----------


def test_every_listed_column_is_produced_and_none_is_missing():
    signals = _signals(
        [
            ("asp.price_ratio", "asp", "", "quarter", 201903, 201903, 1.5, 201905),
            ("company.net_sales_usd", "sec", "", "quarter", 201903, 201903, 100.0, 201905),
            ("openpay.total_amount_usd", "openpay", "", "month", 201901, 201901, 10.0, 201903),
        ]
    )
    out = task_a_group_columns(_ramp_table(), signals=signals, group_visits=_group_visits())
    expected = {c for g in T2_GROUPS for c in GROUP_COLUMNS_T2[g]}
    assert expected <= set(out.columns)
    assert not out[sorted(expected)].isna().any().any()
    national = task_b_group_columns(
        pd.DataFrame(
            {"month_id": MONTHS, "branded_injectable_visits": 10, "total_category_visits": 100}
        ),
        signals=signals,
        group_visits=_group_visits(),
    )
    expected_t1 = {c for g in T1_GROUPS for c in GROUP_COLUMNS_T1[g]}
    assert expected_t1 <= set(national.columns)
    assert not national[sorted(expected_t1)].isna().any().any()


def test_the_group_lists_match_the_plan():
    assert T2_GROUPS == ("G1", "G2", "G3", "G4", "G5", "G6", "G7")
    assert T1_GROUPS == ("G1", "G2", "G5", "G6", "G7")
    assert "g7_pediatrics" in GROUP_COLUMNS_T2["G7"]
    assert "g7_pediatrics" not in GROUP_COLUMNS_T1["G7"]


def test_columns_for_leaves_out_the_shrinkage_columns_handled_by_the_tuner():
    columns = columns_for(("G3",), target="T2")
    assert "g3_cum_share" in columns
    assert not any(c.startswith("g3_logit_shrunk") or c.startswith("g3_weight") for c in columns)


def test_the_building_is_deterministic():
    seg, visits = _ramp_table(), _group_visits()
    pd.testing.assert_frame_equal(_all_t2(seg, visits), _all_t2(seg, visits))


def test_every_group_column_is_documented_in_the_feature_dictionary():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docs" / "feature_dictionary.md").read_text(
        "utf-8"
    )
    columns = {
        c for table in (GROUP_COLUMNS_T1, GROUP_COLUMNS_T2) for cols in table.values() for c in cols
    }
    missing = sorted(c for c in columns if f"`{c}`" not in text)
    assert not missing, missing
