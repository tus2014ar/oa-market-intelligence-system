"""SQLAlchemy Core table definitions for the Silver star schema.

Mirrors docs/database_schema.md exactly. Defined as Core Table/Column objects, not raw
SQL strings, so the schema itself is portable to Postgres via a connection-string change
(the documented upgrade path, §5.6 of PROPOSAL.md) if that migration ever happens. The
*upsert* logic in build_silver.py is not equally portable — it uses SQLite's own
ON CONFLICT clause and would need a dialect-specific swap on a Postgres migration.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

dim_month = Table(
    "dim_month",
    metadata,
    Column("month_id", Integer, primary_key=True),  # deterministic natural key, YYYYMM
    Column("calendar_date", Text, nullable=False),
    Column("year", Integer, nullable=False),
    Column("quarter", Integer, nullable=False),
    Column("month_number", Integer, nullable=False),
    Column("month_name", Text, nullable=False),
    CheckConstraint("quarter BETWEEN 1 AND 4", name="ck_dim_month_quarter"),
    CheckConstraint("month_number BETWEEN 1 AND 12", name="ck_dim_month_number"),
)

# 'not_applicable' and 'unclassified' added to PROPOSAL.md §18.10's taxonomy after
# database_schema.md's DDL was first written; that DDL never actually had a CHECK
# constraint on this column despite its comment listing valid values - added here.
TREATMENT_CATEGORIES = (
    "branded_injectable",
    "generic_corticosteroid",
    "nsaid_otc",
    "opioid_other",
    "not_applicable",
    "unclassified",
)

dim_product = Table(
    "dim_product",
    metadata,
    Column("product_id", Integer, primary_key=True, autoincrement=True),
    Column("product_name", Text, nullable=False, unique=True),
    Column("manufacturer", Text),  # informational only; never used for grouping/joins
    Column("brand_generic_tag", Text, nullable=False),
    Column("disease_area", Text, nullable=False),
    Column("treatment_category", Text, nullable=False, server_default="unclassified"),
    Column("fda_approval_date", Text),  # ISO date, nullable
    CheckConstraint(
        "brand_generic_tag IN ('BRAND','GENERIC','BRANDED GENERIC','OTHER')",
        name="ck_dim_product_tag",
    ),
    CheckConstraint("disease_area IN ('OA','RA')", name="ck_dim_product_disease_area"),
    CheckConstraint(
        "treatment_category IN ('branded_injectable','generic_corticosteroid',"
        "'nsaid_otc','opioid_other','not_applicable','unclassified')",
        name="ck_dim_product_category",
    ),
)

dim_specialty = Table(
    "dim_specialty",
    metadata,
    Column("specialty_id", Integer, primary_key=True, autoincrement=True),
    Column("specialty_name", Text, nullable=False, unique=True),
)

dim_demographics = Table(
    "dim_demographics",
    metadata,
    Column("demographic_id", Integer, primary_key=True, autoincrement=True),
    Column("age_band", Text, nullable=False),
    Column("gender", Text, nullable=False),
    CheckConstraint("gender IN ('MALE','FEMALE','UNSPECIFIED')", name="ck_dim_demo_gender"),
    UniqueConstraint("age_band", "gender", name="uq_dim_demographics"),
)

fact_product_visits = Table(
    "fact_product_visits",
    metadata,
    Column("month_id", Integer, ForeignKey("dim_month.month_id"), nullable=False),
    Column("product_id", Integer, ForeignKey("dim_product.product_id"), nullable=False),
    Column("specialty_id", Integer, ForeignKey("dim_specialty.specialty_id"), nullable=False),
    Column(
        "demographic_id", Integer, ForeignKey("dim_demographics.demographic_id"), nullable=False
    ),
    Column("patient_visits", Integer, nullable=False),
    PrimaryKeyConstraint("month_id", "product_id", "specialty_id", "demographic_id"),
    CheckConstraint("patient_visits >= 0", name="ck_fact_product_visits_nonneg"),
)

fact_place_of_service_visits = Table(
    "fact_place_of_service_visits",
    metadata,
    Column("month_id", Integer, ForeignKey("dim_month.month_id"), nullable=False),
    Column("disease_area", Text, nullable=False),
    Column("place_of_service", Text, nullable=False),
    Column("patient_visits", Integer, nullable=False),
    PrimaryKeyConstraint("month_id", "disease_area", "place_of_service"),
    CheckConstraint("disease_area IN ('OA','RA')", name="ck_fact_pos_disease_area"),
    CheckConstraint(
        "place_of_service IN ('HOSPITAL','OFFICE','OTHER','TELEHEALTH')",
        name="ck_fact_pos_setting",
    ),
    CheckConstraint("patient_visits >= 0", name="ck_fact_pos_visits_nonneg"),
)


def create_schema(engine: Engine) -> None:
    """Create every Silver table that doesn't already exist. Never drops or alters one
    that does — safe to call at the start of every monthly run."""
    metadata.create_all(engine, checkfirst=True)
