"""Tests for the Silver warehouse builder.

Hand-built DataFrames exercise each function's logic in isolation, including the specific
bugs found while writing this module (the manufacturer-collapse in fact_product_visits,
and the unmapped-vs-genuinely-'unclassified' distinction in resolve_product_attributes).
A real-data integration test runs the whole thing end to end and checks it against figures
independently confirmed in prior steps (160 products, 5,546,090 total patient visits,
Zilretta's resolved attributes).
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select, text

from oa_market_intelligence.ingestion.nmta_loader import parse_pivot_sheet
from oa_market_intelligence.ingestion.place_of_service_loader import parse_place_of_service
from oa_market_intelligence.ingestion.reference_loader import parse_reference_table
from oa_market_intelligence.warehouse.build_silver import (
    build_silver,
    month_id,
    refresh_fact_place_of_service_visits,
    refresh_fact_product_visits,
    resolve_product_attributes,
    upsert_demographics,
    upsert_months,
    upsert_products,
    upsert_specialties,
)
from oa_market_intelligence.warehouse.schema import (
    create_schema,
    dim_demographics,
    dim_month,
    dim_product,
    dim_specialty,
)

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    create_schema(eng)
    return eng


# ---------- upsert_months ----------


def test_upsert_months_inserts_expected_rows(engine):
    months = pd.Series([pd.Timestamp("2019-08-01"), pd.Timestamp("2019-09-01")])
    upsert_months(engine, months)
    with engine.connect() as conn:
        rows = {r.month_id: r for r in conn.execute(select(dim_month)).all()}
    assert set(rows) == {201908, 201909}
    assert rows[201908].quarter == 3
    assert rows[201908].month_name == "August"
    assert rows[201909].quarter == 3


def test_upsert_months_is_idempotent_and_keys_stable(engine):
    months = pd.Series([pd.Timestamp("2019-08-01"), pd.Timestamp("2019-09-01")])
    upsert_months(engine, months)
    # Second call repeats one month and adds a new one - existing rows must not change.
    upsert_months(engine, pd.Series([pd.Timestamp("2019-09-01"), pd.Timestamp("2019-10-01")]))
    with engine.connect() as conn:
        rows = {r.month_id: r for r in conn.execute(select(dim_month)).all()}
    assert set(rows) == {201908, 201909, 201910}


def test_upsert_months_empty_series_is_a_noop(engine):
    upsert_months(engine, pd.Series([], dtype="datetime64[ns]"))
    with engine.connect() as conn:
        assert conn.execute(select(dim_month)).all() == []


# ---------- upsert_specialties / upsert_demographics ----------


def test_upsert_specialties_dedupes_and_is_idempotent(engine):
    upsert_specialties(engine, pd.Series(["ORTHOPEDIC SURGERY", "RHEUMATOLOGY", "RHEUMATOLOGY"]))
    upsert_specialties(engine, pd.Series(["RHEUMATOLOGY", "PAIN MANAGEMENT"]))
    with engine.connect() as conn:
        names = {r.specialty_name for r in conn.execute(select(dim_specialty)).all()}
    assert names == {"ORTHOPEDIC SURGERY", "RHEUMATOLOGY", "PAIN MANAGEMENT"}


def test_upsert_specialty_ids_stable_across_reruns(engine):
    upsert_specialties(engine, pd.Series(["ORTHOPEDIC SURGERY"]))
    with engine.connect() as conn:
        first_id = conn.execute(
            select(dim_specialty.c.specialty_id).where(
                dim_specialty.c.specialty_name == "ORTHOPEDIC SURGERY"
            )
        ).scalar_one()
    upsert_specialties(engine, pd.Series(["ORTHOPEDIC SURGERY", "RHEUMATOLOGY"]))
    with engine.connect() as conn:
        second_id = conn.execute(
            select(dim_specialty.c.specialty_id).where(
                dim_specialty.c.specialty_name == "ORTHOPEDIC SURGERY"
            )
        ).scalar_one()
    assert first_id == second_id


def test_upsert_demographics_dedupes_pairs_and_ignores_extra_columns(engine):
    demographics = pd.DataFrame(
        {
            "age_band": ["40 TO 59", "40 TO 59", "60 TO 64"],
            "gender": ["FEMALE", "FEMALE", "MALE"],
            "patient_visits": [1, 2, 3],  # extra column, must be ignored
        }
    )
    upsert_demographics(engine, demographics)
    with engine.connect() as conn:
        pairs = {(r.age_band, r.gender) for r in conn.execute(select(dim_demographics)).all()}
    assert pairs == {("40 TO 59", "FEMALE"), ("60 TO 64", "MALE")}


# ---------- resolve_product_attributes ----------


def _reference_row(product, manufacturer, tag, visits, disease_area="OA"):
    return {
        "disease_area": disease_area,
        "manufacturer": manufacturer,
        "product": product,
        "brand_generic_tag": tag,
        "icd10_code": "M15",
        "icd10_label": "Osteoarthritis",
        "patient_visits": visits,
    }


def test_resolve_product_attributes_picks_manufacturer_and_tag_by_most_visits():
    reference_table = pd.DataFrame(
        [
            _reference_row("HYDROCORTISONE", "VIATRIS", "GENERIC", 100),
            _reference_row("HYDROCORTISONE", "SOME OTHER MFR", "BRAND", 3),
        ]
    )
    taxonomy = pd.DataFrame(
        [
            {
                "product_name": "HYDROCORTISONE",
                "treatment_category": "generic_corticosteroid",
                "disease_area": "OA",
            },
        ]
    )
    attrs, unmapped = resolve_product_attributes(
        reference_table, taxonomy, fetch_approval_date=lambda name: None
    )
    row = attrs.set_index("product_name").loc["HYDROCORTISONE"]
    assert row["manufacturer"] == "VIATRIS"
    assert row["brand_generic_tag"] == "GENERIC"
    assert unmapped == []


def test_resolve_product_attributes_flags_genuinely_unmapped_product():
    reference_table = pd.DataFrame([_reference_row("NEW PRODUCT", "SOME MFR", "BRAND", 10)])
    taxonomy = pd.DataFrame(
        [
            {
                "product_name": "ZILRETTA",
                "treatment_category": "branded_injectable",
                "disease_area": "OA",
            },
        ]
    )
    attrs, unmapped = resolve_product_attributes(
        reference_table, taxonomy, fetch_approval_date=lambda name: None
    )
    assert unmapped == ["NEW PRODUCT"]
    assert (
        attrs.set_index("product_name").loc["NEW PRODUCT", "treatment_category"] == "unclassified"
    )


def test_resolve_product_attributes_does_not_flag_a_product_already_tagged_unclassified():
    # Regression test for the isin()-before-map() fix: a product genuinely present in the
    # taxonomy file with the recorded value 'unclassified' (already reviewed, no confident
    # category - e.g. MLK PROCEDUR F2) must NOT show up in `unmapped`, unlike a product
    # that is simply missing from the file entirely.
    reference_table = pd.DataFrame(
        [_reference_row("REVIEWED LONGTAIL ITEM", "SOME MFR", "OTHER", 1)]
    )
    taxonomy = pd.DataFrame(
        [
            {
                "product_name": "REVIEWED LONGTAIL ITEM",
                "treatment_category": "unclassified",
                "disease_area": "OA",
            },
        ]
    )
    attrs, unmapped = resolve_product_attributes(
        reference_table, taxonomy, fetch_approval_date=lambda name: None
    )
    assert unmapped == []
    assert (
        attrs.set_index("product_name").loc["REVIEWED LONGTAIL ITEM", "treatment_category"]
        == "unclassified"
    )


def test_resolve_product_attributes_only_fetches_approval_date_for_branded_injectable():
    reference_table = pd.DataFrame(
        [
            _reference_row("ZILRETTA", "PACIRA PHARM", "BRANDED GENERIC", 100),
            _reference_row("HYDROCORTISONE", "VIATRIS", "GENERIC", 100),
        ]
    )
    taxonomy = pd.DataFrame(
        [
            {
                "product_name": "ZILRETTA",
                "treatment_category": "branded_injectable",
                "disease_area": "OA",
            },
            {
                "product_name": "HYDROCORTISONE",
                "treatment_category": "generic_corticosteroid",
                "disease_area": "OA",
            },
        ]
    )
    fetched = []

    def fetch(name):
        fetched.append(name)
        return date(2017, 10, 6)

    attrs, _ = resolve_product_attributes(reference_table, taxonomy, fetch_approval_date=fetch)
    assert fetched == ["ZILRETTA"]
    assert attrs.set_index("product_name").loc["ZILRETTA", "fda_approval_date"] == date(2017, 10, 6)
    assert attrs.set_index("product_name").loc["HYDROCORTISONE", "fda_approval_date"] is None


# ---------- upsert_products (product_id stability across attribute changes) ----------


def test_upsert_products_preserves_product_id_when_attributes_change(engine):
    attrs = pd.DataFrame(
        [
            {
                "product_name": "ZILRETTA",
                "disease_area": "OA",
                "manufacturer": "PACIRA PHARM",
                "brand_generic_tag": "BRANDED GENERIC",
                "treatment_category": "unclassified",
                "fda_approval_date": None,
            }
        ]
    )
    upsert_products(engine, attrs)
    with engine.connect() as conn:
        first_id = conn.execute(
            select(dim_product.c.product_id).where(dim_product.c.product_name == "ZILRETTA")
        ).scalar_one()

    attrs.loc[0, "treatment_category"] = "branded_injectable"
    attrs.loc[0, "fda_approval_date"] = date(2017, 10, 6)
    upsert_products(engine, attrs)
    with engine.connect() as conn:
        row = (
            conn.execute(select(dim_product).where(dim_product.c.product_name == "ZILRETTA"))
            .mappings()
            .first()
        )

    assert row["product_id"] == first_id
    assert row["treatment_category"] == "branded_injectable"


# ---------- refresh_fact_product_visits ----------


def _seed_dims_for_fact(engine, product_name="ZILRETTA"):
    upsert_months(engine, pd.Series([pd.Timestamp("2019-08-01")]))
    upsert_specialties(engine, pd.Series(["ORTHOPEDIC SURGERY"]))
    upsert_demographics(engine, pd.DataFrame({"age_band": ["40 TO 59"], "gender": ["FEMALE"]}))
    upsert_products(
        engine,
        pd.DataFrame(
            [
                {
                    "product_name": product_name,
                    "disease_area": "OA",
                    "manufacturer": "PACIRA PHARM",
                    "brand_generic_tag": "BRANDED GENERIC",
                    "treatment_category": "branded_injectable",
                    "fda_approval_date": None,
                }
            ]
        ),
    )


def test_refresh_fact_product_visits_sums_across_manufacturer(engine):
    # This is the real bug found against production data: the same product/month/
    # specialty/demographic slice appears once per manufacturer in the raw pivot output,
    # but fact_product_visits's grain has no manufacturer column at all.
    _seed_dims_for_fact(engine)
    visits = pd.DataFrame(
        [
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "OA",
                "manufacturer": "MFR A",
                "product": "ZILRETTA",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 5,
            },
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "OA",
                "manufacturer": "MFR B",
                "product": "ZILRETTA",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 3,
            },
        ]
    )
    n = refresh_fact_product_visits(engine, visits)
    assert n == 1
    with engine.connect() as conn:
        total = conn.execute(text("SELECT SUM(patient_visits) FROM fact_product_visits")).scalar()
    assert total == 8


def test_refresh_fact_product_visits_full_refresh_overwrites_prior_rows(engine):
    _seed_dims_for_fact(engine)
    first = pd.DataFrame(
        [
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "OA",
                "manufacturer": "PACIRA PHARM",
                "product": "ZILRETTA",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 5,
            }
        ]
    )
    refresh_fact_product_visits(engine, first)

    second = pd.DataFrame(
        [
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "OA",
                "manufacturer": "PACIRA PHARM",
                "product": "ZILRETTA",
                "specialty": "ORTHOPEDIC SURGERY",
                "age_band": "40 TO 59",
                "gender": "FEMALE",
                "patient_visits": 9,
            }
        ]
    )
    n = refresh_fact_product_visits(engine, second)
    assert n == 1
    with engine.connect() as conn:
        total = conn.execute(text("SELECT SUM(patient_visits) FROM fact_product_visits")).scalar()
    assert total == 9  # not 14 - the old row was replaced, not added to


# ---------- refresh_fact_place_of_service_visits ----------


def test_refresh_fact_place_of_service_visits_excludes_ra(engine):
    upsert_months(engine, pd.Series([pd.Timestamp("2019-08-01")]))
    pos = pd.DataFrame(
        [
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "OA",
                "place_of_service": "OFFICE",
                "patient_visits": 10,
            },
            {
                "month": pd.Timestamp("2019-08-01"),
                "disease_area": "RA",
                "place_of_service": "OFFICE",
                "patient_visits": 999,
            },
        ]
    )
    n = refresh_fact_place_of_service_visits(engine, pos)
    assert n == 1
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT disease_area, patient_visits FROM fact_place_of_service_visits")
        ).all()
    assert rows == [("OA", 10)]


# ---------- month_id ----------


def test_month_id_is_deterministic_yyyymm():
    assert month_id(pd.Timestamp("2019-08-01")) == 201908
    assert month_id(pd.Timestamp("2025-07-15")) == 202507


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
    return date(2017, 10, 6) if name == "ZILRETTA" else None


def test_build_silver_end_to_end_against_real_data(engine, real_data):
    visits, reference, taxonomy, pos = real_data
    summary = build_silver(
        engine,
        visits=visits,
        reference_table=reference,
        taxonomy=taxonomy,
        place_of_service=pos,
        fetch_approval_date=_fake_fda,
    )

    assert summary["unmapped_products"] == []

    with engine.connect() as conn:
        counts = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in ["dim_month", "dim_product", "dim_specialty", "dim_demographics"]
        }
        total_visits = conn.execute(
            text("SELECT SUM(patient_visits) FROM fact_product_visits")
        ).scalar()
        ra_pos_rows = conn.execute(
            text("SELECT COUNT(*) FROM fact_place_of_service_visits WHERE disease_area='RA'")
        ).scalar()
        zilretta = (
            conn.execute(text("SELECT * FROM dim_product WHERE product_name='ZILRETTA'"))
            .mappings()
            .first()
        )

    assert counts["dim_month"] == 72  # Aug 2019 - Jul 2025 inclusive, confirmed both files
    assert counts["dim_product"] == 160  # 145 OA + 15 RA, confirmed in Step 4
    assert counts["dim_specialty"] == 50  # OA's 50; RA's 19 are a confirmed subset
    assert total_visits == visits["patient_visits"].sum()  # collapse loses nothing
    assert ra_pos_rows == 0

    assert zilretta["manufacturer"] == "PACIRA PHARM"
    assert zilretta["brand_generic_tag"] == "BRANDED GENERIC"
    assert zilretta["treatment_category"] == "branded_injectable"
    assert zilretta["fda_approval_date"] == "2017-10-06"


def test_build_silver_is_idempotent_against_real_data(engine, real_data):
    visits, reference, taxonomy, pos = real_data
    build_silver(
        engine,
        visits=visits,
        reference_table=reference,
        taxonomy=taxonomy,
        place_of_service=pos,
        fetch_approval_date=_fake_fda,
    )
    with engine.connect() as conn:
        product_ids_before = dict(
            conn.execute(text("SELECT product_name, product_id FROM dim_product")).all()
        )

    summary2 = build_silver(
        engine,
        visits=visits,
        reference_table=reference,
        taxonomy=taxonomy,
        place_of_service=pos,
        fetch_approval_date=_fake_fda,
    )
    with engine.connect() as conn:
        product_ids_after = dict(
            conn.execute(text("SELECT product_name, product_id FROM dim_product")).all()
        )
        fact_rows = conn.execute(text("SELECT COUNT(*) FROM fact_product_visits")).scalar()

    assert product_ids_before == product_ids_after
    assert fact_rows == summary2["fact_product_visits_rows"]
