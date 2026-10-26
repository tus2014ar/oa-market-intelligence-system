"""Tests for the safe publish step (src/.../publish.py).

Publishing rebuilds the warehouse into a staging file, checks it, and only then swaps it in.
The property under test: a failed run never damages the published database, and every run
leaves a record.
"""

import json

import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.publish import publish
from tiny_gold import build_tiny_gold

MONTHS_2 = (201908, 201909)
MONTHS_3 = (201908, 201909, 201910)


def _fake_pipeline(month_ids):
    def run(*, raw_dir, reference_dir, db_path, **_):
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        build_tiny_gold(engine, month_ids)
        engine.dispose()
        return {"ok": True}

    return run


def _publish(tmp_path, month_ids, **overrides):
    return publish(
        raw_dir=tmp_path / "raw",
        reference_dir=tmp_path / "ref",
        published_dir=tmp_path / "published",
        run_pipeline_fn=overrides.pop("run_pipeline_fn", _fake_pipeline(month_ids)),
        features_fn=overrides.pop("features_fn", lambda engine: {}),
        model_tables_fn=overrides.pop("model_tables_fn", lambda engine: {}),
        **overrides,
    )


def _db(tmp_path):
    return tmp_path / "published" / "warehouse.db"


def _log(tmp_path):
    lines = (tmp_path / "published" / "run_log.jsonl").read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


def _months(tmp_path):
    engine = create_engine(f"sqlite:///{_db(tmp_path).as_posix()}")
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT COUNT(*), MAX(month_id) FROM gold_visit_share_monthly")
            ).one()
    finally:
        engine.dispose()


def test_a_good_run_publishes_the_database_and_a_log_entry(tmp_path):
    record = _publish(tmp_path, MONTHS_2)
    assert record["status"] == "ok"
    assert record["last_month_id"] == 201909 and record["n_months"] == 2
    assert tuple(_months(tmp_path)) == (2, 201909)
    assert [entry["status"] for entry in _log(tmp_path)] == ["ok"]


def test_a_new_month_replaces_the_published_database(tmp_path):
    _publish(tmp_path, MONTHS_2)
    record = _publish(tmp_path, MONTHS_3)
    assert record["status"] == "ok" and record["last_month_id"] == 201910
    assert tuple(_months(tmp_path)) == (3, 201910)
    assert [entry["status"] for entry in _log(tmp_path)] == ["ok", "ok"]


def test_a_crashing_pipeline_leaves_the_published_database_untouched(tmp_path):
    _publish(tmp_path, MONTHS_2)
    before = _db(tmp_path).read_bytes()

    def broken(**_):
        raise RuntimeError("openFDA timed out")

    record = _publish(tmp_path, MONTHS_3, run_pipeline_fn=broken)
    assert record["status"] == "failed"
    assert "openFDA timed out" in record["error"]
    assert _db(tmp_path).read_bytes() == before
    assert [entry["status"] for entry in _log(tmp_path)] == ["ok", "failed"]


def test_a_rebuild_with_fewer_months_is_refused(tmp_path):
    _publish(tmp_path, MONTHS_3)
    before = _db(tmp_path).read_bytes()
    record = _publish(tmp_path, MONTHS_2)
    assert record["status"] == "failed"
    assert "fewer months" in record["error"] or "backwards" in record["error"]
    assert _db(tmp_path).read_bytes() == before


def test_an_empty_warehouse_is_refused_on_a_first_run(tmp_path):
    record = _publish(tmp_path, ())
    assert record["status"] == "failed"
    assert not _db(tmp_path).exists()
    assert _log(tmp_path)[-1]["status"] == "failed"


def test_no_staging_files_are_left_behind(tmp_path):
    _publish(tmp_path, MONTHS_2)
    _publish(tmp_path, MONTHS_2, run_pipeline_fn=lambda **_: 1 / 0)
    leftovers = {path.name for path in (tmp_path / "published").iterdir()}
    assert leftovers == {"warehouse.db", "run_log.jsonl"}


@pytest.mark.parametrize("count", [1, 3])
def test_the_log_is_append_only_json_lines(tmp_path, count):
    for _ in range(count):
        _publish(tmp_path, MONTHS_2)
    assert len(_log(tmp_path)) == count
    assert {"status", "started_at", "finished_at"} <= set(_log(tmp_path)[0])


