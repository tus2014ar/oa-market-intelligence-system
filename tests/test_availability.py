"""Tests for the source-availability table and the `available_from_month` rule (R1).

Planted cases check each rule type; the manifest tests keep the *documented* claims true (for
example, that every Part B and Part D file name says release year = data year + 2); the
completeness test makes sure every table a model could read is covered by a source row.
"""

import json
import re

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

from oa_market_intelligence.availability import (
    AVAILABILITY_CSV,
    add_months,
    available_from_month,
    is_available,
    load_availability,
    refresh_source_availability,
    validate_availability,
)
from oa_market_intelligence.external.common import REFERENCE_DIR
from oa_market_intelligence.warehouse.schema import create_schema


def rule(source_id, table=None):
    table = load_availability() if table is None else table
    return table.set_index("source_id").loc[source_id].to_dict()


# ---------- month arithmetic ----------


@pytest.mark.parametrize(
    ("month", "n", "expected"),
    [
        (202401, 2, 202403),
        (202411, 2, 202501),
        (202412, 1, 202501),
        (202403, -2, 202401),
        (202402, -3, 202311),
        (202401, 0, 202401),
        (202312, 24, 202512),
    ],
)
def test_add_months_crosses_year_ends_both_ways(month, n, expected):
    assert add_months(month, n) == expected


# ---------- the four rule types ----------


def test_lag_rule_makes_iqvia_month_t_known_in_month_t_plus_2():
    assert available_from_month(rule("iqvia_nmta"), 202407) == 202409
    assert available_from_month(rule("iqvia_nmta"), 202411) == 202501


def test_a_negative_lag_makes_an_asp_quarter_known_when_the_quarter_starts():
    # a quarter's period end is its last month; the file is known by the first month
    assert available_from_month(rule("asp_price"), 202403) == 202401
    assert available_from_month(rule("asp_price"), 202412) == 202410


def test_release_year_rules_use_the_release_year_and_month():
    assert available_from_month(rule("openpay"), 202312) == 202406  # June of the next year
    assert available_from_month(rule("partb_geo"), 202212) == 202412  # two years later, December
    assert available_from_month(rule("geovar"), 202412) == 202604  # April two years later
    assert available_from_month(rule("ma_geovar"), 202312) == 202607  # July three years later


def test_a_per_record_rule_uses_the_records_own_date():
    sec = rule("sec_filings")
    assert available_from_month(sec, 202106, record_date="2021-08-09") == 202108
    with pytest.raises(ValueError, match="record_date"):
        available_from_month(sec, 202106)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        available_from_month(sec, 202106, record_date="09/08/2021")


def test_a_snapshot_is_known_only_from_its_own_month_whatever_the_period():
    nppes = rule("nppes")
    assert available_from_month(nppes, 201901) == 202609
    assert available_from_month(nppes, 202512) == 202609


def test_is_available_compares_with_the_month_a_decision_is_made():
    iqvia = rule("iqvia_nmta")
    assert not is_available(iqvia, 202407, as_of_month=202408)
    assert is_available(iqvia, 202407, as_of_month=202409)
    assert is_available(iqvia, 202407, as_of_month=202412)


def test_unknown_rule_types_are_refused():
    with pytest.raises(ValueError, match="rule_type"):
        available_from_month({"rule_type": "guess"}, 202401)


# ---------- the committed table is valid ----------


def test_the_committed_table_passes_its_own_validation():
    table = load_availability()
    assert validate_availability(table) == []
    assert table["source_id"].is_unique and len(table) >= 14


def test_validation_names_every_problem():
    table = load_availability().copy()
    table.loc[0, "basis"] = "guessed"
    table.loc[1, "rule_type"] = "lag_months"
    table.loc[1, "lag_months"] = None
    table.loc[2, "evidence"] = ""
    problems = " | ".join(validate_availability(table))
    assert "basis" in problems and "lag_months" in problems and "evidence" in problems


