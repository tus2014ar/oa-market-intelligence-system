"""Tests for the external-data schema (DL-59, step 3).

The schema lives in its own SQLite file (`external.db`) with its own metadata, so the IQVIA
warehouse cannot be altered by it. These tests pin that separation, the table classification
(what is committed and what stays local), the constraints, and that every column is documented
in docs/external_data_lineage.md.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from oa_market_intelligence.external.schema import (
    LOCAL_ONLY_TABLES,
    PUBLISHED_TABLES,
    create_external_schema,
    external_metadata,
)
from oa_market_intelligence.warehouse.schema import create_schema
from oa_market_intelligence.warehouse.schema import metadata as iqvia_metadata

LINEAGE = Path(__file__).resolve().parent.parent / "docs" / "external_data_lineage.md"
IQVIA_TABLES = {
    "dim_month",
    "dim_product",
    "dim_specialty",
    "dim_demographics",
    "fact_product_visits",
    "fact_place_of_service_visits",
    "gold_visit_share_monthly",
    "gold_segment_adoption",
}


@pytest.fixture
def engine():
    return create_engine("sqlite:///:memory:")


def _insert(engine, table: str, **values):
    columns = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({marks})"), values)


# ---------- separation from the IQVIA warehouse ----------


def test_the_external_schema_shares_no_table_name_with_the_iqvia_warehouse():
    assert (
        set(iqvia_metadata.tables) == IQVIA_TABLES
    )  # importing the external module changed nothing
    assert not set(external_metadata.tables) & set(iqvia_metadata.tables)


def test_creating_the_external_schema_leaves_every_iqvia_table_definition_unchanged(engine):
    create_schema(engine)
    with engine.connect() as conn:
        before = dict(
            conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table'")).all()
        )
    create_external_schema(engine)
    with engine.connect() as conn:
        after = dict(
            conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='table'")).all()
        )
    assert {name: after[name] for name in IQVIA_TABLES} == {
        name: before[name] for name in IQVIA_TABLES
    }


def test_creating_the_schema_twice_changes_nothing(engine):
    create_external_schema(engine)
    with engine.connect() as conn:
        first = conn.execute(text("SELECT name, sql FROM sqlite_master ORDER BY name")).all()
    create_external_schema(engine)
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT name, sql FROM sqlite_master ORDER BY name")).all() == first
        )


# ---------- what is committed and what stays local ----------


def test_every_table_is_either_published_or_local_only_and_never_both():
    names = set(external_metadata.tables)
    assert set(PUBLISHED_TABLES) | set(LOCAL_ONLY_TABLES) == names
    assert not set(PUBLISHED_TABLES) & set(LOCAL_ONLY_TABLES)


def test_the_npi_level_fact_the_county_file_and_the_taxonomy_text_stay_local():
    assert set(LOCAL_ONLY_TABLES) >= {
        "fact_ext_partb_provider",
        "fact_ext_arthritis_prevalence",
        "src_nucc_taxonomy",
    }


def test_every_gold_table_is_published_and_every_fact_has_a_declared_key():
    assert {t for t in external_metadata.tables if t.startswith("gold_ext_")} <= set(
        PUBLISHED_TABLES
    )
    for name, table in external_metadata.tables.items():
        assert len(table.primary_key.columns) >= 1, name
        assert all(not c.nullable for c in table.primary_key.columns), name


# ---------- the lineage document ----------


def _lineage_rows() -> list[tuple[str, str, str, str]]:
    rows = []
    for line in LINEAGE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*`([a-z_0-9]+\.[a-z_0-9]+)`\s*\|(.*)\|\s*$", line)
        if match:
            cells = [c.strip() for c in ("`" + match.group(1) + "`|" + match.group(2)).split("|")]
            rows.append((cells[0].strip("`"), cells[1], cells[2], cells[3]))
    return rows


def test_every_column_is_documented_exactly_once_and_nothing_stale_is_documented():
    documented = [r[0] for r in _lineage_rows()]
    actual = {f"{t.name}.{c.name}" for t in external_metadata.tables.values() for c in t.columns}
    assert len(documented) == len(set(documented)), "a column is documented twice"
    assert set(documented) - actual == set(), "lineage rows for columns that do not exist"
    assert actual - set(documented) == set(), "columns missing from the lineage document"


def test_every_lineage_row_names_a_source_and_a_rule():
    for column, source, original, rule in _lineage_rows():
        assert source and original and rule, column


# ---------- constraints ----------


def test_a_cpt_code_cannot_carry_a_description(engine):
    create_external_schema(engine)
    ok = dict(code_group="C_denominator_procedures", drug_family="joint_injection_procedure")
    _insert(engine, "dim_hcpcs_code", hcpcs_code="20610", is_cpt=1, short_description=None, **ok)
    with pytest.raises(IntegrityError):
        _insert(
            engine, "dim_hcpcs_code", hcpcs_code="20611", is_cpt=1, short_description="text", **ok
        )
    _insert(
        engine,
        "dim_hcpcs_code",
        hcpcs_code="J3304",
        is_cpt=0,
        short_description="Inj triamcinolone",
        code_group="A_primary",
        drug_family="zilretta_triamcinolone_er",
    )


def test_code_group_setting_entity_and_rate_values_are_constrained(engine):
    create_external_schema(engine)
    with pytest.raises(IntegrityError):
        _insert(
            engine, "dim_hcpcs_code", hcpcs_code="J0000", is_cpt=0, code_group="Z", drug_family="x"
        )
    base = dict(
        year=2022, npi="1", hcpcs_code="J3304", specialty_cms="Rheumatology", entity_type="I"
    )
    _insert(engine, "fact_ext_partb_provider", setting="O", **base)
    with pytest.raises(IntegrityError):
        _insert(engine, "fact_ext_partb_provider", setting="X", **{**base, "npi": "2"})
    with pytest.raises(IntegrityError):
        _insert(
            engine,
            "fact_ext_partb_provider",
            setting="F",
            **{**base, "npi": "3", "entity_type": "Z"},
        )
    with pytest.raises(IntegrityError):
        _insert(
            engine,
            "fact_ext_geo_variation",
            year=2022,
            geo_level="State",
            geo_code="48",
            age_level="All",
            ma_participation_rate=1.5,
        )
    _insert(
        engine,
        "fact_ext_geo_variation",
        year=2022,
        geo_level="State",
        geo_code="48",
        age_level="All",
        ma_participation_rate=0.45,
    )


def test_the_declared_grain_rejects_a_duplicate_row(engine):
    create_external_schema(engine)
    row = dict(quarter_id="2021Q2", hcpcs_code="J3304", source_file="a.zip")
    _insert(engine, "fact_ext_asp_price", **row)
    with pytest.raises(IntegrityError):
        _insert(engine, "fact_ext_asp_price", **row)


def test_county_rows_are_not_allowed_in_the_published_geographic_variation_table(engine):
    create_external_schema(engine)
    with pytest.raises(IntegrityError):
        _insert(
            engine,
            "fact_ext_geo_variation",
            year=2022,
            geo_level="County",
            geo_code="48201",
            age_level="All",
        )


def test_the_inspector_sees_every_declared_table(engine):
    create_external_schema(engine)
    assert set(inspect(engine).get_table_names()) == set(external_metadata.tables)