def test_unmapped_products_are_recorded_in_the_run_log_and_do_not_stop_the_run(tmp_path):
    inner = _fake_pipeline(MONTHS_2)

    def pipeline_with_a_new_product(**kwargs):
        inner(**kwargs)
        return {"silver": {"unmapped_products": ["NEWDRUG B", "NEWDRUG A"]}}

    record = _publish(tmp_path, MONTHS_2, run_pipeline_fn=pipeline_with_a_new_product)
    assert record["status"] == "ok"
    assert record["unmapped_products"] == ["NEWDRUG A", "NEWDRUG B"]
    assert _log(tmp_path)[-1]["unmapped_products"] == ["NEWDRUG A", "NEWDRUG B"]


def test_a_run_without_unmapped_products_records_an_empty_list(tmp_path):
    record = _publish(tmp_path, MONTHS_2)
    assert record["unmapped_products"] == []


def test_the_extract_hashes_are_recorded_in_the_run_log(tmp_path):
    inner = _fake_pipeline(MONTHS_2)
    files = [
        {
            "file_name": "Team1_M15_19_OA.xlsx",
            "role": "nmta_pivot",
            "sha256": "ab" * 32,
            "size_bytes": 10,
            "rows_parsed": 3,
        }
    ]

    def pipeline_with_audit(**kwargs):
        inner(**kwargs)
        return {"ingest_files": files}

    record = _publish(tmp_path, MONTHS_2, run_pipeline_fn=pipeline_with_audit)
    assert record["ingest_files"] == files
    assert _log(tmp_path)[-1]["ingest_files"] == files
    assert _publish(tmp_path / "again", MONTHS_2)["ingest_files"] == []  # no audit: empty list


def test_the_feature_tables_are_built_in_the_staging_file_and_recorded_in_the_run_log(tmp_path):
    def features(engine):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE feat_monthly (month_id INTEGER)"))
            conn.execute(text("INSERT INTO feat_monthly VALUES (201908), (201909)"))
        return {"feat_monthly": 2}

    record = _publish(tmp_path, MONTHS_2, features_fn=features)
    assert record["status"] == "ok" and record["feature_tables"] == {"feat_monthly": 2}
    engine = create_engine(f"sqlite:///{_db(tmp_path).as_posix()}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM feat_monthly")).scalar() == 2
    assert _log(tmp_path)[-1]["feature_tables"] == {"feat_monthly": 2}


def test_a_failing_feature_stage_keeps_the_last_good_database(tmp_path):
    _publish(tmp_path, MONTHS_2)
    before = _db(tmp_path).read_bytes()

    def broken(engine):
        raise RuntimeError("feature build failed")

    record = _publish(tmp_path, MONTHS_3, features_fn=broken)
    assert record["status"] == "failed" and "feature build failed" in record["error"]
    assert _db(tmp_path).read_bytes() == before


def test_the_model_tables_are_built_in_the_staging_file_and_recorded_in_the_run_log(tmp_path):
    def model_tables(engine):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE model_scores (task TEXT)"))
            conn.execute(text("INSERT INTO model_scores VALUES ('T1'), ('T2'), ('T3')"))
        return {"model_scores": 3}

    record = _publish(tmp_path, MONTHS_2, model_tables_fn=model_tables)
    assert record["status"] == "ok" and record["model_tables"] == {"model_scores": 3}
    engine = create_engine(f"sqlite:///{_db(tmp_path).as_posix()}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM model_scores")).scalar() == 3
    assert _log(tmp_path)[-1]["model_tables"] == {"model_scores": 3}


def test_a_failing_model_table_stage_keeps_the_last_good_database(tmp_path):
    _publish(tmp_path, MONTHS_2)
    before = _db(tmp_path).read_bytes()

    def broken(engine):
        raise RuntimeError("model tables failed")

    record = _publish(tmp_path, MONTHS_3, model_tables_fn=broken)
    assert record["status"] == "failed" and "model tables failed" in record["error"]
    assert _db(tmp_path).read_bytes() == before
