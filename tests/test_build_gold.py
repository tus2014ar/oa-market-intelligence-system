"""Tests for the Gold serving-table builder.

Hand-built DataFrames exercise each function's logic in isolation - the §18.1 visit-
share formula, the §18.2 direction-label threshold (including its exact-boundary
behavior), the lag/rolling features, and the Method B FDA-derived features (including
the zero-competitor and multi-competitor cases the current real data can't exercise).
A real-data integration test runs the whole thing end to end and checks it against
figures independently confirmed in prior steps (135,119 total branded_injectable
visits, a 1.7%-3.4% visit_share range, the April 2020 COVID Office/Telehealth shock),
plus that gold_segment_adoption's per-segment sums reconcile exactly to
gold_visit_share_monthly's per-month totals.
"""

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.ingestion.nmta_loader import parse_pivot_sheet
from oa_market_intelligence.ingestion.place_of_service_loader import parse_place_of_service
from oa_market_intelligence.ingestion.reference_loader import parse_reference_table
from oa_market_intelligence.warehouse.build_gold import (
    build_gold,
    compute_direction_label,
    compute_fda_derived_features,
    compute_lag_and_rolling_features,
    compute_visit_share,
    refresh_gold_segment_adoption,
    refresh_gold_visit_share_monthly,
)
from oa_market_intelligence.warehouse.build_silver import build_silver
from oa_market_intelligence.warehouse.schema import create_schema

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    create_schema(eng)
    return eng


# ---------- compute_visit_share ----------


def test_compute_visit_share_matches_18_1_formula():
    totals = pd.DataFrame(
        {
            "month_id": [201908],
            "branded_injectable_visits": [1390],
            "generic_corticosteroid_visits": [70354],
            "nsaid_otc_visits": [3570],
        }
    )
    result = compute_visit_share(totals)
    expected = 1390 / (1390 + 70354 + 3570)
    assert result.loc[0, "visit_share"] == pytest.approx(expected)


def test_compute_visit_share_is_nan_not_zero_when_denominator_is_zero():
    totals = pd.DataFrame(
        {
            "month_id": [201908],
            "branded_injectable_visits": [0],
            "generic_corticosteroid_visits": [0],
            "nsaid_otc_visits": [0],
        }
    )
    result = compute_visit_share(totals)
    assert pd.isna(result.loc[0, "visit_share"])


# ---------- compute_direction_label ----------


def _monthly(shares: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"month_id": range(201901, 201901 + len(shares)), "visit_share": shares})


def test_direction_label_first_month_is_null():
    result = compute_direction_label(_monthly([0.02, 0.02]))
    assert result.loc[0, "direction_label"] is None


def test_direction_label_up_and_down():
    # +2.0pp then -2.0pp, well past the default 1.0pp threshold in both directions.
    result = compute_direction_label(_monthly([0.02, 0.04, 0.02]))
    assert result.loc[1, "direction_label"] == "Up"
    assert result.loc[2, "direction_label"] == "Down"


def test_direction_label_exact_threshold_is_flat_not_up():
    # §18.2: "Up: change > +1.0pp" - a change of *exactly* 1.0pp must not count as Up.
    result = compute_direction_label(_monthly([0.02, 0.03]), threshold_pp=1.0)
    assert result.loc[1, "direction_label"] == "Flat"


def test_direction_label_respects_custom_threshold():
    result = compute_direction_label(_monthly([0.020, 0.023]), threshold_pp=0.1)
    assert result.loc[1, "direction_label"] == "Up"


# ---------- compute_lag_and_rolling_features ----------


def test_lag_features_shift_by_correct_amount():
    result = compute_lag_and_rolling_features(_monthly([0.01, 0.02, 0.03, 0.04]))
    assert result.loc[3, "visit_share_lag_1"] == pytest.approx(0.03)
    assert result.loc[3, "visit_share_lag_2"] == pytest.approx(0.02)
    assert result.loc[3, "visit_share_lag_3"] == pytest.approx(0.01)
    assert pd.isna(result.loc[0, "visit_share_lag_1"])


def test_rolling_features_are_null_until_window_is_full():
    result = compute_lag_and_rolling_features(_monthly([0.01, 0.02, 0.03, 0.04, 0.05, 0.06]))
    assert pd.isna(result.loc[1, "visit_share_roll_3mo"])
    assert result.loc[2, "visit_share_roll_3mo"] == pytest.approx((0.01 + 0.02 + 0.03) / 3)
    assert pd.isna(result.loc[4, "visit_share_roll_6mo"])
    assert result.loc[5, "visit_share_roll_6mo"] == pytest.approx(
        sum([0.01, 0.02, 0.03, 0.04, 0.05, 0.06]) / 6
    )


# ---------- compute_fda_derived_features ----------


def test_fda_features_all_null_with_no_known_competitor_dates():
    monthly = pd.DataFrame({"month_id": [201908]})
    result = compute_fda_derived_features(monthly, competitor_dates=[])
    assert result.loc[0, "months_since_launch"] is None
    assert result.loc[0, "is_post_launch"] is None
    assert result.loc[0, "competitor_count_on_market"] == 0
    assert result.loc[0, "months_since_last_competitor_event"] is None


