"""Tests for the modelling runner (src/.../modeling/run_modelling.py): the JSON writer, the counts
mode and the wiring of every task, with small synthetic inputs."""

import json

import numpy as np
import pytest

from modelling_fixtures import make_inputs, make_series
from oa_market_intelligence.features.groups import columns_for
from oa_market_intelligence.modeling import alerts, run_modelling, segment_models
from oa_market_intelligence.modeling.run_modelling import (
    clean,
    counts,
    serving_map,
    write_results,
)


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
    gbm, rf = [{"max_depth": 3, "learning_rate": 0.1}], [{"max_depth": 4, "min_samples_leaf": 20}]
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "gbm", gbm)
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "rf", rf)
    monkeypatch.setitem(segment_models.RF_BASE, "n_estimators", 10)
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "gbm", [{"max_depth": 3, "learning_rate": 0.1}])
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "rf", [{"max_depth": 4, "min_samples_leaf": 20}])


def _with_g4(rows):
    """The real feature table carries the G4 columns the models use; the synthetic one gets some."""
    rng = np.random.default_rng(1)
    out = rows.copy()
    for column in columns_for(("G4",), target="T2"):
        out[column] = rng.normal(size=len(out))
    return out


def test_clean_drops_private_keys_and_makes_json_safe():
    value = {"a": np.float64(1.5), "b": np.int64(3), "_hidden": object(), "c": [np.nan, np.inf],
             "d": {"e": np.bool_(True), "_x": 1}, "f": (1, 2)}
    assert clean(value) == {"a": 1.5, "b": 3, "c": [None, None], "d": {"e": True}, "f": [1, 2]}


def test_write_results_is_deterministic_and_valid_json(tmp_path):
    results = {"b": 2.0, "a": {"z": np.float64(0.1), "y": float("nan")}}
    path = tmp_path / "out" / "results.json"
    write_results(results, path)
    first = path.read_bytes()
    write_results(results, path)
    assert path.read_bytes() == first
    assert json.loads(first) == {"a": {"y": None, "z": 0.1}, "b": 2.0}
    assert first.index(b'"a"') < first.index(b'"b"')  # sorted keys


def test_counts_need_no_model_and_match_the_plans_test_months():
    rows, outcomes, _ = make_inputs(n_months=44)
    out = counts(rows, outcomes)
    assert out["1"]["t2_test_months"] == 44 - 2 - 24
    assert out["3"]["t2_test_months"] == out["1"]["t2_test_months"] - 2
    assert out["6"]["t2_test_months"] == out["1"]["t2_test_months"] - 5
    for h in ("1", "3", "6"):
        assert out[h]["t3_test_declines"] <= out[h]["t3_declines"] <= out[h]["t3_scored_rows"]
        assert 0 < out[h]["t3_decline_share"] < 1


def test_serving_map_reads_the_main_scenario():
    results = {"1": {"scenarios": {"main": {"serving": "ridge"}}},
               "3": {"scenarios": {"main": {"serving": "last_month"}}}}
    assert serving_map(results) == {1: "ridge", 3: "last_month"}


def test_run_all_wires_every_task_and_writes_the_file(monkeypatch, tmp_path):
    rows, outcomes, seg = make_inputs(n_months=30)
    rows = _with_g4(rows)
    series = make_series(72)
    g5_columns = columns_for(("G5",), target="T2")
    rng = np.random.default_rng(0)
    rows_g5 = rows.copy()
    for column in g5_columns:
        rows_g5[column] = rng.normal(size=len(rows_g5))
    monkeypatch.setattr(
        run_modelling, "load_inputs",
        lambda engine: {"rows": rows, "seg": seg, "outcomes": outcomes, "series": series},
    )
    monkeypatch.setattr(run_modelling, "_rows_with_g5", lambda engine, seg_: rows_g5)
    results = run_modelling.run_all(
        None, horizons=(1,), tasks=("T1", "T2", "T3", "G5", "demonstration"),
        n_boot=30, scenarios=("main",),
    )
    # no demonstration, which needs h = 1, 3 and 6
    assert set(results) == {"meta", "counts", "T1", "T2", "T3", "G5"}
    assert results["T2"]["1"]["scenarios"]["main"]["n_test_months"] == 30 - 2 - 24
    assert results["T3"]["1"]["scenarios"]["main"]["n_test_months"] == 30 - 2 - 24
    assert results["T1"]["1"]["scenarios"]["main"]["n_test_months"] == 72 - 23 - 1
    assert results["G5"]["1"]["lower_percentile"] == pytest.approx(2.5)
    assert results["meta"]["g4_columns"] == list(columns_for(("G4",), target="T2"))
    path = tmp_path / "results.json"
    write_results(results, path)
    text = path.read_text()
    assert "_forecasts" not in text and "_draws" not in text
    assert json.loads(text)["T3"]["1"]["scenarios"]["main"]["serving"]


def test_run_all_adds_the_demonstration_when_all_horizons_are_run(monkeypatch):
    rows, outcomes, seg = make_inputs(n_months=60)
    rows = _with_g4(rows)
    series = make_series(72)
    monkeypatch.setattr(
        run_modelling, "load_inputs",
        lambda engine: {"rows": rows, "seg": seg, "outcomes": outcomes, "series": series},
    )
    monkeypatch.setattr(run_modelling, "counts", lambda *a, **k: {})
    # the tasks themselves are tested elsewhere: stub them with fixed serving models so that the
    # demonstration (trained models included) is what runs for real
    monkeypatch.setattr(
        run_modelling, "run_t1_horizon",
        lambda series, h, **k: {"scenarios": {"main": {"serving": "ridge"}}},
    )
    monkeypatch.setattr(
        run_modelling, "run_t2_horizon",
        lambda rows, outcomes, h, **k: {"scenarios": {"main": {"serving": "logistic"}}},
    )
    results = run_modelling.run_all(
        None, horizons=(1, 3, 6), tasks=("T1", "T2", "demonstration"), n_boot=20,
    )
    demo = results["demonstration"]
    assert demo["serving_t1"] == {1: "ridge", 3: "ridge", 6: "ridge"}
    assert demo["serving_t2"] == {1: "logistic", 3: "logistic", 6: "logistic"}
    assert {r["cutoff"] for r in demo["national"]} == set(run_modelling.CUTOFFS)
    assert len(demo["national"]) == 12
    assert {r["cutoff"] for r in demo["segment"]} == set(run_modelling.CUTOFFS)
    assert {r["step"] for r in demo["segment"]} == {1, 2, 3, 4, 5, 6}
