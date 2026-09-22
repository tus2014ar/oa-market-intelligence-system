"""Builds/refreshes the Silver star schema from validated ingestion output.

Per docs/database_schema.md §6: dimension tables are upserted (existing surrogate keys
stay stable — gold_segment_adoption's own primary key depends on them), then fact tables
are full-refresh-overwritten, since nothing depends on their keys staying stable across
runs. Callers pass already-validated DataFrames (ingestion/validation.py) — this module
only builds the star schema from clean data, it does not parse or validate raw extracts.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import date

import pandas as pd
from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from oa_market_intelligence.ingestion.openfda_client import earliest_approval_date
from oa_market_intelligence.warehouse.schema import (
    dim_demographics,
    dim_month,
    dim_product,
    dim_specialty,
    fact_place_of_service_visits,
    fact_product_visits,
)

BRANDED_INJECTABLE_CATEGORY = "branded_injectable"
UNCLASSIFIED = "unclassified"

FdaLookupFn = Callable[[str], date | None]


def _month_row(month: pd.Timestamp) -> dict:
    return {
        "month_id": month.year * 100 + month.month,
        "calendar_date": month.strftime("%Y-%m-%d"),
        "year": month.year,
        "quarter": (month.month - 1) // 3 + 1,
        "month_number": month.month,
        "month_name": calendar.month_name[month.month],
    }


def month_id(month: pd.Timestamp) -> int:
    """The deterministic dim_month key for a given month — exposed so callers building
    fact rows can compute it without a round trip to the database."""
    return month.year * 100 + month.month


def upsert_months(engine: Engine, months: pd.Series) -> None:
    rows = [_month_row(m) for m in pd.Series(months).drop_duplicates()]
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(
            sqlite_insert(dim_month).on_conflict_do_nothing(index_elements=["month_id"]), rows
        )


def upsert_specialties(engine: Engine, specialty_names: pd.Series) -> None:
    rows = [{"specialty_name": s} for s in pd.Series(specialty_names).drop_duplicates()]
    if not rows:
        return
    with engine.begin() as conn:
        stmt = sqlite_insert(dim_specialty).on_conflict_do_nothing(
            index_elements=["specialty_name"]
        )
        conn.execute(stmt, rows)


def upsert_demographics(engine: Engine, demographics: pd.DataFrame) -> None:
    """`demographics` needs `age_band` and `gender` columns; extra columns are ignored."""
    pairs = demographics[["age_band", "gender"]].drop_duplicates()
    rows = pairs.to_dict("records")
    if not rows:
        return
    with engine.begin() as conn:
        stmt = sqlite_insert(dim_demographics).on_conflict_do_nothing(
            index_elements=["age_band", "gender"]
        )
        conn.execute(stmt, rows)


def _resolve_by_most_visits(reference_table: pd.DataFrame, value_col: str) -> pd.Series:
    """For each product, the `value_col` value associated with the most total visits
    across the reference table's rows — used for both manufacturer and
    brand_generic_tag, independently (database_schema.md §3.1's dim_product note)."""
    totals = reference_table.groupby(["product", value_col])["patient_visits"].sum()
    winners = totals.groupby(level="product").idxmax()
    return winners.map(lambda pair: pair[1])


def resolve_product_attributes(
    reference_table: pd.DataFrame,
    taxonomy: pd.DataFrame,
    fetch_approval_date: FdaLookupFn = earliest_approval_date,
) -> tuple[pd.DataFrame, list[str]]:
    """One row per product: manufacturer and brand_generic_tag resolved by most-visits,
    treatment_category from `taxonomy` (falling back to 'unclassified', never guessed —
    PROPOSAL.md §18.10), fda_approval_date populated only for treatment_category ==
    'branded_injectable' (decided: Zilretta only today, data_analysis_reference.md §6.2).

    Returns (attributes, unmapped_product_names) — the caller logs/alerts on the second
    part; this function only detects the gap; the monitoring channel (§17.4) is later.
    """
    disease_area = reference_table.groupby("product")["disease_area"].first()
    manufacturer = _resolve_by_most_visits(reference_table, "manufacturer")
    brand_generic_tag = _resolve_by_most_visits(reference_table, "brand_generic_tag")

    attrs = pd.DataFrame(
        {
            "product_name": disease_area.index,
            "disease_area": disease_area.values,
            "manufacturer": manufacturer.reindex(disease_area.index).values,
            "brand_generic_tag": brand_generic_tag.reindex(disease_area.index).values,
        }
    )

    taxonomy_map = taxonomy.set_index("product_name")["treatment_category"]
    in_taxonomy = attrs["product_name"].isin(taxonomy_map.index)
    # A product genuinely absent from the taxonomy file is flagged as unmapped. A
    # product present *with* the value 'unclassified' (e.g. MLK PROCEDUR F2, a
    # deliberately reviewed Category E long-tail item, data_analysis_reference.md §3.1)
    # is not - it was already reviewed, it just has no confident category. Mapping via
    # .fillna() alone would conflate these two cases, since both produce the same
    # string; checking membership first, before mapping, keeps them distinct.
    attrs["treatment_category"] = attrs["product_name"].map(taxonomy_map)
    attrs.loc[~in_taxonomy, "treatment_category"] = UNCLASSIFIED
    unmapped = sorted(attrs.loc[~in_taxonomy, "product_name"])

    is_branded_injectable = attrs["treatment_category"] == BRANDED_INJECTABLE_CATEGORY
    attrs["fda_approval_date"] = None
    attrs.loc[is_branded_injectable, "fda_approval_date"] = attrs.loc[
        is_branded_injectable, "product_name"
    ].map(fetch_approval_date)

    return attrs, unmapped


def upsert_products(engine: Engine, product_attrs: pd.DataFrame) -> None:
    attrs = product_attrs.astype(object).where(product_attrs.notna(), None)
    # dim_product.fda_approval_date is TEXT (an ISO date string, per schema.py), but
    # resolve_product_attributes fills it with real date objects, matching
    # openfda_client's own return type. Stringify explicitly here, at the DB-write
    # boundary, rather than relying on sqlite3's default date adapter to do it
    # implicitly - that adapter is deprecated as of Python 3.12 and due for removal.
    if "fda_approval_date" in attrs.columns:
        attrs["fda_approval_date"] = attrs["fda_approval_date"].map(
            lambda d: d.isoformat() if isinstance(d, date) else d
        )
    rows = attrs.to_dict("records")
    if not rows:
        return
    update_cols = [
        "manufacturer",
        "brand_generic_tag",
        "disease_area",
        "treatment_category",
        "fda_approval_date",
    ]
    with engine.begin() as conn:
        for row in rows:
            stmt = sqlite_insert(dim_product).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=["product_name"],
                set_={col: stmt.excluded[col] for col in update_cols},
            )
            conn.execute(stmt)


