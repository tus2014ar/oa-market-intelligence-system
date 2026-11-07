"""Tests for experiment tracking and the model registry (src/.../tracking.py, DL-125).

The first half needs only pandas: what each run and each registered model would contain, the
integrity check on the saved numbers, the safety rules for the store folder and the command. The
second half builds a real MLflow store (skipped where MLflow is not installed, as in the main CI
job; the `tracking` job installs it) and checks what a person browsing it would see.
"""

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from oa_market_intelligence import tracking as tr
from oa_market_intelligence.serving import data

needs_mlflow = pytest.mark.skipif(
    importlib.util.find_spec("mlflow") is None, reason="MLflow is not installed"
)

SCORE_COLUMNS = [
    "task", "horizon", "scenario", "method", "kind", "metric", "higher_is_better", "score",
    "is_best_baseline", "gain_over_best_baseline", "gain_lower", "gain_upper", "promoted",
    "serving", "n_test_months", "months_better", "coverage_90", "precision", "recall", "f1",
    "n_alerts", "bias_vs_actual",
]  # fmt: skip
NAN = float("nan")


def score_row(**changes):
    row = dict.fromkeys(SCORE_COLUMNS, NAN)
    row.update(
        task="T2", horizon=1, scenario="main", method="logistic", kind="model", metric="log_loss",
        higher_is_better=0, score=0.3, is_best_baseline=0, gain_over_best_baseline=0.01,
        gain_lower=0.005, gain_upper=0.02, promoted=1.0, serving=1, n_test_months=43,
        bias_vs_actual=0.055,
    )  # fmt: skip
    row.update(changes)
    return row


def scores_frame(*rows):
    return pd.DataFrame([score_row(**r) for r in rows], columns=SCORE_COLUMNS)


