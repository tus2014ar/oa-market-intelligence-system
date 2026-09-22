"""Builds/refreshes the Gold serving tables from the Silver star schema.

Per docs/database_schema.md §4 and §6: both Gold tables are truly full-refresh-
overwritten each cycle (DELETE + INSERT), the same as the fact tables, since nothing
depends on their keys staying stable across runs. Reads only from Silver tables - never
touches raw ingestion output directly (that's build_silver.py's job), and never touches
dim_month/dim_product/dim_specialty/dim_demographics.

Known, deliberately-unresolved tension with §6's full-refresh-overwrite rule: this
module always writes gold_visit_share_monthly's predicted_direction/
prediction_probability/model_version/actual_direction columns as NULL, per
silver_gold_data_dictionary.md §2.1 ("written by the modeling stage after a prediction
run" - not by this builder). But §6 says Gold tables are DELETE+INSERT'd wholesale on
every rebuild. No modeling stage exists yet (later phase), so this has never actually
been exercised - flagging it now rather than silently picking a resolution: once a
modeling stage writes real predictions into this table, a later Gold rebuild as
implemented today would silently wipe them, unless a future change either (a) runs the
modeling write-back strictly after each Gold rebuild every cycle, or (b) has this
module preserve those four columns for month_ids that already have a non-NULL value.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, delete, select

from oa_market_intelligence.warehouse.schema import (
    dim_product,
    fact_place_of_service_visits,
    fact_product_visits,
    gold_segment_adoption,
    gold_visit_share_monthly,
)

# The §18.1 three-category visit-share denominator. Every other treatment_category
# (opioid_other, not_applicable, unclassified) is deliberately excluded from both Gold
# tables' share calculations - not just the monthly one.
CATEGORY_COLUMNS = {
    "branded_injectable": "branded_injectable_visits",
    "generic_corticosteroid": "generic_corticosteroid_visits",
    "nsaid_otc": "nsaid_otc_visits",
}
SHARE_CATEGORIES = tuple(CATEGORY_COLUMNS)

DEFAULT_DIRECTION_THRESHOLD_PP = 1.0
# PROPOSAL.md §18.2's stated default. Confirmed degenerate against the real 72-month
# series (0 Up, 0 Down out of 71 month-over-month changes) - the replacement value is
# an open decision explicitly deferred to the modeling phase, not decided here.
# Exposed as a parameter so that later decision can be applied without editing this
# module again.

POS_COLUMN_MAP = {
    "HOSPITAL": "hospital_visits",
    "OFFICE": "office_visits",
    "OTHER": "other_visits",
    "TELEHEALTH": "telehealth_visits",
}


def _month_id_to_timestamp(month_id_value: int) -> pd.Timestamp:
    year, month = divmod(int(month_id_value), 100)
    return pd.Timestamp(year=year, month=month, day=1)


def _month_diff(later: pd.Timestamp, earlier: pd.Timestamp) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _load_product_visits_by_category(engine: Engine) -> pd.DataFrame:
    """One row per (month, specialty, demographic, product-visit), each tagged with its
    product's treatment_category - the base table both Gold tables aggregate further."""
    stmt = (
        select(
            fact_product_visits.c.month_id,
            fact_product_visits.c.specialty_id,
            fact_product_visits.c.demographic_id,
            dim_product.c.treatment_category,
            fact_product_visits.c.patient_visits,
        )
        .select_from(fact_product_visits)
        .join(dim_product, fact_product_visits.c.product_id == dim_product.c.product_id)
    )
    with engine.connect() as conn:
        return pd.read_sql(stmt, conn)


def _category_totals_by_month(visits_by_category: pd.DataFrame) -> pd.DataFrame:
    """One row per month: total visits in each of the three §18.1 categories."""
    totals = (
        visits_by_category.groupby(["month_id", "treatment_category"])["patient_visits"]
        .sum()
        .unstack("treatment_category", fill_value=0)
    )
    for category in SHARE_CATEGORIES:
        if category not in totals.columns:
            totals[category] = 0
    totals = totals[list(SHARE_CATEGORIES)].rename(columns=CATEGORY_COLUMNS)
    return totals.reset_index()


def compute_visit_share(totals: pd.DataFrame) -> pd.DataFrame:
    """Adds `visit_share` per §18.1: branded / (branded + generic + nsaid). Every real
    month in the current data has a positive denominator, but a future all-zero month
    would divide to NaN rather than a misleading 0 - left as NaN (-> NULL on write) as a
    signal something upstream needs investigating, not silently hidden."""
    totals = totals.copy()
    denominator = totals[list(CATEGORY_COLUMNS.values())].sum(axis=1)
    totals["visit_share"] = totals["branded_injectable_visits"] / denominator
    return totals