def _key_map(engine: Engine, table, key_col: str, name_cols: list[str]) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(select(*[table.c[c] for c in [key_col, *name_cols]])).all()
    if len(name_cols) == 1:
        return {getattr(r, name_cols[0]): getattr(r, key_col) for r in rows}
    return {tuple(getattr(r, c) for c in name_cols): getattr(r, key_col) for r in rows}


def refresh_fact_product_visits(engine: Engine, visits: pd.DataFrame) -> int:
    """Full-refresh-overwrite: replaces every row, since nothing depends on this table's
    own key staying stable across runs (database_schema.md §6).

    fact_product_visits's grain (month, product, specialty, demographic) deliberately
    excludes manufacturer — dim_product resolves one manufacturer per product for display
    only (schema.py's comment: "never used for grouping/joins"). But a single product is
    routinely sold under several manufacturers within the same month/specialty/demographic
    slice (e.g. a generic with 13 manufacturers in one real slice), so the raw visit rows
    are only unique once manufacturer is included. Collapsing to the fact grain therefore
    requires summing across manufacturer, not just dropping the column — verified against
    real data that this sum is exact (total patient_visits unchanged: 5,546,090 before and
    after, for the full real OA+RA dataset)."""
    product_ids = _key_map(engine, dim_product, "product_id", ["product_name"])
    specialty_ids = _key_map(engine, dim_specialty, "specialty_id", ["specialty_name"])
    demographic_ids = _key_map(engine, dim_demographics, "demographic_id", ["age_band", "gender"])

    grouped = visits.groupby(
        ["month", "product", "specialty", "age_band", "gender"], as_index=False
    )["patient_visits"].sum()

    rows = [
        {
            "month_id": month_id(r.month),
            "product_id": product_ids[r.product],
            "specialty_id": specialty_ids[r.specialty],
            "demographic_id": demographic_ids[(r.age_band, r.gender)],
            "patient_visits": r.patient_visits,
        }
        for r in grouped.itertuples()
    ]
    with engine.begin() as conn:
        conn.execute(delete(fact_product_visits))
        if rows:
            conn.execute(fact_product_visits.insert(), rows)
    return len(rows)


def refresh_fact_place_of_service_visits(engine: Engine, place_of_service: pd.DataFrame) -> int:
    """OA only — decided, data_dictionary.md §3: RA's three Place-of-Service sources
    disagree by an order of magnitude and nothing in Core scope reads an RA figure yet.
    Enforced here, not just assumed of the caller."""
    oa_only = place_of_service[place_of_service["disease_area"] == "OA"]
    rows = [
        {
            "month_id": month_id(r.month),
            "disease_area": r.disease_area,
            "place_of_service": r.place_of_service,
            "patient_visits": r.patient_visits,
        }
        for r in oa_only.itertuples()
    ]
    with engine.begin() as conn:
        conn.execute(delete(fact_place_of_service_visits))
        if rows:
            conn.execute(fact_place_of_service_visits.insert(), rows)
    return len(rows)


def build_silver(
    engine: Engine,
    *,
    visits: pd.DataFrame,
    reference_table: pd.DataFrame,
    taxonomy: pd.DataFrame,
    place_of_service: pd.DataFrame,
    fetch_approval_date: FdaLookupFn = earliest_approval_date,
) -> dict:
    """Upserts all 4 dimension tables, then full-refresh-overwrites both fact tables, in
    that order (facts reference dimension keys that must exist first).

    Returns a summary including `unmapped_products`, so a caller can log or alert on a
    product the taxonomy file doesn't recognize yet, without this function owning that
    channel itself (§17.4 is separate, later infrastructure).
    """
    upsert_months(engine, pd.concat([visits["month"], place_of_service["month"]]))
    upsert_specialties(engine, visits["specialty"])
    upsert_demographics(engine, visits)

    product_attrs, unmapped = resolve_product_attributes(
        reference_table, taxonomy, fetch_approval_date
    )
    upsert_products(engine, product_attrs)

    fact_rows = refresh_fact_product_visits(engine, visits)
    pos_rows = refresh_fact_place_of_service_visits(engine, place_of_service)

    return {
        "unmapped_products": unmapped,
        "fact_product_visits_rows": fact_rows,
        "fact_place_of_service_visits_rows": pos_rows,
    }