def test_fda_features_single_competitor_matches_zilretta_case():
    # Zilretta's real approval date; Aug 2019 is 22 months after Oct 2017.
    monthly = pd.DataFrame({"month_id": [201908]})
    result = compute_fda_derived_features(monthly, competitor_dates=[pd.Timestamp("2017-10-06")])
    assert result.loc[0, "months_since_launch"] == 22
    assert result.loc[0, "is_post_launch"] == 1
    assert result.loc[0, "competitor_count_on_market"] == 1
    assert result.loc[0, "months_since_last_competitor_event"] == 22


def test_fda_features_before_launch_is_not_post_launch():
    monthly = pd.DataFrame({"month_id": [201501]})  # before the 2017-10 approval
    result = compute_fda_derived_features(monthly, competitor_dates=[pd.Timestamp("2017-10-06")])
    assert result.loc[0, "months_since_launch"] < 0
    assert result.loc[0, "is_post_launch"] == 0
    assert result.loc[0, "competitor_count_on_market"] == 0
    assert result.loc[0, "months_since_last_competitor_event"] is None


def test_fda_features_two_competitors_counts_and_uses_most_recent_event():
    # Exercises the multi-competitor branch the current real data (Zilretta-only)
    # cannot: a second branded_injectable enters the market between two test months.
    monthly = pd.DataFrame({"month_id": [201801, 202001]})  # before and after 2019-06 entrant
    competitor_dates = [pd.Timestamp("2017-10-06"), pd.Timestamp("2019-06-01")]
    result = compute_fda_derived_features(monthly, competitor_dates=competitor_dates)

    before = result.loc[0]
    assert before["competitor_count_on_market"] == 1
    assert before["months_since_last_competitor_event"] == before["months_since_launch"]

    after = result.loc[1]
    assert after["competitor_count_on_market"] == 2
    # months since the *second* (more recent) competitor's approval, not the first.
    assert after["months_since_last_competitor_event"] == 7  # 2019-06 -> 2020-01
    assert after["months_since_launch"] == 27  # still relative to the earliest, 2017-10


# ---------- refresh_gold_visit_share_monthly / refresh_gold_segment_adoption ----------


def _seed_minimal_silver(engine):
    from oa_market_intelligence.warehouse.build_silver import (
        upsert_demographics,
        upsert_months,
        upsert_products,
        upsert_specialties,
    )

    upsert_months(engine, pd.Series([pd.Timestamp("2019-08-01"), pd.Timestamp("2019-09-01")]))
    upsert_specialties(engine, pd.Series(["ORTHOPEDIC SURGERY"]))
    upsert_demographics(engine, pd.DataFrame({"age_band": ["40 TO 59"], "gender": ["FEMALE"]}))
    upsert_products(
        engine,
        pd.DataFrame(
            [
                {
                    "product_name": "ZILRETTA",
                    "disease_area": "OA",
                    "manufacturer": "PACIRA PHARM",
                    "brand_generic_tag": "BRANDED GENERIC",
                    "treatment_category": "branded_injectable",
                    "fda_approval_date": "2017-10-06",
                },
                {
                    "product_name": "HYDROCORTISONE",
                    "disease_area": "OA",
                    "manufacturer": "VIATRIS",
                    "brand_generic_tag": "GENERIC",
                    "treatment_category": "generic_corticosteroid",
                    "fda_approval_date": None,
                },
            ]
        ),
    )
    from oa_market_intelligence.warehouse.build_silver import refresh_fact_product_visits

    visits = pd.DataFrame(
        [
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "OA",
                "manufacturer": "PACIRA PHARM",
                "product": "ZILRETTA",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 10,
            },
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "OA",
                "manufacturer": "VIATRIS",
                "product": "HYDROCORTISONE",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 90,
            },
            {
                "month": pd.Timestamp("2019-09-01"),
                "disease_area": "OA",
                "manufacturer": "PACIRA PHARM",
                "product": "ZILRETTA",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 20,
            },
            {
                "month": pd.Timestamp("2019-09-01"),
                "disease_area": "OA",
                "manufacturer": "VIATRIS",
                "product": "HYDROCORTISONE",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 80,
            },
        ]
    )
    refresh_fact_product_visits(engine, visits)


def test_refresh_gold_visit_share_monthly_writes_expected_rows(engine):
    _seed_minimal_silver(engine)
    n = refresh_gold_visit_share_monthly(engine)
    assert n == 2
    with engine.connect() as conn:
        row = (
            conn.execute(text("SELECT * FROM gold_visit_share_monthly WHERE month_id=201908"))
            .mappings()
            .first()
        )
    assert row["branded_injectable_visits"] == 10
    assert row["generic_corticosteroid_visits"] == 90
    assert row["visit_share"] == pytest.approx(0.1)
    assert row["predicted_direction"] is None


def test_refresh_gold_visit_share_monthly_full_refresh_overwrites(engine):
    _seed_minimal_silver(engine)
    refresh_gold_visit_share_monthly(engine)
    refresh_gold_visit_share_monthly(engine)  # rerun with identical Silver data
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM gold_visit_share_monthly")).scalar()
    assert n == 2  # not 4 - the old rows were replaced, not appended to


