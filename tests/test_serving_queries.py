"""Tests for the read-only serving queries (src/.../serving/queries.py).

A tiny hand-built Gold database keeps every expected number checkable by hand. The market
share is 10% in both months, so a specialty's expected Zilretta share is exactly 10% and
the arithmetic is easy to follow:

- specialty A: 8 + 20 = 28 Zilretta visits of 40 + 100 = 140 -> 20% observed, 2.0x expected
- specialty B: 2 + 0  =  2 Zilretta visits of 60 + 100 = 160 -> 1.25% observed, 0.125x expected
"""

import pandas as pd
import pytest
from sqlalchemy import create_engine

from oa_market_intelligence.serving.queries import (
    data_status,
    market_trend,
    segment_table,
)
from oa_market_intelligence.warehouse.schema import (
    create_schema,
    dim_demographics,
    dim_month,
    dim_specialty,
    gold_segment_adoption,
    gold_visit_share_monthly,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    create_schema(eng)
    with eng.begin() as conn:
        conn.execute(
            dim_month.insert(),
            [
                dict(month_id=201908, calendar_date="2019-08-01", year=2019, quarter=3,
                     month_number=8, month_name="August"),
                dict(month_id=201909, calendar_date="2019-09-01", year=2019, quarter=3,
                     month_number=9, month_name="September"),
            ],
        )
        conn.execute(
            dim_specialty.insert(),
            [dict(specialty_id=1, specialty_name="A"), dict(specialty_id=2, specialty_name="B")],
        )
        conn.execute(
            dim_demographics.insert(),
            [
                dict(demographic_id=1, age_band="65 TO 74", gender="FEMALE"),
                dict(demographic_id=2, age_band="40 TO 59", gender="MALE"),
            ],
        )
        conn.execute(
            gold_visit_share_monthly.insert(),
            [
                dict(month_id=201908, branded_injectable_visits=10,
                     generic_corticosteroid_visits=80, nsaid_otc_visits=10, visit_share=0.10,
                     direction_label=None),
                dict(month_id=201909, branded_injectable_visits=20,
                     generic_corticosteroid_visits=160, nsaid_otc_visits=20, visit_share=0.10,
                     direction_label="Flat"),
            ],
        )
        conn.execute(
            gold_segment_adoption.insert(),
            [
                dict(month_id=201908, specialty_id=1, demographic_id=1,
                     branded_injectable_visits=8, total_category_visits=40,
                     segment_visit_share=0.2),
                dict(month_id=201908, specialty_id=2, demographic_id=2,
                     branded_injectable_visits=2, total_category_visits=60,
                     segment_visit_share=2 / 60),
                dict(month_id=201909, specialty_id=1, demographic_id=1,
                     branded_injectable_visits=20, total_category_visits=100,
                     segment_visit_share=0.2),
                dict(month_id=201909, specialty_id=2, demographic_id=2,
                     branded_injectable_visits=0, total_category_visits=100,
                     segment_visit_share=0.0),
            ],
        )
    return eng


def test_data_status_summarises_what_the_site_is_showing(engine):
    status = data_status(engine)
    assert status["first_month_id"] == 201908
    assert status["last_month_id"] == 201909
    assert status["n_months"] == 2
    assert status["zilretta_visits"] == 30
    assert status["category_visits"] == 300


def test_market_trend_is_ordered_by_month_with_labels(engine):
    trend = market_trend(engine)
    assert trend["month_id"].tolist() == [201908, 201909]
    assert pd.api.types.is_datetime64_any_dtype(trend["month"])
    assert trend["visit_share"].tolist() == [0.10, 0.10]
    assert pd.isna(trend.loc[0, "direction_label"])
    assert trend.loc[1, "direction_label"] == "Flat"
    counts = {"branded_injectable_visits", "generic_corticosteroid_visits", "nsaid_otc_visits"}
    assert counts <= set(trend.columns)


def test_specialty_table_compares_observed_with_the_market_wide_share(engine):
    table = segment_table(engine, by="specialty").set_index("group")

    a, b = table.loc["A"], table.loc["B"]
    assert (a["zilretta_visits"], a["category_visits"]) == (28, 140)
    assert a["observed_share"] == pytest.approx(0.20)
    assert a["expected_share"] == pytest.approx(0.10)
    assert a["ratio"] == pytest.approx(2.0)
    assert (b["zilretta_visits"], b["category_visits"]) == (2, 160)
    assert b["observed_share"] == pytest.approx(2 / 160)
    assert b["expected_share"] == pytest.approx(0.10)
    assert b["ratio"] == pytest.approx(0.125)


def test_table_is_sorted_by_volume_and_intervals_bracket_the_observed_share(engine):
    table = segment_table(engine, by="specialty")
    assert table["group"].tolist() == ["B", "A"]  # 160 visits before 140
    for _, row in table.iterrows():
        assert 0.0 <= row["ci_low"] <= row["observed_share"] <= row["ci_high"] <= 1.0


@pytest.mark.parametrize(
    "by, groups",
    [("age_band", {"65 TO 74", "40 TO 59"}), ("gender", {"FEMALE", "MALE"})],
)
def test_the_same_comparison_by_age_band_and_gender(engine, by, groups):
    table = segment_table(engine, by=by)
    assert set(table["group"]) == groups
    assert table["zilretta_visits"].sum() == 30
    assert table["category_visits"].sum() == 300


def test_min_visits_drops_small_groups(engine):
    table = segment_table(engine, by="specialty", min_visits=150)
    assert table["group"].tolist() == ["B"]


def test_an_unknown_grouping_is_rejected(engine):
    with pytest.raises(ValueError, match="by must be one of"):
        segment_table(engine, by="manufacturer")
