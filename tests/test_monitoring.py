"""Tests for the monitoring stage (src/.../monitoring.py) and the run versions (versions.py):
freshness at the boundary, drift against what happened by hand, nothing to compare when no month
has arrived, and a stage that never stops a run."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from oa_market_intelligence.monitoring import (
    MAX_AGE_DAYS,
    drift_report,
    freshness,
    month_end,
    run_monitoring,
)
from oa_market_intelligence.versions import (
    UNKNOWN,
    data_version,
    data_version_from_files,
    environment_version,
    run_versions,
)

SEG_KEYS = ["specialty_name", "age_band", "gender"]


def _seg(rows):
    columns = ["month_id", "specialty_name", "age_band", "gender", "branded_injectable_visits",
               "total_category_visits"]
    return pd.DataFrame(
        [(m, s, "40 TO 59", "MALE", z, t) for m, s, z, t in rows], columns=columns
    )


def _pred(rows):
    base = dict(run_month_id=202203, level="segment", age_band="40 TO 59", gender="MALE")
    return pd.DataFrame([{**base, **r} for r in rows])


# ------------------------------------------------------------------ freshness


def test_month_end_by_hand():
    assert month_end(202507) == date(2025, 7, 31)
    assert month_end(202402) == date(2024, 2, 29)  # a leap year


def test_freshness_flips_at_the_limit():
    end = month_end(202507)
    fresh = freshness(202507, today=date.fromordinal(end.toordinal() + MAX_AGE_DAYS))
    stale = freshness(202507, today=date.fromordinal(end.toordinal() + MAX_AGE_DAYS + 1))
    assert fresh["status"] == "fresh" and fresh["age_days"] == MAX_AGE_DAYS
    assert stale["status"] == "stale" and stale["age_days"] == MAX_AGE_DAYS + 1
    assert "no newer extract" in stale["note"] and stale["last_month_end"] == "2025-07-31"


# ------------------------------------------------------------------ drift


def test_the_segment_bias_is_the_weighted_predicted_over_observed_minus_one():
    previous = _pred([
        dict(specialty_name="A", target_month_id=202204, horizon=1, point_share=0.05,
             known_bias=0.05, model="logistic"),
        dict(specialty_name="B", target_month_id=202204, horizon=1, point_share=0.10,
             known_bias=0.05, model="logistic"),
    ])
    seg = _seg([(202204, "A", 4, 100), (202204, "B", 12, 200)])
    monthly = pd.DataFrame({"month_id": [202203, 202204], "visit_share": [0.04, 0.05]})
    out = drift_report(previous, seg, monthly, pd.DataFrame())
    row = out["segment"]["1"]
    # predicted visits 0.05 * 100 + 0.10 * 200 = 25; observed 4 + 12 = 16; 25 / 16 - 1
    assert row["realised_bias"] == pytest.approx(25 / 16 - 1)
    assert row["tested_bias"] == pytest.approx(0.05) and row["n_rows"] == 2
    assert row["drift"] is True and out["status"] == "drift"


def test_a_bias_within_the_tolerance_is_ok_and_a_baseline_has_no_tested_value():
    previous = _pred([
        dict(specialty_name="A", target_month_id=202204, horizon=1, point_share=0.05,
             known_bias=0.05, model="logistic"),
        dict(specialty_name="A", target_month_id=202206, horizon=6, point_share=0.05,
             known_bias=np.nan, model="last_month"),
    ])
    seg = _seg([(202204, "A", 5, 100), (202206, "A", 1, 100)])  # realised +0% at 1, +400% at 6
    monthly = pd.DataFrame({"month_id": [202203, 202206], "visit_share": [0.04, 0.05]})
    out = drift_report(previous, seg, monthly, pd.DataFrame())
    assert out["segment"]["1"]["drift"] is False  # 0.0 against a tested 0.05: within 5 points
    assert out["segment"]["6"]["tested_bias"] is None and out["segment"]["6"]["drift"] is False
    assert out["status"] == "ok"


def test_the_national_error_is_compared_with_the_tested_error():
    previous = pd.DataFrame([dict(run_month_id=202203, level="national", target_month_id=202204,
                                  horizon=1, point_share=0.030, model="last_month")])
    monthly = pd.DataFrame({"month_id": [202203, 202204], "visit_share": [0.030, 0.025]})

    def scores(mae):
        return pd.DataFrame([dict(task="T1", scenario="main", horizon=1, method="last_month",
                                  score=mae)])

    drifted = drift_report(previous, _seg([]), monthly, scores(0.125))  # 0.5 pp > 2 x 0.125
    assert drifted["national"]["1"]["error_points"] == pytest.approx(0.5)
    assert drifted["national"]["1"]["drift"] is True and drifted["status"] == "drift"
    fine = drift_report(previous, _seg([]), monthly, scores(0.30))  # 0.5 pp <= 2 x 0.30
    assert fine["national"]["1"]["drift"] is False and fine["status"] == "ok"


def test_nothing_to_compare_before_a_new_month_and_without_a_previous_run():
    previous = _pred([dict(specialty_name="A", target_month_id=202204, horizon=1,
                           point_share=0.05, known_bias=0.05, model="logistic")])
    same = pd.DataFrame({"month_id": [202202, 202203], "visit_share": [0.04, 0.05]})
    out = drift_report(previous, _seg([]), same, pd.DataFrame())
    assert out["status"] == "nothing_to_compare" and "no month has arrived" in out["note"]
    assert drift_report(None, _seg([]), same, pd.DataFrame())["status"] == "no_previous_run"
    assert drift_report(previous.iloc[0:0], _seg([]), same, pd.DataFrame())["status"] == (
        "no_previous_run"
    )
    # a new month arrived but none of the earlier predictions' targets is in the data yet
    later = pd.DataFrame({"month_id": [202203, 202204], "visit_share": [0.04, 0.05]})
    pending = _pred([dict(specialty_name="A", target_month_id=202209, horizon=6,
                          point_share=0.05, known_bias=np.nan, model="last_month")])
    assert drift_report(pending, _seg([]), later, pd.DataFrame())["status"] == "nothing_to_compare"


# ------------------------------------------------------------------ the stage


def _staged_engine(tmp_path, monthly, scores):
    engine = create_engine(f"sqlite:///{(tmp_path / 'staged.db').as_posix()}")
    with engine.begin() as conn:
        monthly.to_sql("gold_visit_share_monthly", conn, index=False)
        scores.to_sql("model_scores", conn, index=False)
    return engine


def test_the_stage_reports_freshness_and_drift_and_flags_attention(tmp_path, monkeypatch):
    seg = _seg([(202203, "A", 5, 100), (202204, "A", 4, 100)])
    monkeypatch.setattr("oa_market_intelligence.features.segment.load_segment_gold", lambda e: seg)
    monthly = pd.DataFrame({"month_id": [202203, 202204], "visit_share": [0.05, 0.0501]})
    engine = _staged_engine(tmp_path, monthly, pd.DataFrame(
        [dict(task="T1", scenario="main", horizon=1, method="last_month", score=0.125)]))
    previous = tmp_path / "previous.db"
    prev_engine = create_engine(f"sqlite:///{previous.as_posix()}")
    pd.DataFrame([dict(run_month_id=202203, level="national", target_month_id=202204, horizon=1,
                       point_share=0.05, model="last_month")]).to_sql(
        "pred_batch", prev_engine, index=False)
    prev_engine.dispose()
    out = run_monitoring(engine, previous, today=date(2022, 5, 10))
    assert out["freshness"]["status"] == "fresh" and out["freshness"]["age_days"] == 10
    assert out["drift"]["status"] == "ok"  # a 0.01 point error, far inside 2 x 0.125
    assert out["status"] == "ok"
    stale = run_monitoring(engine, previous, today=date(2023, 5, 10))
    assert stale["freshness"]["status"] == "stale" and stale["status"] == "attention"


def test_the_stage_without_a_previous_database_and_with_a_failure(tmp_path, monkeypatch):
    seg = _seg([(202203, "A", 5, 100)])
    monkeypatch.setattr("oa_market_intelligence.features.segment.load_segment_gold", lambda e: seg)
    monthly = pd.DataFrame({"month_id": [202203], "visit_share": [0.05]})
    engine = _staged_engine(tmp_path, monthly, pd.DataFrame(
        [dict(task="T1", scenario="main", horizon=1, method="last_month", score=0.1)]))
    out = run_monitoring(engine, None, today=date(2022, 4, 10))
    assert out["drift"]["status"] == "no_previous_run" and out["status"] == "ok"
    broken = create_engine("sqlite://")  # no tables at all: the stage reports, it does not raise
    failed = run_monitoring(broken, None)
    assert failed["status"] == "error" and "error" in failed


# ------------------------------------------------------------------ versions


def test_the_data_version_changes_only_when_an_extract_changes():
    a = {"file_name": "x.xlsx", "sha256": "1" * 64}
    b = {"file_name": "y.xlsx", "sha256": "2" * 64}
    version = data_version_from_files([a, b])
    assert version == data_version_from_files([b, a]) == data_version_from_files([a, b, a])
    assert len(version) == 12 and version != UNKNOWN
    assert data_version_from_files([a, {**b, "sha256": "3" * 64}]) != version
    assert data_version_from_files([]) == UNKNOWN


def test_the_data_version_of_a_warehouse_and_the_run_versions(tmp_path):
    engine = create_engine("sqlite://")
    assert data_version(engine) == UNKNOWN  # no bronze table
    files = [{"file_name": "x.xlsx", "sha256": "1" * 64}]
    pd.DataFrame(files).to_sql("bronze_ingest_files", engine, index=False)
    assert data_version(engine) == data_version_from_files(files)
    requirements = tmp_path / "r.txt"
    requirements.write_text("pandas==3.1.0\n")
    assert environment_version(requirements) == environment_version(requirements) != UNKNOWN
    assert environment_version(tmp_path / "missing.txt") == UNKNOWN
    versions = run_versions(files)
    assert set(versions) == {"data", "code", "environment"}
    assert versions["data"] == data_version_from_files(files)