def test_refresh_gold_segment_adoption_excludes_zero_denominator_segments(engine):
    _seed_minimal_silver(engine)
    n = refresh_gold_segment_adoption(engine)
    assert n == 2  # one row per month; the single seeded segment has visits both months
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT month_id, branded_injectable_visits, total_category_visits, "
                "segment_visit_share, adoption_label FROM gold_segment_adoption ORDER BY month_id"
            )
        ).all()
    assert rows[0] == (201908, 10, 100, pytest.approx(0.1), None)
    assert rows[1] == (201909, 20, 100, pytest.approx(0.2), None)


# ---------- real-data integration ----------


@pytest.fixture(scope="module")
def real_data():
    oa_visits = parse_pivot_sheet(RAW / "Team1_M15_19_OA.xlsx")
    ra_visits = parse_pivot_sheet(RAW / "Team1_M04_RA.xlsx")
    visits = pd.concat([oa_visits, ra_visits], ignore_index=True)
    pos = pd.concat(
        [
            parse_place_of_service(RAW / "Team1_M15_19_OA.xlsx"),
            parse_place_of_service(RAW / "Team1_M04_RA.xlsx"),
        ],
        ignore_index=True,
    )
    reference = pd.concat(
        [
            parse_reference_table(RAW / "Branded Generic - OA.xlsx"),
            parse_reference_table(RAW / "Branded Generic - RA.xlsx"),
        ],
        ignore_index=True,
    )
    taxonomy = pd.read_csv(REFERENCE_DIR / "product_taxonomy.csv")
    return visits, reference, taxonomy, pos


def _fake_fda(name: str):
    from datetime import date

    return date(2017, 10, 6) if name == "ZILRETTA" else None


@pytest.fixture
def real_silver_engine(engine, real_data):
    visits, reference, taxonomy, pos = real_data
    build_silver(
        engine,
        visits=visits,
        reference_table=reference,
        taxonomy=taxonomy,
        place_of_service=pos,
        fetch_approval_date=_fake_fda,
    )
    return engine


def test_build_gold_end_to_end_against_real_data(real_silver_engine):
    summary = build_gold(real_silver_engine)
    assert (
        summary["gold_visit_share_monthly_rows"] == 72
    )  # Aug 2019 - Jul 2025, confirmed in Step 7

    with real_silver_engine.connect() as conn:
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly", conn)
        seg = pd.read_sql("SELECT * FROM gold_segment_adoption", conn)

    assert monthly["branded_injectable_visits"].sum() == 135_119  # PROPOSAL.md §18.1
    assert monthly["visit_share"].min() > 0.017
    assert monthly["visit_share"].max() < 0.034  # PROPOSAL.md §18.1's 1.7%-3.4% range

    # §18.2's documented degenerate finding at the default ±1.0pp threshold.
    counts = monthly["direction_label"].value_counts(dropna=False)
    assert counts.get("Up", 0) == 0
    assert counts.get("Down", 0) == 0
    assert counts.get("Flat", 0) == 71
    assert monthly["direction_label"].isna().sum() == 1

    # April 2020 COVID Office->Telehealth shock, confirmed in Step 3.
    april_2020 = monthly.set_index("month_id").loc[202004]
    assert april_2020["office_visits"] == 39_487
    assert april_2020["telehealth_visits"] == 1_016

    # §6.5's honest caveat: Zilretta-only means a constant competitor count.
    assert (monthly["competitor_count_on_market"] == 1).all()

    # segment_visit_share is always a valid ratio, never out of bounds.
    assert (seg["segment_visit_share"] >= 0).all()
    assert (seg["segment_visit_share"] <= 1).all()

    # gold_segment_adoption's per-month sums reconcile exactly to the monthly table's
    # own totals - no visits gained or lost between the two independent aggregations.
    seg_monthly_totals = seg.groupby("month_id")[
        ["branded_injectable_visits", "total_category_visits"]
    ].sum()
    monthly_totals = monthly.set_index("month_id")
    monthly_totals["total_category_visits"] = (
        monthly_totals["branded_injectable_visits"]
        + monthly_totals["generic_corticosteroid_visits"]
        + monthly_totals["nsaid_otc_visits"]
    )
    pd.testing.assert_series_equal(
        seg_monthly_totals["branded_injectable_visits"],
        monthly_totals.loc[seg_monthly_totals.index, "branded_injectable_visits"],
        check_names=False,
    )
    pd.testing.assert_series_equal(
        seg_monthly_totals["total_category_visits"],
        monthly_totals.loc[seg_monthly_totals.index, "total_category_visits"],
        check_names=False,
    )


def test_build_gold_is_idempotent_against_real_data(real_silver_engine):
    build_gold(real_silver_engine)
    with real_silver_engine.connect() as conn:
        first = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)

    build_gold(real_silver_engine)
    with real_silver_engine.connect() as conn:
        second = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)

    pd.testing.assert_frame_equal(first, second)