def compute_direction_label(
    monthly: pd.DataFrame, threshold_pp: float = DEFAULT_DIRECTION_THRESHOLD_PP
) -> pd.DataFrame:
    """Adds `direction_label` per §18.2: Up/Down/Flat on the month-over-month change in
    `visit_share`, in percentage points. `threshold_pp` is exclusive for Up/Down (a
    change of exactly the threshold is Flat, matching §18.2's stated wording: "Up:
    change > +1.0pp"). The first month has no prior month and gets NULL, not a guessed
    label."""
    monthly = monthly.sort_values("month_id").reset_index(drop=True)
    change_pp = monthly["visit_share"].diff() * 100
    monthly["direction_label"] = None
    monthly.loc[change_pp > threshold_pp, "direction_label"] = "Up"
    monthly.loc[change_pp < -threshold_pp, "direction_label"] = "Down"
    monthly.loc[
        change_pp.notna() & change_pp.between(-threshold_pp, threshold_pp), "direction_label"
    ] = "Flat"
    return monthly


def compute_lag_and_rolling_features(monthly: pd.DataFrame) -> pd.DataFrame:
    """Adds the lag-1/2/3 and 3/6-month rolling-average engineered features
    (data_analysis_reference.md §4.1), the single highest-priority predictors for the
    core classifier. Rolling windows include the current month (pandas' own
    `.rolling(n).mean()` convention) - the first n-1 months of each window are NaN
    (-> NULL on write), not backfilled, since there's no real data to average yet."""
    monthly = monthly.sort_values("month_id").reset_index(drop=True)
    share = monthly["visit_share"]
    monthly["visit_share_lag_1"] = share.shift(1)
    monthly["visit_share_lag_2"] = share.shift(2)
    monthly["visit_share_lag_3"] = share.shift(3)
    monthly["visit_share_roll_3mo"] = share.rolling(window=3).mean()
    monthly["visit_share_roll_6mo"] = share.rolling(window=6).mean()
    return monthly


def _competitor_approval_dates(engine: Engine) -> list[pd.Timestamp]:
    """Sorted approval dates of every no-generic-equivalent branded product that
    actually has one on file (today: Zilretta only - database_schema.md §3.1,
    data_analysis_reference.md §6.2). General over all dim_product rows tagged
    branded_injectable, not hardcoded to Zilretta, so a future second branded_
    injectable product with a queried approval date is picked up automatically."""
    stmt = select(dim_product.c.fda_approval_date).where(
        dim_product.c.treatment_category == "branded_injectable",
        dim_product.c.fda_approval_date.is_not(None),
    )
    with engine.connect() as conn:
        raw_dates = pd.read_sql(stmt, conn)["fda_approval_date"]
    return sorted(pd.to_datetime(raw_dates))


def compute_fda_derived_features(
    monthly: pd.DataFrame, competitor_dates: list[pd.Timestamp]
) -> pd.DataFrame:
    """Adds the four Method B features (data_analysis_reference.md §6.3), computed
    relative to `competitor_dates` - the known branded_injectable approval dates, not
    hardcoded to a single product:

    - months_since_launch: months since the *earliest* known branded_injectable
      approval - i.e. whether the category itself has launched by this month.
    - is_post_launch: 1 if months_since_launch >= 0, else 0.
    - competitor_count_on_market: count of known approvals on or before this month.
    - months_since_last_competitor_event: months since the most recent such approval.

    All four are NULL/0 when no branded_injectable product has a known approval date
    yet. With today's real data (Zilretta only, approved 2017-10-06, before the Aug
    2019 data window starts), months_since_launch and months_since_last_competitor_
    event are identical and grow by exactly 1 every month, and competitor_count_on_
    market is constant at 1 - the §6.5 "honest caveat" the docs already call out, not a
    bug in this function.
    """
    monthly = monthly.copy()
    months_since_launch: list[int | None] = []
    is_post_launch: list[int | None] = []
    competitor_count: list[int] = []
    months_since_last_event: list[int | None] = []

    for month_id_value in monthly["month_id"]:
        month_date = _month_id_to_timestamp(month_id_value)
        approved_so_far = [d for d in competitor_dates if d <= month_date]

        if competitor_dates:
            diff = _month_diff(month_date, competitor_dates[0])
            months_since_launch.append(diff)
            is_post_launch.append(1 if diff >= 0 else 0)
        else:
            months_since_launch.append(None)
            is_post_launch.append(None)

        competitor_count.append(len(approved_so_far))
        months_since_last_event.append(
            _month_diff(month_date, max(approved_so_far)) if approved_so_far else None
        )

    monthly["months_since_launch"] = months_since_launch
    monthly["is_post_launch"] = is_post_launch
    monthly["competitor_count_on_market"] = competitor_count
    monthly["months_since_last_competitor_event"] = months_since_last_event
    return monthly