def test_documented_means_every_period_has_its_own_date_in_the_files_we_hold():
    table = load_availability().set_index("source_id")
    documented = set(table.index[table["basis"] == "documented"])
    assert {"sec_filings", "events", "nppes"} <= documented
    assert table.loc[list(documented), "rule_type"].isin(["per_record_date", "snapshot_date"]).all()


# ---------- documented claims stay true (against the committed manifest) ----------


def manifest():
    path = REFERENCE_DIR / "external_manifest.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for row in rows:
        row["file"] = row["file"].replace("\\", "/")
    return rows


def test_every_part_b_and_part_d_file_says_release_year_is_data_year_plus_two():
    pattern = re.compile(r"MUP_(?:PHY|DPR)_R(?:Y)?(\d\d)_P\d\d_V\d\d_D(?:Y)?(\d\d)_")
    checked = 0
    for row in manifest():
        found = pattern.search(row["file"].split("/")[-1])
        if found:
            assert int(found.group(1)) - int(found.group(2)) == 2, row["file"]
            checked += 1
    assert checked >= 12  # Part B geography 6, Part D geography 6, Part D by provider 1
    for source in ("partb_geo", "partd_geo", "partd_provider"):
        assert rule(source)["release_year_lag"] == 2


def test_every_open_payments_file_carries_the_june_2026_refresh_stamp():
    files = [r["file"] for r in manifest() if r["dataset"].startswith("Open Payments")]
    assert len(files) == 7 and all("P06302026" in name for name in files)
    assert rule("openpay")["release_year_lag"] == 1 and rule("openpay")["release_month"] == 6


def test_the_registry_snapshot_month_matches_its_file_name():
    name = next(r["file"] for r in manifest() if r["dataset"] == "NPPES")
    assert "September_2026" in name and rule("nppes")["fixed_month"] == 202609


def test_geovar_release_months_match_the_url_folders():
    urls = {r["dataset"]: r["url"] for r in manifest()}
    assert "/2026-04/" in urls["Medicare Geographic Variation"]
    assert "/2026-07/" in urls["Medicare Advantage Geographic Variation"]
    assert rule("geovar")["release_month"] == 4 and rule("ma_geovar")["release_month"] == 7


def test_every_sec_file_name_starts_with_its_filing_date():
    files = [r["file"] for r in manifest() if r["dataset"].startswith("SEC ")]
    assert len(files) == 86
    assert all(re.match(r"^SEC Filings/[^/]+/\d{4}-\d{2}-\d{2}_", name) for name in files)


# ---------- completeness ----------


def test_every_table_a_model_could_read_is_covered_by_a_source_row():
    from oa_market_intelligence.external.schema import external_metadata

    covered = set()
    for cell in load_availability()["stored_in"]:
        covered.update(name.strip() for name in cell.split(","))
    needed = {
        "fact_product_visits",
        "fact_place_of_service_visits",
        "gold_visit_share_monthly",
        "gold_segment_adoption",
        "dim_event",
        *(name for name in external_metadata.tables if name.startswith("fact_ext_")),
    }
    assert needed <= covered, sorted(needed - covered)


# ---------- the table in the warehouse ----------


def test_the_table_is_created_loaded_and_reloaded_without_duplicates():
    engine = create_engine("sqlite://")
    create_schema(engine)
    assert "dim_source_availability" in inspect(engine).get_table_names()
    first = refresh_source_availability(engine)
    second = refresh_source_availability(engine)
    assert first == second == len(load_availability())
    with engine.connect() as conn:
        stored = pd.read_sql(text("SELECT * FROM dim_source_availability"), conn)
    assert len(stored) == first and stored["source_id"].is_unique
    assert set(stored["basis"]) <= {"documented", "assumed"}


def test_the_csv_is_where_the_table_comes_from():
    assert AVAILABILITY_CSV.exists() and AVAILABILITY_CSV.name == "source_availability.csv"
