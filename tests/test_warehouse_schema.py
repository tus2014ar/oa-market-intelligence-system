"""Tests for the Silver star schema's table definitions.

Runs against a real in-memory SQLite engine — this is DDL, so the thing worth testing is
that SQLite actually enforces what the CHECK constraints claim to enforce, not just that
the Python objects exist.
"""

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from oa_market_intelligence.warehouse.schema import (
    create_schema,
    dim_demographics,
    dim_month,
    dim_product,
    dim_specialty,
    fact_place_of_service_visits,
    fact_product_visits,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    create_schema(eng)
    return eng


def test_create_schema_creates_all_six_tables(engine):
    from sqlalchemy import inspect

    tables = set(inspect(engine).get_table_names())
    assert tables == {
        "dim_month",
        "dim_product",
        "dim_specialty",
        "dim_demographics",
        "fact_product_visits",
        "fact_place_of_service_visits",
    }


def test_create_schema_is_idempotent(engine):
    # Calling it again on an already-populated database must not drop/alter anything -
    # this is what makes it safe to call at the start of every monthly run.
    with engine.begin() as conn:
        conn.execute(insert(dim_month).values(
            month_id=201908, calendar_date="2019-08-01", year=2019, quarter=3,
            month_number=8, month_name="August",
        ))
    create_schema(engine)
    with engine.connect() as conn:
        rows = conn.execute(select(dim_month)).all()
    assert len(rows) == 1


def test_dim_month_rejects_bad_quarter(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_month).values(
                month_id=201908, calendar_date="2019-08-01", year=2019, quarter=5,
                month_number=8, month_name="August",
            ))


def test_dim_month_rejects_bad_month_number(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_month).values(
                month_id=201913, calendar_date="2019-13-01", year=2019, quarter=4,
                month_number=13, month_name="Undecember",
            ))


def test_dim_product_rejects_bad_brand_generic_tag(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_product).values(
                product_name="ZILRETTA", brand_generic_tag="NOT_A_REAL_TAG", disease_area="OA",
                treatment_category="branded_injectable",
            ))


def test_dim_product_rejects_bad_disease_area(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_product).values(
                product_name="ZILRETTA", brand_generic_tag="BRANDED GENERIC", disease_area="XX",
                treatment_category="branded_injectable",
            ))


def test_dim_product_rejects_bad_treatment_category(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_product).values(
                product_name="ZILRETTA", brand_generic_tag="BRANDED GENERIC", disease_area="OA",
                treatment_category="not_a_real_category",
            ))


def test_dim_product_name_is_unique(engine):
    with engine.begin() as conn:
        conn.execute(insert(dim_product).values(
            product_name="ZILRETTA", brand_generic_tag="BRANDED GENERIC", disease_area="OA",
            treatment_category="branded_injectable",
        ))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_product).values(
                product_name="ZILRETTA", brand_generic_tag="GENERIC", disease_area="OA",
                treatment_category="unclassified",
            ))


def test_dim_demographics_rejects_bad_gender(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_demographics).values(age_band="40 TO 59", gender="OTHER"))


def test_dim_demographics_age_band_gender_pair_is_unique(engine):
    with engine.begin() as conn:
        conn.execute(insert(dim_demographics).values(age_band="40 TO 59", gender="FEMALE"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(dim_demographics).values(age_band="40 TO 59", gender="FEMALE"))


def test_fact_product_visits_rejects_negative_visits(engine):
    _seed_one_fact_dependency_set(engine)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(fact_product_visits).values(
                month_id=201908, product_id=1, specialty_id=1, demographic_id=1,
                patient_visits=-1,
            ))


def test_fact_place_of_service_visits_rejects_bad_setting(engine):
    with engine.begin() as conn:
        conn.execute(insert(dim_month).values(
            month_id=201908, calendar_date="2019-08-01", year=2019, quarter=3,
            month_number=8, month_name="August",
        ))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(fact_place_of_service_visits).values(
                month_id=201908, disease_area="OA", place_of_service="PHARMACY", patient_visits=1,
            ))


def _seed_one_fact_dependency_set(engine) -> None:
    with engine.begin() as conn:
        conn.execute(insert(dim_month).values(
            month_id=201908, calendar_date="2019-08-01", year=2019, quarter=3,
            month_number=8, month_name="August",
        ))
        conn.execute(insert(dim_product).values(
            product_name="ZILRETTA", brand_generic_tag="BRANDED GENERIC", disease_area="OA",
            treatment_category="branded_injectable",
        ))
        conn.execute(insert(dim_specialty).values(specialty_name="ORTHOPEDIC SURGERY"))
        conn.execute(insert(dim_demographics).values(age_band="40 TO 59", gender="FEMALE"))
