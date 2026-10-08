"""Tests for the IQVIA ingest audit table (R5).

One row per parsed output of each raw extract: which file, its SHA-256 and size, how many rows
were parsed, the month span and the visit sum. Built from small synthetic frames and files here;
the real extracts are covered in tests/test_pipeline.py (the real `run_pipeline` run).
"""

import hashlib

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.ingestion.audit import (
    RAW_FILES,
    build_ingest_audit,
    refresh_bronze_ingest_files,
    sha256_file,
)
from oa_market_intelligence.warehouse.schema import create_schema


def _raw_dir(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in {file for pair in RAW_FILES.values() for file in pair.values()}:
        (raw / name).write_bytes(f"contents of {name}".encode())
    return raw


def _frames():
    months = pd.to_datetime(["2019-08-01", "2019-09-01", "2019-09-01"])
    visits = pd.DataFrame(
        {
            "month": months.append(months[:2]),
            "disease_area": ["OA", "OA", "OA", "RA", "RA"],
            "patient_visits": [10, 20, 30, 1, 2],
        }
    )
    pos = pd.DataFrame(
        {
            "month": pd.to_datetime(["2019-08-01", "2019-09-01", "2019-08-01"]),
            "disease_area": ["OA", "OA", "RA"],
            "place_of_service": ["OFFICE", "OFFICE", "OFFICE"],
            "patient_visits": [100, 200, 5],
        }
    )
    reference = pd.DataFrame(
        {
            "disease_area": ["OA", "OA", "RA"],
            "product": ["A", "B", "C"],
            "patient_visits": [7, 8, 9],
        }
    )
    return visits, pos, reference


def test_the_hash_and_size_are_those_of_the_file(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"hello")
    assert sha256_file(path) == hashlib.sha256(b"hello").hexdigest()


def test_each_extract_gets_a_row_per_parsed_output(tmp_path):
    raw = _raw_dir(tmp_path)
    rows = build_ingest_audit(raw, *_frames(), ingested_at="2026-10-08T12:00:00+00:00")
    table = pd.DataFrame(rows).set_index(["file_name", "role"])
    assert len(table) == 6
    assert set(table.index.get_level_values("role")) == {
        "nmta_pivot",
        "place_of_service",
        "reference_table",
    }

    oa_pivot = table.loc[("Team1_M15_19_OA.xlsx", "nmta_pivot")]
    assert oa_pivot["disease_area"] == "OA" and oa_pivot["rows_parsed"] == 3
    assert (oa_pivot["first_month"], oa_pivot["last_month"], oa_pivot["n_months"]) == (
        201908,
        201909,
        2,
    )
    assert oa_pivot["visits_sum"] == 60
    assert oa_pivot["sha256"] == hashlib.sha256(b"contents of Team1_M15_19_OA.xlsx").hexdigest()
    assert oa_pivot["size_bytes"] == len(b"contents of Team1_M15_19_OA.xlsx")

    ra_pos = table.loc[("Team1_M04_RA.xlsx", "place_of_service")]
    assert ra_pos["rows_parsed"] == 1 and ra_pos["visits_sum"] == 5 and ra_pos["n_months"] == 1


def test_the_same_workbook_appears_once_per_output_it_produced(tmp_path):
    rows = build_ingest_audit(_raw_dir(tmp_path), *_frames())
    oa_workbook = [r for r in rows if r["file_name"] == "Team1_M15_19_OA.xlsx"]
    assert {r["role"] for r in oa_workbook} == {"nmta_pivot", "place_of_service"}
    assert len({r["sha256"] for r in oa_workbook}) == 1  # one file, one hash


def test_reference_tables_have_no_month_span(tmp_path):
    rows = build_ingest_audit(_raw_dir(tmp_path), *_frames())
    reference = next(r for r in rows if r["file_name"] == "Branded Generic - OA.xlsx")
    assert reference["rows_parsed"] == 2 and reference["visits_sum"] == 15
    assert reference["first_month"] is None and reference["n_months"] is None


def test_every_row_names_what_the_parser_already_checked(tmp_path):
    rows = build_ingest_audit(_raw_dir(tmp_path), *_frames())
    assert all(r["parser_checks"] for r in rows)
    pivot = next(r for r in rows if r["role"] == "nmta_pivot")
    assert "Grand Total" in pivot["parser_checks"]


def test_a_missing_raw_file_is_named_in_the_error(tmp_path):
    raw = _raw_dir(tmp_path)
    (raw / "Branded Generic - RA.xlsx").unlink()
    with pytest.raises(FileNotFoundError, match="Branded Generic - RA.xlsx"):
        build_ingest_audit(raw, *_frames())


def test_an_extract_with_no_parsed_rows_is_recorded_with_zero_rows(tmp_path):
    visits, pos, reference = _frames()
    rows = build_ingest_audit(
        _raw_dir(tmp_path), visits[visits["disease_area"] == "OA"], pos, reference
    )
    ra_pivot = next(
        r for r in rows if r["file_name"] == "Team1_M04_RA.xlsx" and r["role"] == "nmta_pivot"
    )
    assert ra_pivot["rows_parsed"] == 0 and ra_pivot["visits_sum"] == 0
    assert ra_pivot["first_month"] is None


def test_the_table_is_created_loaded_and_replaced_without_duplicates(tmp_path):
    engine = create_engine("sqlite://")
    create_schema(engine)
    rows = build_ingest_audit(_raw_dir(tmp_path), *_frames())
    assert refresh_bronze_ingest_files(engine, rows) == 6
    assert refresh_bronze_ingest_files(engine, rows) == 6  # a rerun replaces, never appends
    with engine.connect() as conn:
        stored = pd.read_sql(text("SELECT * FROM bronze_ingest_files"), conn)
    assert len(stored) == 6 and {"sha256", "rows_parsed", "ingested_at"} <= set(stored.columns)
    assert stored["ingested_at"].notna().all()
