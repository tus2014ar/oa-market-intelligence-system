"""SQLAlchemy Core table definitions for the Silver star schema and Gold serving tables.

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
    Float,
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


DIRECTION_LABELS = ("Up", "Down", "Flat")

gold_visit_share_monthly = Table(
    "gold_visit_share_monthly",
    metadata,
    Column("month_id", Integer, ForeignKey("dim_month.month_id"), primary_key=True),
    # category totals (database_schema.md §4.1, PROPOSAL.md §18.1)
    Column("branded_injectable_visits", Integer, nullable=False),
    Column("generic_corticosteroid_visits", Integer, nullable=False),
    Column("nsaid_otc_visits", Integer, nullable=False),
    # target
    Column("visit_share", Float, nullable=False),
    Column("direction_label", Text),  # nullable: the first month has no prior month
    # engineered features
    Column("visit_share_lag_1", Float),
    Column("visit_share_lag_2", Float),
    Column("visit_share_lag_3", Float),
    Column("visit_share_roll_3mo", Float),
    Column("visit_share_roll_6mo", Float),
    # FDA-derived features, Method B
    Column("months_since_launch", Integer),
    Column("is_post_launch", Integer),
    Column("competitor_count_on_market", Integer),
    Column("months_since_last_competitor_event", Integer),
    # market-level context, from fact_place_of_service_visits (OA only)
    Column("hospital_visits", Integer),
    Column("office_visits", Integer),
    Column("other_visits", Integer),
    Column("telehealth_visits", Integer),
    # model output - written by the (not-yet-built) modeling stage, never by
    # build_gold.py. See build_gold.py's module docstring for the rebuild-semantics
    # tension this creates with database_schema.md §6's full-refresh-overwrite rule.
    Column("predicted_direction", Text),
    Column("prediction_probability", Float),
    Column("model_version", Text),
    Column("actual_direction", Text),
    CheckConstraint("branded_injectable_visits >= 0", name="ck_gvsm_branded_nonneg"),
    CheckConstraint("generic_corticosteroid_visits >= 0", name="ck_gvsm_generic_nonneg"),
    CheckConstraint("nsaid_otc_visits >= 0", name="ck_gvsm_nsaid_nonneg"),
    CheckConstraint("direction_label IN ('Up','Down','Flat')", name="ck_gvsm_direction_label"),
    CheckConstraint("is_post_launch IN (0,1)", name="ck_gvsm_is_post_launch"),
    CheckConstraint(
        "predicted_direction IN ('Up','Down','Flat')", name="ck_gvsm_predicted_direction"
    ),
    CheckConstraint("actual_direction IN ('Up','Down','Flat')", name="ck_gvsm_actual_direction"),
)

gold_segment_adoption = Table(
    "gold_segment_adoption",
    metadata,
    Column("month_id", Integer, ForeignKey("dim_month.month_id"), nullable=False),
    Column("specialty_id", Integer, ForeignKey("dim_specialty.specialty_id"), nullable=False),
    Column(
        "demographic_id", Integer, ForeignKey("dim_demographics.demographic_id"), nullable=False
    ),
    Column("branded_injectable_visits", Integer, nullable=False),
    Column("total_category_visits", Integer, nullable=False),
    Column("segment_visit_share", Float, nullable=False),
    # Written by the Objective 3 (stretch) model once it runs, never by build_gold.py -
    # "High"/"Low" is a classification output, not a fixed rule (silver_gold_data_
    # dictionary.md §2.2).
    Column("adoption_label", Text),
    PrimaryKeyConstraint("month_id", "specialty_id", "demographic_id"),
    CheckConstraint("branded_injectable_visits >= 0", name="ck_gsa_branded_nonneg"),
    CheckConstraint("total_category_visits >= 0", name="ck_gsa_total_nonneg"),
    CheckConstraint("adoption_label IN ('High','Low')", name="ck_gsa_adoption_label"),
)


def create_schema(engine: Engine) -> None:
    """Create every Silver table that doesn't already exist. Never drops or alters one
    that does — safe to call at the start of every monthly run."""
    metadata.create_all(engine, checkfirst=True)
