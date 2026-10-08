"""Tests for the golden regression check (R4): committed expected values for the warehouse built
from the real extracts, so any change that alters the data or the tables unexpectedly fails a test.

The mechanism is tested on a tiny database; the real check runs the real pipeline (on the session's
parsed extracts) and compares every table with `tests/golden/warehouse_golden.json`. An intended
change is accepted on purpose with `python -m oa_market_intelligence.golden --update` and reviewed
as a diff of that file.
"""

import json

import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.golden import (
    GOLDEN_JSON,
    TABLES,
    compare,
    fake_fda,
    snapshot,
)
from oa_market_intelligence.pipeline import run_pipeline


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE t (k INTEGER PRIMARY KEY, a REAL, label TEXT)"))
        conn.execute(
            text("INSERT INTO t VALUES (1, 1.5, 'x'), (2, 2.5, 'y'), (3, 0.1234567891, 'z')")
        )
    return eng


# ---------- the mechanism ----------


def test_a_snapshot_has_rows_sums_and_a_content_hash(engine):
    snap = snapshot(engine, tables=("t",))["t"]
    assert snap["rows"] == 3
    assert snap["sums"]["a"] == pytest.approx(4.1234568, abs=1e-6)
    assert len(snap["sha256"]) == 64 and "label" not in snap["sums"]


def test_the_hash_does_not_depend_on_row_order(engine):
    first = snapshot(engine, tables=("t",))
    other = create_engine("sqlite://")
    with other.begin() as conn:
        conn.execute(text("CREATE TABLE t (k INTEGER PRIMARY KEY, a REAL, label TEXT)"))
        conn.execute(
            text("INSERT INTO t VALUES (3, 0.1234567891, 'z'), (1, 1.5, 'x'), (2, 2.5, 'y')")
        )
    assert snapshot(other, tables=("t",)) == first


def test_identical_data_has_no_differences(engine):
    snap = snapshot(engine, tables=("t",))
    assert compare(snap, json.loads(json.dumps(snap))) == []


def test_a_changed_value_an_added_row_and_a_missing_table_are_all_reported(engine):
    golden = snapshot(engine, tables=("t",))
    with engine.begin() as conn:
        conn.execute(text("UPDATE t SET a = 9.0 WHERE k = 2"))
    changed = compare(snapshot(engine, tables=("t",)), golden)
    assert any("t" in line and "sum of a" in line for line in changed)
    assert any("content differs" in line for line in changed)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO t VALUES (4, 1.0, 'w')"))
    assert any("rows" in line for line in compare(snapshot(engine, tables=("t",)), golden))
    assert any("missing" in line for line in compare({}, golden))
    assert any("not in the golden" in line for line in compare(golden, {}))


def test_a_tiny_float_difference_is_not_a_change(engine):
    golden = snapshot(engine, tables=("t",))
    with engine.begin() as conn:
        conn.execute(text("UPDATE t SET a = 1.50000000001 WHERE k = 1"))
    assert compare(snapshot(engine, tables=("t",)), golden) == []


# ---------- the real check ----------


def test_the_warehouse_built_from_the_real_extracts_matches_the_committed_golden(
    tmp_path, monkeypatch, real_ingest
):
    monkeypatch.setattr("oa_market_intelligence.pipeline.ingest", lambda raw_dir: real_ingest())
    db = tmp_path / "warehouse.db"
    run_pipeline(db_path=db, fetch_approval_date=fake_fda)
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    golden = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    assert set(golden) == set(TABLES)
    differences = compare(snapshot(engine), golden)
    assert differences == [], "\n".join(differences)
