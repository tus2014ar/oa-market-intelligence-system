"""Schema of the external-data database (`data/processed/external.db`, DL-59).

A second set of dimensions and facts for the public datasets, kept in its own SQLite file with its
own metadata so the IQVIA warehouse (`warehouse/schema.py`) can never be altered by it. Tables join
to the IQVIA warehouse by natural keys at analysis time: month (YYYYMM), specialty name and
two-letter state code. Every column is documented in docs/external_data_lineage.md; a test fails if
one is missing.

`PUBLISHED_TABLES` are small enough to commit inside the published database. `LOCAL_ONLY_TABLES`
stay on this machine: the NPI-level fact, the county-level prevalence, the raw taxonomy text (AMA
copyright) and the run log.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Engine,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
)

external_metadata = MetaData()

CODE_GROUPS = (
    "A_primary",
    "B_context_hyaluronic",
    "C_denominator_procedures",
    "D_sensitivity_iv_steroids",
)
RECIPIENT_TYPES = ("physician", "non_physician_practitioner", "teaching_hospital")
VERDICTS = (
    "agrees",
    "partial",
    "disagrees",
    "consistent",
    "inconsistent",
    "supported",
    "not_supported",
    "usable",
    "unstable",
    "not_run",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _rate(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR {column} BETWEEN 0 AND 1", name=f"ck_{column}_rate"
    )


def _table(name: str, *items) -> Table:
    return Table(name, external_metadata, *items)


# ---------------------------------------------------------------- dimensions and bridges

dim_state = _table(
    "dim_state",
    Column("state_code", Text, primary_key=True),
    Column("state_name", Text, nullable=False),
    Column("state_fips", Text),
    Column("is_us_state_or_dc", Integer, nullable=False),
    CheckConstraint("length(state_code) = 2", name="ck_dim_state_code"),
    CheckConstraint("is_us_state_or_dc IN (0, 1)", name="ck_dim_state_flag"),
)

dim_year = _table("dim_year", Column("year", Integer, primary_key=True))

dim_quarter = _table(
    "dim_quarter",
    Column("quarter_id", Text, primary_key=True),
    Column("year", Integer, nullable=False),
    Column("quarter", Integer, nullable=False),
    Column("first_month_id", Integer, nullable=False),
    Column("last_month_id", Integer, nullable=False),
    CheckConstraint("quarter BETWEEN 1 AND 4", name="ck_dim_quarter_number"),
)

dim_hcpcs_code = _table(
    "dim_hcpcs_code",
    Column("hcpcs_code", Text, primary_key=True),
    Column("code_group", Text, nullable=False),
    Column("drug_family", Text, nullable=False),
    Column("is_cpt", Integer, nullable=False),
    Column("short_description", Text),
    Column("first_year_seen", Integer),
    Column("last_year_seen", Integer),
    CheckConstraint(_in("code_group", CODE_GROUPS), name="ck_dim_hcpcs_group"),
    CheckConstraint("is_cpt IN (0, 1)", name="ck_dim_hcpcs_cpt_flag"),
    # CPT code descriptions are AMA copyright: never stored.
    CheckConstraint("is_cpt = 0 OR short_description IS NULL", name="ck_dim_hcpcs_no_cpt_text"),
)

dim_event = _table(
    "dim_event",
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("event_date", Text, nullable=False),
    Column("event_end_date", Text),
    Column("event_type", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("verified", Integer, nullable=False),
    CheckConstraint("verified IN (0, 1)", name="ck_dim_event_verified"),
)

bridge_specialty_crosswalk = _table(
    "bridge_specialty_crosswalk",
    Column("iqvia_group", Text, primary_key=True),
    Column("medicare_name", Text, primary_key=True),
    Column("relationship", Text, nullable=False),
    Column("note", Text),
    CheckConstraint(
        _in("relationship", ("exact", "combined", "weak", "excluded")),
        name="ck_crosswalk_relationship",
    ),
)

bridge_drug_family = _table(
    "bridge_drug_family",
    Column("drug_family", Text, primary_key=True),
    Column("iqvia_product_name", Text, primary_key=True),
)

bridge_taxonomy_specialty = _table(
    "bridge_taxonomy_specialty",
    Column("taxonomy_code", Text, primary_key=True),
    Column("specialty_group", Text, nullable=False),
    Column("medicare_name", Text),
)

src_nucc_taxonomy = _table(
    "src_nucc_taxonomy",
    Column("code", Text, primary_key=True),
    Column("grouping", Text),
    Column("classification", Text),
    Column("specialization", Text),
    Column("display_name", Text),
)

bronze_external_files = _table(
    "bronze_external_files",
    Column("file_id", Integer, primary_key=True, autoincrement=True),
    Column("dataset", Text, nullable=False),
    Column("relative_path", Text, nullable=False, unique=True),
    Column("source_url", Text),
    Column("bytes", Integer),
    Column("sha256", Text),
    Column("status", Text),
    Column("downloaded_at", Text),
    Column("rows_loaded", Integer),
    Column("loaded_at", Text),
)

external_load_runs = _table(
    "external_load_runs",
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("source", Text, nullable=False),
    Column("data_year", Integer),
    Column("rows_read", Integer),
    Column("rows_loaded", Integer),
    Column("status", Text, nullable=False),
    Column("started_at", Text),
    Column("finished_at", Text),
    Column("note", Text),
    CheckConstraint(_in("status", ("ok", "failed")), name="ck_external_load_status"),
)

# ---------------------------------------------------------------- facts

_AMOUNTS = ("avg_submitted_charge", "avg_allowed_amt", "avg_payment_amt", "avg_standardized_amt")

fact_ext_partb_provider = _table(
    "fact_ext_partb_provider",
    Column("year", Integer, primary_key=True),
    Column("npi", Text, primary_key=True),
    Column("hcpcs_code", Text, primary_key=True),
    Column("setting", Text, primary_key=True),
    Column("specialty_cms", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("state_code", Text),
    Column("medicare_participating", Text),
    Column("benes", Integer),
    Column("services", Float),
    Column("bene_day_services", Integer),
    *(Column(a, Float) for a in _AMOUNTS),
    CheckConstraint("setting IN ('O', 'F')", name="ck_partb_provider_setting"),
    CheckConstraint("entity_type IN ('I', 'O')", name="ck_partb_provider_entity"),
)

fact_ext_partb_geo = _table(
    "fact_ext_partb_geo",
    Column("year", Integer, primary_key=True),
    Column("geo_level", Text, primary_key=True),
    Column("geo_code", Text, primary_key=True),
    Column("hcpcs_code", Text, primary_key=True),
    Column("setting", Text, primary_key=True),
    Column("geo_desc", Text),
    Column("drug_indicator", Text),
    Column("n_providers", Integer),
    Column("benes", Integer),
    Column("services", Float),
    Column("bene_day_services", Integer),
    *(Column(a, Float) for a in _AMOUNTS),
    CheckConstraint("setting IN ('O', 'F')", name="ck_partb_geo_setting"),
    CheckConstraint("geo_level IN ('National', 'State')", name="ck_partb_geo_level"),
)

fact_ext_partd_geo = _table(
    "fact_ext_partd_geo",
    Column("year", Integer, primary_key=True),
    Column("geo_level", Text, primary_key=True),
    Column("geo_code", Text, primary_key=True),
    Column("brand_name", Text, primary_key=True),
    Column("generic_name", Text, primary_key=True),
    Column("drug_family", Text, nullable=False),
    Column("n_prescribers", Integer),
    Column("claims", Float),
    Column("fills_30day", Float),
    Column("total_drug_cost", Float),
    Column("benes", Integer),
    Column("ge65_claims", Float),
    Column("ge65_fills_30day", Float),
    Column("ge65_drug_cost", Float),
    Column("ge65_benes", Integer),
    Column("ge65_suppression_flag", Text),
    Column("ge65_bene_suppression_flag", Text),
    CheckConstraint(_in("drug_family", ("nsaid", "oral_steroid")), name="ck_partd_geo_family"),
    CheckConstraint("geo_level IN ('National', 'State')", name="ck_partd_geo_level"),
)

fact_ext_asp_price = _table(
    "fact_ext_asp_price",
    Column("quarter_id", Text, primary_key=True),
    Column("hcpcs_code", Text, primary_key=True),
    Column("short_description", Text),
    Column("dosage", Text),
    Column("payment_limit", Float),
    Column("coinsurance_pct", Float),
    Column("notes", Text),
    Column("source_file", Text, nullable=False),
    Column("source_release", Text),
    CheckConstraint("payment_limit IS NULL OR payment_limit >= 0", name="ck_asp_limit_nonneg"),
)

fact_ext_openpay_month = _table(
    "fact_ext_openpay_month",
    Column("month_id", Integer, primary_key=True),
    Column("product", Text, primary_key=True),
    Column("recipient_type", Text, primary_key=True),
    Column("n_records", Integer, nullable=False),
    Column("n_distinct_recipients", Integer, nullable=False),
    Column("total_amount_usd", Float, nullable=False),
    Column("n_payments_counted", Integer),
    Column("n_flagged_records", Integer, nullable=False),
    CheckConstraint(_in("recipient_type", RECIPIENT_TYPES), name="ck_openpay_month_recipient"),
)

fact_ext_openpay_nature = _table(
    "fact_ext_openpay_nature",
    Column("month_id", Integer, primary_key=True),
    Column("product", Text, primary_key=True),
    Column("recipient_type", Text, primary_key=True),
    Column("nature_of_payment", Text, primary_key=True),
    Column("n_records", Integer, nullable=False),
    Column("total_amount_usd", Float, nullable=False),
    CheckConstraint(_in("recipient_type", RECIPIENT_TYPES), name="ck_openpay_nature_recipient"),
)

fact_ext_company_revenue = _table(
    "fact_ext_company_revenue",
    Column("period_end", Text, primary_key=True),
    Column("company", Text, primary_key=True),
    Column("product", Text, primary_key=True),
    Column("period_type", Text, primary_key=True),
    Column("fiscal_label", Text),
    Column("net_sales_usd", Float),
    Column("source_accession", Text, nullable=False),
    Column("source_form", Text),
    Column("source_page", Text),
    Column("derived", Integer, nullable=False),
    Column("note", Text),
    CheckConstraint(
        _in("period_type", ("quarter", "nine_months", "year")), name="ck_revenue_period"
    ),
    CheckConstraint("derived IN (0, 1)", name="ck_revenue_derived"),
)

fact_ext_geo_variation = _table(
    "fact_ext_geo_variation",
    Column("year", Integer, primary_key=True),
    Column("geo_level", Text, primary_key=True),
    Column("geo_code", Text, primary_key=True),
    Column("age_level", Text, primary_key=True),
    Column("geo_desc", Text),
    Column("benes_total", Integer),
    Column("benes_ffs_ab", Integer),
    Column("benes_original_medicare", Integer),
    Column("benes_ma", Integer),
    Column("ma_participation_rate", Float),
    Column("avg_age", Float),
    # county rows are not loaded: the protocol works at state level
    CheckConstraint("geo_level IN ('National', 'State')", name="ck_geovar_level"),
    _rate("ma_participation_rate"),
)

fact_ext_provider_counts = _table(
    "fact_ext_provider_counts",
    Column("snapshot_date", Text, primary_key=True),
    Column("state_code", Text, primary_key=True),
    Column("taxonomy_code", Text, primary_key=True),
    Column("n_individual_providers", Integer, nullable=False),
)

fact_ext_arthritis_prevalence = _table(
    "fact_ext_arthritis_prevalence",
    Column("data_year", Integer, primary_key=True),
    Column("location_id", Text, primary_key=True),
    Column("measure_id", Text, primary_key=True),
    Column("value_type", Text, primary_key=True),
    Column("state_code", Text),
    Column("county_name", Text),
    Column("prevalence_pct", Float),
    Column("ci_low_pct", Float),
    Column("ci_high_pct", Float),
    Column("total_population", Integer),
    Column("footnote", Text),
)

# ---------------------------------------------------------------- gold

gold_ext_specialty_triangulation = _table(
    "gold_ext_specialty_triangulation",
    Column("period", Text, primary_key=True),
    Column("specialty_group", Text, primary_key=True),
    Column("relationship", Text),
    Column("n_visible_providers", Integer),
    Column("n_zilretta_providers", Integer),
    Column("medicare_adoption_rate", Float),
    Column("medicare_ci_low", Float),
    Column("medicare_ci_high", Float),
    Column("iqvia_adjusted_share", Float),
    Column("iqvia_ci_low", Float),
    Column("iqvia_ci_high", Float),
    Column("iqvia_share_65plus", Float),
    _rate("medicare_adoption_rate"),
)

gold_ext_state_adoption = _table(
    "gold_ext_state_adoption",
    Column("year", Integer, primary_key=True),
    Column("state_code", Text, primary_key=True),
    Column("n_visible_providers", Integer),
    Column("n_zilretta_providers", Integer),
    Column("adoption_rate", Float),
    Column("ci_low", Float),
    Column("ci_high", Float),
    Column("reported", Integer, nullable=False),
    Column("ma_participation_rate", Float),
    Column("arthritis_prevalence_pct", Float),
    Column("headroom", Float),
    CheckConstraint("reported IN (0, 1)", name="ck_state_adoption_reported"),
    _rate("adoption_rate"),
)

gold_ext_promotion_monthly = _table(
    "gold_ext_promotion_monthly",
    Column("month_id", Integer, primary_key=True),
    Column("n_physicians_paid", Integer),
    Column("n_practitioners_paid", Integer),
    Column("total_amount_usd", Float),
    Column("n_records", Integer),
    Column("n_flagged_records", Integer),
    Column("iqvia_share_pct", Float),
)

gold_ext_price_quarterly = _table(
    "gold_ext_price_quarterly",
    Column("quarter_id", Text, primary_key=True),
    Column("j3304_limit_per_mg", Float),
    Column("j3301_limit_per_mg", Float),
    Column("price_ratio", Float),
)

gold_ext_company_vs_visits = _table(
    "gold_ext_company_vs_visits",
    Column("quarter_id", Text, primary_key=True),
    Column("company", Text),
    Column("net_sales_usd", Float),
    Column("iqvia_zilretta_visits", Integer),
    Column("yoy_sales_direction", Text),
    Column("yoy_visits_direction", Text),
    Column("directions_agree", Integer),
)

gold_ext_verdicts = _table(
    "gold_ext_verdicts",
    Column("run_id", Text, primary_key=True),
    Column("check_id", Text, primary_key=True),
    Column("metric", Text, primary_key=True),
    Column("value", Float),
    Column("threshold", Text),
    Column("rule", Text, nullable=False),
    Column("verdict", Text, nullable=False),
    Column("note", Text),
    Column("created_at", Text),
    CheckConstraint(_in("verdict", VERDICTS), name="ck_verdicts_label"),
)

# ---------------------------------------------------------------- classification

LOCAL_ONLY_TABLES: tuple[str, ...] = (
    "fact_ext_partb_provider",  # NPI level, 800k+ rows
    "fact_ext_arthritis_prevalence",  # county level
    "src_nucc_taxonomy",  # AMA-copyright text
    "external_load_runs",  # run log
)
PUBLISHED_TABLES: tuple[str, ...] = tuple(
    name for name in external_metadata.tables if name not in LOCAL_ONLY_TABLES
)


def create_external_schema(engine: Engine) -> None:
    """Create every external table that does not exist. Never drops or alters one that does."""
    external_metadata.create_all(engine, checkfirst=True)
