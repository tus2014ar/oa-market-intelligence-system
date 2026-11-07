"""Tests for the stage-by-stage publish run (PublishRun, DL-128) and the extract fingerprint.

`publish()` and the Dagster job both drive `PublishRun`, so what matters here is that driving it by
hand, stage by stage, gives exactly what `publish()` gives, that its record is written once and
tells the truth about a run that stopped, and that the stage order is the one the real run logs.
"""

import json
import os

import pytest

from oa_market_intelligence import publish as pub
from oa_market_intelligence.publish import STAGE_ORDER, PublishRun, extracts_fingerprint, publish
from test_publish import MONTHS_2, _boom, _fake_pipeline


def run_kwargs(tmp_path, **fns):
    base = {
        "raw_dir": tmp_path / "raw", "reference_dir": tmp_path / "ref",
        "published_dir": tmp_path / "published", "run_pipeline_fn": _fake_pipeline(MONTHS_2),
        "public_data_fn": lambda: {}, "features_fn": lambda engine: {},
        "model_tables_fn": lambda engine: {}, "scoring_fn": None, "monitoring_fn": None,
    }  # fmt: skip
    return {**base, **fns}


def drive(run):
    run.start()
    for name in STAGE_ORDER:
        getattr(run, name)()
    return run.finish()


def log_lines(folder):
    path = folder / "published" / "run_log.jsonl"
    return [json.loads(line) for line in path.read_text().strip().splitlines()]


STABLE_KEYS = ("status", "error", "n_months", "last_month_id", "unmapped_products")


def stages_of(record):
    return [(s["stage"], s["status"]) for s in record["stages"]]


# ------------------------------------------------------------------ the same run, two ways