def _place_of_service_pivot(engine: Engine) -> pd.DataFrame:
    """month_id -> hospital/office/other/telehealth visit counts. fact_place_of_service_
    visits only ever holds OA rows (build_silver.py's own documented scope decision), so
    no disease_area filter is needed here - it's already enforced upstream."""
    stmt = select(
        fact_place_of_service_visits.c.month_id,
        fact_place_of_service_visits.c.place_of_service,
        fact_place_of_service_visits.c.patient_visits,
    )
    with engine.connect() as conn:
        pos = pd.read_sql(stmt, conn)
    pivot = pos.pivot_table(
        index="month_id", columns="place_of_service", values="patient_visits", fill_value=0
    )
    pivot = pivot.rename(columns=POS_COLUMN_MAP)
    for col in POS_COLUMN_MAP.values():
        if col not in pivot.columns:
            pivot[col] = 0
    return pivot[list(POS_COLUMN_MAP.values())].reset_index()


def refresh_gold_visit_share_monthly(
    engine: Engine, direction_threshold_pp: float = DEFAULT_DIRECTION_THRESHOLD_PP
) -> int:
    """Full-refresh-overwrite (database_schema.md §6). See this module's docstring for
    the known tension this creates with the model-output columns, which are always
    written NULL here."""
    visits_by_category = _load_product_visits_by_category(engine)
    monthly = _category_totals_by_month(visits_by_category)
    monthly = compute_visit_share(monthly)
    monthly = compute_direction_label(monthly, direction_threshold_pp)
    monthly = compute_lag_and_rolling_features(monthly)
    monthly = compute_fda_derived_features(monthly, _competitor_approval_dates(engine))

    monthly = monthly.merge(_place_of_service_pivot(engine), on="month_id", how="left")

    monthly["predicted_direction"] = None
    monthly["prediction_probability"] = None
    monthly["model_version"] = None
    monthly["actual_direction"] = None

    monthly = monthly.astype(object).where(monthly.notna(), None)
    rows = monthly.to_dict("records")

    with engine.begin() as conn:
        conn.execute(delete(gold_visit_share_monthly))
        if rows:
            conn.execute(gold_visit_share_monthly.insert(), rows)
    return len(rows)


def refresh_gold_segment_adoption(engine: Engine) -> int:
    """Full-refresh-overwrite (database_schema.md §6). A (month, specialty,
    demographic) combination with zero visits across all three §18.1 categories has an
    undefined share, not a zero one - excluded rather than stored, since
    segment_visit_share is NOT NULL in the schema and storing a placeholder 0 would
    misrepresent "no data" as "no adoption." `adoption_label` is always written NULL -
    it's a classification output the (not-yet-built) Objective 3 model writes later,
    not a fixed rule this builder computes (silver_gold_data_dictionary.md §2.2)."""
    visits_by_category = _load_product_visits_by_category(engine)
    grouped = (
        visits_by_category.groupby(
            ["month_id", "specialty_id", "demographic_id", "treatment_category"]
        )["patient_visits"]
        .sum()
        .unstack("treatment_category", fill_value=0)
    )
    for category in SHARE_CATEGORIES:
        if category not in grouped.columns:
            grouped[category] = 0
    grouped = grouped[list(SHARE_CATEGORIES)].rename(columns=CATEGORY_COLUMNS).reset_index()

    grouped["total_category_visits"] = grouped[list(CATEGORY_COLUMNS.values())].sum(axis=1)
    grouped = grouped[grouped["total_category_visits"] > 0].copy()
    grouped["segment_visit_share"] = (
        grouped["branded_injectable_visits"] / grouped["total_category_visits"]
    )
    grouped["adoption_label"] = None

    rows = grouped[
        [
            "month_id",
            "specialty_id",
            "demographic_id",
            "branded_injectable_visits",
            "total_category_visits",
            "segment_visit_share",
            "adoption_label",
        ]
    ].to_dict("records")

    with engine.begin() as conn:
        conn.execute(delete(gold_segment_adoption))
        if rows:
            conn.execute(gold_segment_adoption.insert(), rows)
    return len(rows)


def build_gold(
    engine: Engine, *, direction_threshold_pp: float = DEFAULT_DIRECTION_THRESHOLD_PP
) -> dict:
    """Rebuilds both Gold tables from whatever is currently in the Silver tables.
    Order doesn't matter between the two - they're independent reads of Silver, unlike
    build_silver.py's dimension-then-fact ordering."""
    return {
        "gold_visit_share_monthly_rows": refresh_gold_visit_share_monthly(
            engine, direction_threshold_pp
        ),
        "gold_segment_adoption_rows": refresh_gold_segment_adoption(engine),
    }