def artifact(model_id, task, horizon, model="logistic", parameters=None, bias=0.05, **changes):
    text = (
        "{}"
        if parameters is None and model != "logistic"
        else json.dumps(
            parameters or {"coef": [0.5, -0.25], "intercept": 0.1},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    row = {
        "model_id": model_id, "task": task, "horizon": horizon, "model": model,
        "model_version": f"v-{model_id}", "run_month_id": 202507, "trained_on_rows": 1234,
        "data_version": "28b9a7bd3234", "library": "scikit-learn 1.9.1",
        "known_bias": bias, "artifact_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "parameters": text,
    }  # fmt: skip
    row.update(changes)
    return row


def artifacts_frame(*rows):
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ what a run contains


def test_each_score_row_becomes_one_run_in_its_tasks_experiment():
    scores = scores_frame(
        {"task": "T1", "method": "last_month", "kind": "baseline", "metric": "mae_pp",
         "score": 0.12, "is_best_baseline": 1, "gain_over_best_baseline": NAN, "promoted": NAN,
         "gain_lower": NAN, "gain_upper": NAN, "bias_vs_actual": NAN},
        {"task": "T3", "horizon": 1, "method": "logistic", "metric": "pr_auc", "score": 0.4,
         "precision": 0.49, "recall": 0.5, "f1": 0.49, "n_alerts": 26.0},
    )  # fmt: skip
    first, second = tr.run_specs(scores, data_version="dv", code_version="abc1234")
    assert first.experiment == "T1 national share" and second.experiment == "T3 decline alert"
    assert first.name == "T1 h1 main last_month" and second.name == "T3 h1 main logistic"
    assert first.params["method"] == "last_month" and first.params["kind"] == "baseline"
    assert first.params["horizon"] == "1" and first.params["scenario"] == "main"
    assert second.params["n_alerts"] == "26" and second.params["n_test_months"] == "43"


def test_a_missing_value_is_left_out_not_recorded_as_zero_or_nan():
    first, second = tr.run_specs(
        scores_frame({"task": "T1", "method": "last_month", "gain_over_best_baseline": NAN,
                      "gain_lower": NAN, "gain_upper": NAN, "bias_vs_actual": NAN}, {}),
        data_version="dv", code_version="c",
    )  # fmt: skip
    assert "gain_over_best_baseline" not in first.metrics and "bias_vs_actual" not in first.metrics
    assert first.metrics["score"] == 0.3
    assert second.metrics["bias_vs_actual"] == 0.055
    assert all(
        v == v and abs(v) != float("inf") for s in (first, second) for v in s.metrics.values()
    )


def test_the_score_is_also_recorded_under_its_own_metric_name():
    (spec,) = tr.run_specs(scores_frame({"metric": "log_loss", "score": 0.31}),
                           data_version="dv", code_version="c")  # fmt: skip
    assert spec.metrics["score"] == spec.metrics["log_loss"] == 0.31


def test_the_tags_say_what_is_in_service_and_which_data_and_code_made_the_run():
    (spec,) = tr.run_specs(scores_frame({"serving": 1, "promoted": 1.0, "is_best_baseline": 0}),
                           data_version="28b9a7bd3234", code_version="acd52c4b9f")  # fmt: skip
    assert spec.tags["serving"] == "1" and spec.tags["promoted"] == "1"
    assert spec.tags["best_baseline"] == "0" and spec.tags["scenario"] == "main"
    assert spec.tags["data_version"] == "28b9a7bd3234" and spec.tags["code_version"] == "acd52c4b9f"
    (unset,) = tr.run_specs(scores_frame({"promoted": NAN}), data_version="d", code_version="c")
    assert unset.tags["promoted"] == "unknown"  # a baseline was never promoted or refused


def test_a_score_for_an_unknown_task_is_refused():
    with pytest.raises(tr.TrackingError, match="unknown task"):
        tr.run_specs(scores_frame({"task": "T9"}), data_version="d", code_version="c")


# -------------------------------------------------- what a registered model contains


def test_model_names_say_the_task_and_the_horizon():
    assert tr.model_name("segment", 3) == "oa-segment-share-3m"
    assert tr.model_name("alert", 1) == "oa-decline-alert-1m"
    with pytest.raises(tr.TrackingError):
        tr.model_name("national", 1)


def test_each_saved_model_becomes_a_spec_with_its_checksum_and_its_known_bias():
    rows = artifacts_frame(
        artifact("segment:1", "segment", 1, bias=0.0549),
        artifact("segment:6", "segment", 6, model="last_month", bias=None),
        artifact("alert:1", "alert", 1, bias=None),
    )
    specs = {s.model_id: s for s in tr.model_specs(rows)}
    one = specs["segment:1"]
    assert one.name == "oa-segment-share-1m" and one.tags["known_bias_pct"] == "5.49"
    assert one.tags["artifact_sha256"] == hashlib.sha256(one.parameters.encode()).hexdigest()
    assert one.tags["trained_on_rows"] == "1234" and one.tags["library"] == "scikit-learn 1.9.1"
    assert one.tags["model_version"] == "v-segment:1" and "logistic" in one.description
    rule = specs["segment:6"]
    assert rule.parameters == "{}" and rule.tags["known_bias_pct"] == "none recorded"
    assert "a rule" in rule.description
    assert specs["alert:1"].name == "oa-decline-alert-1m"


def test_saved_numbers_that_do_not_match_their_recorded_checksum_are_never_registered():
    good = artifact("segment:1", "segment", 1)
    for tampered in (
        {**good, "parameters": good["parameters"].replace("0.5", "0.6")},
        {**good, "artifact_sha256": "0" * 64},
        {**good, "parameters": ""},
    ):
        with pytest.raises(tr.TrackingError, match="do not match their recorded SHA-256"):
            tr.model_specs(artifacts_frame(good, tampered))  # one bad row refuses the lot


# -------------------------------------------------- the store folder and the command


def test_a_store_is_replaced_only_if_it_is_ours_and_a_stranger_folder_is_left_alone(tmp_path):
    stranger = tmp_path / "my_files"
    stranger.mkdir()
    (stranger / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(tr.TrackingError, match="not a tracking store"):
        tr._prepare_store(stranger)
    assert (stranger / "notes.txt").read_text(encoding="utf-8") == "keep me"  # untouched

    ours = tmp_path / "store"
    tr._prepare_store(ours)
    assert (ours / tr.MARKER).exists()
    (ours / "old.db").write_text("old", encoding="utf-8")
    tr._prepare_store(ours)  # ours: replaced
    assert (ours / tr.MARKER).exists() and not (ours / "old.db").exists()


def test_a_warehouse_without_the_tables_or_with_two_data_versions_is_refused(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(tr.TrackingError, match="lacks model_scores"):
        tr._read(empty)
    two = tmp_path / "two.db"
    con = sqlite3.connect(two)
    scores_frame({}).to_sql("model_scores", con, index=False)
    artifacts_frame(artifact("segment:1", "segment", 1)).to_sql("model_artifacts", con, index=False)
    pd.DataFrame({"data_version": ["a", "b"]}).to_sql("pred_batch", con, index=False)
    con.close()
    with pytest.raises(tr.TrackingError, match="exactly one data version"):
        tr._read(two)


def test_the_command_says_why_it_refuses_and_exits_with_a_failing_status(tmp_path, capsys):
    code = tr.main(["summary", "--store", str(tmp_path / "nothing")])
    assert code == 1 and "run oa-track build first" in capsys.readouterr().err


def test_without_mlflow_the_command_says_how_to_install_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mlflow", None)  # makes `import mlflow` fail
    code = tr.main(["build", "--db", str(tmp_path / "x.db"), "--store", str(tmp_path / "s")])
    assert code == 1 and "pip install -r requirements-tracking.txt" in capsys.readouterr().err


# ------------------------------------------------------------------ a real MLflow store


def promotion_log(**changes) -> pd.DataFrame:
    """A small promotion log: one sanity check, two reproducibility checks, one stability check."""
    base = {
        "decision": "promote", "champion_origin": 202507, "challenger_origin": 202507,
        "champion_data_version": "28b9a7bd3234", "challenger_data_version": "28b9a7bd3234",
        "override_reason": None,
    }  # fmt: skip
    rows = [
        {"scope": "sanity", "check": "forecasts_finite_and_between_0_and_1", "horizon": None,
         "value": None, "limit": None, "passed": 1, "detail": "339 rows"},
        {"scope": "reproducibility", "check": "forecasts_identical", "horizon": None,
         "value": 2.8e-11, "limit": 1e-6, "passed": 1, "detail": "largest difference"},
        {"scope": "reproducibility", "check": "alert_flags_identical", "horizon": None,
         "value": 0.0, "limit": 0.0, "passed": 1, "detail": ""},
        {"scope": "stability", "check": "weighted_mean_change_pp", "horizon": 1,
         "value": 0.2, "limit": 0.6, "passed": 1, "detail": "volume-weighted"},
    ]  # fmt: skip
    return pd.DataFrame([{**base, **changes, **r} for r in rows])


def make_warehouse(tmp_path, *, tamper=False, with_log=True):
    """A small warehouse with the tables tracking reads, and a run log with a code version."""
    db = tmp_path / "warehouse.db"
    scores = scores_frame(
        {"task": "T1", "method": "last_month", "kind": "baseline", "metric": "mae_pp",
         "score": 0.12, "serving": 1, "is_best_baseline": 1, "promoted": NAN},
        {"task": "T2", "horizon": 1, "method": "logistic", "serving": 1, "score": 0.31},
        {"task": "T2", "horizon": 1, "method": "logistic", "scenario": "covid_removed",
         "serving": 1, "score": 0.33},
        {"task": "T2", "horizon": 1, "method": "gbm", "serving": 0, "score": 0.35},
        {"task": "T2", "horizon": 3, "method": "logistic", "serving": 1, "score": 0.32},
        {"task": "T2", "horizon": 6, "method": "last_month", "kind": "baseline", "serving": 1,
         "score": 0.4, "is_best_baseline": 1, "promoted": NAN},
        {"task": "T3", "horizon": 1, "method": "logistic", "metric": "pr_auc", "score": 0.4,
         "precision": 0.49, "recall": 0.5, "f1": 0.49, "n_alerts": 26.0},
    )  # fmt: skip
    arts = artifacts_frame(
        artifact("segment:1", "segment", 1, bias=0.0549),
        artifact("segment:3", "segment", 3, bias=0.1092),
        artifact("segment:6", "segment", 6, model="last_month", bias=None),
        artifact("alert:1", "alert", 1, bias=None),
    )
    if tamper:
        arts.loc[0, "parameters"] = arts.loc[0, "parameters"].replace("0.5", "9.9")
    con = sqlite3.connect(db)
    scores.to_sql("model_scores", con, index=False)
    arts.to_sql("model_artifacts", con, index=False)
    pd.DataFrame({"data_version": ["28b9a7bd3234"]}).to_sql("pred_batch", con, index=False)
    if with_log:
        promotion_log().to_sql("promotion_log", con, index=False)
    con.close()
    (tmp_path / "run_log.jsonl").write_text(
        json.dumps({"versions": {"code": "acd52c4b9f"}}) + "\n", encoding="utf-8"
    )
    return db


def client_for(store):
    from mlflow import MlflowClient

    uri = tr._tracking_uri(store)
    return MlflowClient(tracking_uri=uri, registry_uri=uri)


@needs_mlflow
def test_a_built_store_has_an_experiment_per_task_and_a_run_per_score_row(tmp_path):
    db = make_warehouse(tmp_path)
    result = tr.build_store(db, tmp_path / "store")
    assert result["runs"] == 7 and result["promotion_runs"] == 1
    assert result["experiments"] == 5  # three tasks, the registry and the promotion decisions
    assert result["data_version"] == "28b9a7bd3234" and result["code_version"] == "acd52c4b9f"
    client = client_for(tmp_path / "store")
    names = {e.name for e in client.search_experiments()}
    assert {
        "T1 national share",
        "T2 segment share",
        "T3 decline alert",
        tr.REGISTRY_EXPERIMENT,
    } <= names
    t2 = next(e for e in client.search_experiments() if e.name == "T2 segment share")
    runs = client.search_runs([t2.experiment_id], max_results=100)
    assert len(runs) == 5
    main_logistic = next(
        r for r in runs
        if r.data.params["method"] == "logistic" and r.data.params["scenario"] == "main"
        and r.data.params["horizon"] == "1"
    )  # fmt: skip
    assert main_logistic.data.metrics["score"] == pytest.approx(0.31)
    assert main_logistic.data.metrics["log_loss"] == pytest.approx(0.31)
    assert main_logistic.data.metrics["bias_vs_actual"] == pytest.approx(0.055)
    assert (
        main_logistic.data.tags["serving"] == "1"
        and main_logistic.data.tags["code_version"] == "acd52c4b9f"
    )
    assert main_logistic.info.status == "FINISHED"


@needs_mlflow
def test_each_model_in_service_is_registered_with_a_champion_that_carries_its_checksum(tmp_path):
    db = make_warehouse(tmp_path)
    tr.build_store(db, tmp_path / "store")
    listed = {m["name"]: m for m in tr.summary(tmp_path / "store")}
    assert set(listed) == {
        "oa-segment-share-1m", "oa-segment-share-3m", "oa-segment-share-6m", "oa-decline-alert-1m",
    }  # fmt: skip
    arts = pd.read_sql("SELECT * FROM model_artifacts", sqlite3.connect(db)).set_index("model_id")
    for name, item in listed.items():
        tags = item["tags"]
        row = arts.loc[tags["model_id"]]
        assert item["champion_version"] == 1
        assert tags["artifact_sha256"] == row["artifact_sha256"]  # what the API serves
        assert tags["model_version"] == row["model_version"]
        assert tags["trained_on_rows"] == "1234" and tags["data_version"] == "28b9a7bd3234"
    assert listed["oa-segment-share-1m"]["tags"]["known_bias_pct"] == "5.49"


@needs_mlflow
def test_the_registered_artifact_is_exactly_the_saved_numbers_and_the_evidence_run_is_linked(
    tmp_path,
):
    db = make_warehouse(tmp_path)
    tr.build_store(db, tmp_path / "store")
    client = client_for(tmp_path / "store")
    arts = pd.read_sql("SELECT * FROM model_artifacts", sqlite3.connect(db)).set_index("model_id")
    version = client.get_model_version_by_alias("oa-segment-share-1m", tr.CHAMPION)
    path = client.download_artifacts(version.run_id, "model/parameters.json", str(tmp_path / "out"))
    text = Path(path).read_text(encoding="utf-8")
    assert text == arts.loc["segment:1", "parameters"]  # the very bytes that were hashed
    assert hashlib.sha256(text.encode()).hexdigest() == version.tags["artifact_sha256"]
    evidence = client.get_run(version.tags["evidence_run_id"])  # the run that scored this model
    assert evidence.data.params["task"] == "T2" and evidence.data.params["method"] == "logistic"
    assert evidence.data.params["scenario"] == "main" and evidence.data.params["horizon"] == "1"
    rule = client.get_model_version_by_alias("oa-segment-share-6m", tr.CHAMPION)
    scored = client.get_run(rule.tags["evidence_run_id"])
    assert scored.data.params["method"] == "last_month" and scored.data.params["horizon"] == "6"


def run_cli(*args):
    """The real command, in a fresh process (as a person runs it)."""
    src = Path(tr.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(src)}
    return subprocess.run(
        [sys.executable, "-m", "oa_market_intelligence.tracking", *args],
        check=True, capture_output=True, text=True, env=env,
    )  # fmt: skip


@needs_mlflow
def test_running_the_command_again_replaces_the_store_instead_of_adding_to_it(tmp_path):
    db = make_warehouse(tmp_path)
    store = tmp_path / "store"
    first = run_cli("build", "--db", str(db), "--store", str(store))
    again = run_cli("build", "--db", str(db), "--store", str(store))
    assert "recorded 7 runs in 5 experiments and 4 models" in first.stdout
    assert "recorded 7 runs in 5 experiments and 4 models" in again.stdout
    client = client_for(store)
    t2 = next(e for e in client.search_experiments() if e.name == "T2 segment share")
    assert len(client.search_runs([t2.experiment_id], max_results=100)) == 5  # not 10
    assert len(client.search_model_versions("name='oa-segment-share-1m'")) == 1
    assert "champion v1" in run_cli("summary", "--store", str(store)).stdout


@needs_mlflow
def test_a_second_build_of_the_same_store_in_one_process_is_refused_not_attempted(tmp_path):
    db = make_warehouse(tmp_path)
    tr.build_store(db, tmp_path / "store")
    with pytest.raises(tr.TrackingError, match="already built in this process"):
        tr.build_store(db, tmp_path / "store")
    tr.build_store(db, tmp_path / "other")  # a different store is fine


@needs_mlflow
def test_a_tampered_model_stops_the_build_before_anything_is_written(tmp_path):
    db = make_warehouse(tmp_path, tamper=True)
    store = tmp_path / "store"
    with pytest.raises(tr.TrackingError, match="do not match their recorded SHA-256"):
        tr.build_store(db, store)
    assert not store.exists()  # nothing was created, so nothing can be browsed or promoted


@needs_mlflow
def test_a_folder_that_is_not_a_store_is_not_overwritten_by_a_build(tmp_path):
    db = make_warehouse(tmp_path)
    other = tmp_path / "precious"
    other.mkdir()
    (other / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(tr.TrackingError, match="not a tracking store"):
        tr.build_store(db, other)
    assert (other / "file.txt").exists()


@needs_mlflow
@pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")
def test_the_published_warehouse_builds_a_store_with_every_score_and_the_four_champions(
    tmp_path, capsys
):
    store = tmp_path / "real"
    result = tr.build_store(data.DB_PATH, store)
    con = sqlite3.connect(f"file:{data.DB_PATH.as_posix()}?mode=ro", uri=True)
    n_scores = con.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0]
    arts = pd.read_sql("SELECT model_id, artifact_sha256, model_version FROM model_artifacts", con)
    con.close()
    assert result["runs"] == n_scores == 222 and result["data_version"] == "28b9a7bd3234"
    assert result["promotion_runs"] == 1 and result["experiments"] == 5
    listed = tr.summary(store)
    assert len(listed) == 4
    by_id = {m["tags"]["model_id"]: m["tags"] for m in listed}
    for row in arts.itertuples():
        assert by_id[row.model_id]["artifact_sha256"] == row.artifact_sha256  # equals what serves
        assert by_id[row.model_id]["model_version"] == row.model_version
    assert tr.main(["summary", "--store", str(store)]) == 0
    assert capsys.readouterr().out.count("champion v1") == 4


# ------------------------------------------------------------------ the promotion decisions


def test_a_promotion_log_becomes_one_run_with_every_check_its_limit_and_the_decision():
    spec = tr.promotion_spec(promotion_log(), data_version="dv", code_version="cv")
    assert spec.experiment == "Promotion decisions"
    assert spec.name == "promotion 202507 -> 202507: promote"
    assert spec.params["decision"] == "promote" and spec.params["challenger_origin"] == "202507"
    assert spec.metrics["checks_total"] == 4 and spec.metrics["checks_failed"] == 0
    assert spec.metrics["reproducibility.forecasts_identical"] == 2.8e-11
    assert spec.metrics["reproducibility.forecasts_identical.limit"] == 1e-6
    assert spec.metrics["stability.weighted_mean_change_pp.h1"] == 0.2  # a horizon in the name
    assert spec.metrics["sanity.forecasts_finite_and_between_0_and_1.passed"] == 1.0
    assert "sanity.forecasts_finite_and_between_0_and_1" not in spec.metrics  # no value to record
    assert spec.tags["decision"] == "promote" and spec.tags["code_version"] == "cv"


def test_a_refused_or_overridden_decision_records_the_failures_and_the_reason():
    log = promotion_log(decision="override", override_reason="reviewed: library upgrade")
    log.loc[1, "passed"] = 0
    spec = tr.promotion_spec(log, data_version="dv", code_version="cv")
    assert spec.metrics["checks_failed"] == 1
    assert spec.metrics["reproducibility.forecasts_identical.passed"] == 0.0
    assert spec.params["override_reason"] == "reviewed: library upgrade"
    assert spec.name.endswith("override")


def test_a_warehouse_with_no_promotion_log_gives_no_promotion_run():
    assert tr.promotion_spec(pd.DataFrame(), data_version="d", code_version="c") is None
    assert tr.promotion_spec(None, data_version="d", code_version="c") is None


@needs_mlflow
def test_the_store_shows_the_decision_beside_the_models_and_an_older_warehouse_still_builds(
    tmp_path,
):
    tr.build_store(make_warehouse(tmp_path), tmp_path / "with")
    client = client_for(tmp_path / "with")
    gate = next(e for e in client.search_experiments() if e.name == "Promotion decisions")
    (run,) = client.search_runs([gate.experiment_id])
    assert run.data.params["decision"] == "promote" and run.data.metrics["checks_failed"] == 0
    assert run.data.metrics["reproducibility.forecasts_identical"] == pytest.approx(2.8e-11)
    older = tmp_path / "older"
    older.mkdir()
    result = tr.build_store(make_warehouse(older, with_log=False), older / "store")
    assert result["experiments"] == 4 and result["promotion_runs"] == 0  # no log, no experiment