def test_driving_the_stages_by_hand_gives_the_same_record_as_publish(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    by_hand = drive(PublishRun(**run_kwargs(a)))
    in_one = publish(**run_kwargs(b))
    assert stages_of(by_hand) == stages_of(in_one)
    assert [s["stage"] for s in by_hand["stages"]] == list(STAGE_ORDER)
    assert set(by_hand) == set(in_one)  # the same fields in the record
    for key in STABLE_KEYS:
        assert by_hand[key] == in_one[key], key
    assert (a / "published" / "warehouse.db").exists() and (
        b / "published" / "warehouse.db"
    ).exists()


def test_a_stage_that_is_switched_off_is_recorded_as_skipped_in_its_place(tmp_path):
    record = drive(PublishRun(**run_kwargs(tmp_path)))
    status = dict(stages_of(record))
    assert status["scoring"] == status["promotion"] == status["monitoring"] == "skipped"
    assert status["swap"] == "ok" and record["status"] == "ok"


def test_the_record_is_written_once_however_many_times_finish_is_called(tmp_path):
    run = PublishRun(**run_kwargs(tmp_path))
    first = drive(run)
    again = run.finish()
    assert again is first and len(log_lines(tmp_path)) == 1


# ------------------------------------------------------------------ a run that stops


def test_a_failed_stage_is_the_runs_error_and_nothing_is_swapped(tmp_path):
    run = PublishRun(**run_kwargs(tmp_path, features_fn=_boom))
    run.start()
    run.public_data()
    run.warehouse()
    run.coverage_check()
    with pytest.raises(RuntimeError, match="stage broke"):
        run.feature_tables()
    record = run.finish()  # the driver, like Dagster's teardown, does not pass the error
    assert record["status"] == "failed" and record["error"] == "RuntimeError: stage broke"
    assert stages_of(record)[-1] == ("feature_tables", "failed")
    assert not (tmp_path / "published" / "warehouse.db").exists()
    assert not list((tmp_path / "published").glob(".staging-*"))  # the staging area is removed
    assert log_lines(tmp_path)[-1]["error"] == "RuntimeError: stage broke"


def test_a_run_that_was_cut_short_without_a_failed_stage_is_not_reported_ok(tmp_path):
    run = PublishRun(**run_kwargs(tmp_path))
    run.start()
    run.public_data()
    record = run.finish()  # nothing failed, but the swap never happened
    assert record["status"] == "failed" and "did not reach the swap" in record["error"]
    assert len(log_lines(tmp_path)) == 1


def test_a_run_object_that_ran_no_stage_and_reports_no_error_writes_no_record(tmp_path):
    """Found by the first real orchestrated run: Dagster sets the resource up a second time after
    the steps, and that empty run must not add a failed line to the run log."""
    run = PublishRun(**run_kwargs(tmp_path))
    run.start()
    record = run.finish()
    assert record["status"] == "failed" and record["stages"] == []
    assert not (tmp_path / "published" / "run_log.jsonl").exists()  # but nothing was written
    errored = PublishRun(**run_kwargs(tmp_path / "e"))
    errored.start()
    errored.finish(RuntimeError("start-up failed"))  # an error, even with no stage, is a record
    assert len(log_lines(tmp_path / "e")) == 1


def test_an_explicit_error_is_recorded_as_given_in_the_same_form_as_before(tmp_path):
    run = PublishRun(**run_kwargs(tmp_path))
    run.start()
    record = run.finish(ValueError("bad input"))
    assert record["error"] == "ValueError: bad input"
    other = PublishRun(**run_kwargs(tmp_path / "x"))
    other.start()
    assert other.finish("plain text")["error"] == "plain text"


def test_the_last_good_database_survives_a_failed_run_byte_for_byte(tmp_path):
    drive(PublishRun(**run_kwargs(tmp_path)))
    database = tmp_path / "published" / "warehouse.db"
    before = database.read_bytes()
    run = PublishRun(**run_kwargs(tmp_path, model_tables_fn=_boom))
    run.start()
    for name in STAGE_ORDER[:4]:
        getattr(run, name)()
    with pytest.raises(RuntimeError):
        run.model_tables()
    assert run.finish()["status"] == "failed" and database.read_bytes() == before


# ------------------------------------------------------------------ the order, from the real log


def test_the_stage_order_is_the_one_the_published_run_log_records():
    log = pub.DEFAULT_PUBLISHED_DIR / "run_log.jsonl"
    if not log.exists():
        pytest.skip("no run log")
    last = json.loads([ln for ln in log.read_text().splitlines() if ln.strip()][-1])
    assert [s["stage"] for s in last["stages"]] == list(STAGE_ORDER)


def test_there_are_nine_stages_and_the_swap_is_the_last():
    assert len(STAGE_ORDER) == 9 and STAGE_ORDER[-1] == "swap" and STAGE_ORDER[0] == "public_data"
    assert len(set(STAGE_ORDER)) == 9
    assert all(callable(getattr(PublishRun, name)) for name in STAGE_ORDER)


# ------------------------------------------------------------------ the extract fingerprint


def test_no_extracts_means_no_fingerprint_and_other_files_do_not_count(tmp_path):
    assert extracts_fingerprint(tmp_path) is None
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.xlsx").write_bytes(b"x")  # only the folder's own files count
    assert extracts_fingerprint(tmp_path) is None
    assert extracts_fingerprint(tmp_path / "missing") is None


def test_the_fingerprint_is_stable_and_changes_when_an_extract_is_added_or_changed(tmp_path):
    first = tmp_path / "a.xlsx"
    first.write_bytes(b"one")
    base = extracts_fingerprint(tmp_path)
    assert base and len(base) == 16 and extracts_fingerprint(tmp_path) == base  # stable
    (tmp_path / "b.xlsx").write_bytes(b"two")
    added = extracts_fingerprint(tmp_path)
    assert added != base
    first.write_bytes(b"one, but longer")  # a changed size
    resized = extracts_fingerprint(tmp_path)
    assert resized != added
    stamp = first.stat().st_mtime_ns + 5_000_000_000
    os.utime(first, ns=(stamp, stamp))  # the same bytes, touched
    assert extracts_fingerprint(tmp_path) != resized
    (tmp_path / "b.xlsx").unlink()
    assert extracts_fingerprint(tmp_path) != added  # an extract removed
