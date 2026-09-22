"""Tests for the pipeline orchestrator (Step 10).

Most of the real logic being orchestrated here (parsing, validation, Silver/Gold
builds) is already covered by each stage's own test file and by
tests/test_pipeline_integration.py's in-memory end-to-end chain. What's new and
untested until this file: `run_pipeline` writing to a real file-backed SQLite
database (every earlier test used `sqlite:///:memory:`), and the CLI argument
parsing.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.pipeline import (
    DEFAULT_DB_PATH,
    DEFAULT_RAW_DIR,
    DEFAULT_REFERENCE_DIR,
    _parse_args,
    ingest,
    run_pipeline,
    validate,
)


def _fake_fda(name: str):
    return date(2017, 10, 6) if name == "ZILRETTA" else None


def test_parse_args_defaults():
    args = _parse_args([])
    assert args.raw_dir == DEFAULT_RAW_DIR
    assert args.reference_dir == DEFAULT_REFERENCE_DIR
    assert args.db_path == DEFAULT_DB_PATH


def test_parse_args_overrides(tmp_path):
    custom_db = tmp_path / "custom.db"
    args = _parse_args(["--db-path", str(custom_db)])
    assert args.db_path == custom_db


@pytest.fixture(scope="module")
def raw_extracts():
    """ingest() re-parses the full OA pivot (the slow part of every real-data test in
    this project) - shared once across the tests below that don't need their own
    fresh run_pipeline() call."""
    return ingest(DEFAULT_RAW_DIR)


def test_ingest_returns_known_real_row_counts(raw_extracts):
    visits, place_of_service, reference = raw_extracts
    assert len(visits) == 241_794
    assert len(place_of_service) == 370
    assert len(reference) == 852


def test_validate_passes_real_extracts_through_unchanged(raw_extracts):
    visits, place_of_service, reference = raw_extracts
    v_visits, v_pos, v_reference = validate(visits, place_of_service, reference)
    assert len(v_visits) == len(visits)
    assert len(v_pos) == len(place_of_service)
    assert len(v_reference) == len(reference)


def test_run_pipeline_writes_a_real_sqlite_file_with_expected_tables(tmp_path):
    db_path = tmp_path / "warehouse.db"
    summary = run_pipeline(db_path=db_path, fetch_approval_date=_fake_fda)

    assert db_path.exists()
    assert summary["gold"]["gold_visit_share_monthly_rows"] == 72

    engine = create_engine(f"sqlite:///{db_path.resolve().as_posix()}")
    with engine.connect() as conn:
        branded_total = conn.execute(
            text("SELECT SUM(branded_injectable_visits) FROM gold_visit_share_monthly")
        ).scalar()
        product_count = conn.execute(text("SELECT COUNT(*) FROM dim_product")).scalar()
    assert branded_total == 135_119
    assert product_count == 160


def test_run_pipeline_is_safe_to_rerun_against_an_existing_db_file(tmp_path):
    db_path = tmp_path / "warehouse.db"
    run_pipeline(db_path=db_path, fetch_approval_date=_fake_fda)
    # create_schema is checkfirst, and every Silver/Gold write is upsert or full-
    # refresh-overwrite - a second run against the same file must not error or
    # duplicate rows.
    second_summary = run_pipeline(db_path=db_path, fetch_approval_date=_fake_fda)
    assert second_summary["gold"]["gold_visit_share_monthly_rows"] == 72

    engine = create_engine(f"sqlite:///{db_path.resolve().as_posix()}")
    with engine.connect() as conn:
        month_rows = conn.execute(text("SELECT COUNT(*) FROM gold_visit_share_monthly")).scalar()
    assert month_rows == 72  # not 144 - full-refresh-overwrite, not appended to


def test_run_pipeline_creates_parent_directory_if_missing(tmp_path):
    db_path = tmp_path / "nested" / "does" / "not" / "exist" / "warehouse.db"
    run_pipeline(db_path=db_path, fetch_approval_date=_fake_fda)
    assert db_path.exists()
