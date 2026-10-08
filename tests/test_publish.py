"""Tests for the safe publish step (src/.../publish.py).

Publishing rebuilds the warehouse into a staging file, checks it, computes the model panel,
and only then swaps it in. The property under test: a failed run never damages the database
the website is serving, and every run leaves a record.
"""

import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.publish import parse_args, publish
from oa_market_intelligence.serving.model_panel import load_stored_panel
from oa_market_intelligence.serving.queries import data_status
from oa_market_intelligence.serving.results import load_result
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


def _fake_panel(engine, **_):
    return {
        "scores": pd.DataFrame({"balanced_accuracy": [0.4]}, index=["seasonal"]),
        "chance": pd.DataFrame({"p95": [0.46]}, index=["balanced_accuracy"]),
        "decision": {"promoted": False, "serving": "seasonal", "reason": "r"},
        "n_test": 35,
        "label_counts": {"Flat": 25},
    }


def _fake_results(engine, precision):
    """Minimal stand-ins for the five stored results; the direction one carries backtest rows."""
    return {
        "findings": {"trend": {"break_months": [202203]}},
        "segment_model": {"decision": {"serving": "logistic"}},
        "forecast": {"decision": {"serving": "last_month"}},
        "direction": {
            "decision": {"serving": "seasonal"},
            "predictions": [
                {"month_id": 201909, "y_true": "Up", "seasonal": "Flat", "persistence": "Up"}
            ],
        },
        "monitoring": {"status": "ok", "banner": None},
    }


def _publish(tmp_path, month_ids, **overrides):
    return publish(
        raw_dir=tmp_path / "raw",
        reference_dir=tmp_path / "ref",
        published_dir=tmp_path / "published",
        run_pipeline_fn=overrides.pop("run_pipeline_fn", _fake_pipeline(month_ids)),
        panel_fn=overrides.pop("panel_fn", _fake_panel),
        results_fn=overrides.pop("results_fn", _fake_results),
        **overrides,
    )


def _db(tmp_path):
    return tmp_path / "published" / "warehouse.db"


def _log(tmp_path):
    lines = (tmp_path / "published" / "run_log.jsonl").read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


def test_a_good_run_publishes_the_database_the_panel_and_a_log_entry(tmp_path):
    record = _publish(tmp_path, MONTHS_2)
    assert record["status"] == "ok"
    assert record["last_month_id"] == 201909 and record["n_months"] == 2

    engine = create_engine(f"sqlite:///{_db(tmp_path).as_posix()}")
    assert data_status(engine)["n_months"] == 2
    assert load_stored_panel(engine)["decision"]["serving"] == "seasonal"
    assert [entry["status"] for entry in _log(tmp_path)] == ["ok"]


def test_a_new_month_replaces_the_published_database(tmp_path):
    _publish(tmp_path, MONTHS_2)
    record = _publish(tmp_path, MONTHS_3)
    assert record["status"] == "ok" and record["last_month_id"] == 201910
    engine = create_engine(f"sqlite:///{_db(tmp_path).as_posix()}")
    assert data_status(engine)["last_month_id"] == 201910
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


def test_a_failing_model_stage_also_keeps_the_last_good_database(tmp_path):
    _publish(tmp_path, MONTHS_2)
    before = _db(tmp_path).read_bytes()

    def bad_panel(engine, **_):
        raise ValueError("not enough months to train")

    record = _publish(tmp_path, MONTHS_3, panel_fn=bad_panel)
    assert record["status"] == "failed"
    assert _db(tmp_path).read_bytes() == before


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


def test_every_result_is_stored_in_the_published_file_with_its_precision(tmp_path):
    record = _publish(tmp_path, MONTHS_2, precision="fast")
    assert record["status"] == "ok" and record["precision"] == "fast"
    engine = create_engine(f"sqlite:///{_db(tmp_path).as_posix()}")
    for key in ("findings", "segment_model", "forecast", "direction", "monitoring"):
        stored = load_result(engine, key)
        assert stored is not None and stored["precision"] == "fast", key
    assert load_result(engine, "findings")["trend"]["break_months"] == [202203]
    assert record["monitoring_status"] == "ok"


def test_the_backtest_predictions_are_written_to_the_gold_columns_before_the_swap(tmp_path):
    _publish(tmp_path, MONTHS_2)
    engine = create_engine(f"sqlite:///{_db(tmp_path).as_posix()}")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT month_id, predicted_direction, actual_direction, model_version "
                 "FROM gold_visit_share_monthly ORDER BY month_id")
        ).fetchall()
    assert rows[0] == (201908, "Flat", "Up", "backtest walk-forward: seasonal")
    assert rows[1][1:] == (None, None, None)


def test_a_failing_results_stage_keeps_the_last_good_database(tmp_path):
    _publish(tmp_path, MONTHS_2)
    before = _db(tmp_path).read_bytes()

    def broken(engine, precision):
        raise RuntimeError("segment model did not converge")

    record = _publish(tmp_path, MONTHS_3, results_fn=broken)
    assert record["status"] == "failed"
    assert "did not converge" in record["error"]
    assert _db(tmp_path).read_bytes() == before
    assert [entry["status"] for entry in _log(tmp_path)] == ["ok", "failed"]


def test_the_command_line_takes_a_precision_and_defaults_to_full():
    assert parse_args([]).precision == "full"
    assert parse_args(["--precision", "fast"]).precision == "fast"
    with pytest.raises(SystemExit):
        parse_args(["--precision", "sloppy"])


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
