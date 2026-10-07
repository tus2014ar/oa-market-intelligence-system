"""Tests for the stored Phase 4 results (src/.../serving/results.py).

The publish step computes the Q1 and Q3 findings, the segment model, the forecast, the
direction panel and the monitoring status, and stores them in the database file. These tests
check, on simulated data with known structure, that each computation returns what the site and
the Claude tools expect, that everything is strictly JSON-safe, and that the monitoring logic
behaves as specified; one real-data test runs the lot at reduced precision on the committed
warehouse.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from oa_market_intelligence.serving.results import (
    PRECISIONS,
    clean,
    compute_all,
    compute_findings_from_data,
    compute_forecast_from_series,
    compute_monitoring_from,
    compute_segment_model_from_data,
    load_result,
    monitoring_status,
    next_month_id,
    store_result,
)

REAL_DB = Path(__file__).resolve().parents[1] / "data" / "published" / "warehouse.db"


def _safe(payload):
    return json.dumps(payload, allow_nan=False)


def _calendar(n, start=201908):
    out, year, month = [], start // 100, start % 100
    for _ in range(n):
        out.append(year * 100 + month)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def test_clean_makes_numpy_pandas_and_nan_values_strictly_json_safe():
    payload = {
        "a": np.int64(3), "b": np.float64(0.5), "c": np.nan, "d": [np.float32(1.5), float("inf")],
        "e": pd.Timestamp("2020-03-01"), "f": (1, 2), "g": np.array([1, 2]), "h": np.bool_(True),
    }
    out = clean(payload)
    assert out == {"a": 3, "b": 0.5, "c": None, "d": [1.5, None], "e": "2020-03",
                   "f": [1, 2], "g": [1, 2], "h": True}
    _safe(out)


@pytest.mark.parametrize("month, expected", [(202508, 202509), (202512, 202601), (201912, 202001)])
def test_the_next_month_rolls_over_the_year(month, expected):
    assert next_month_id(month) == expected


def test_a_stored_result_round_trips_and_is_replaced_by_the_next_store():
    engine = create_engine("sqlite:///:memory:")
    assert load_result(engine, "findings") is None  # nothing stored yet
    store_result(engine, "findings", {"x": [1, 2], "y": None}, precision="fast")
    loaded = load_result(engine, "findings")
    assert loaded["x"] == [1, 2] and loaded["precision"] == "fast" and "built_at" in loaded
    store_result(engine, "findings", {"x": [3]}, precision="full")
    assert load_result(engine, "findings")["x"] == [3]
    assert load_result(engine, "findings")["precision"] == "full"
    assert load_result(engine, "something_else") is None


def test_the_precision_presets_have_the_same_keys_and_fast_is_smaller():
    assert set(PRECISIONS["full"]) == set(PRECISIONS["fast"])
    assert PRECISIONS["full"]["a_n_boot"] == 2000 and PRECISIONS["full"]["n_boot_adopt"] == 1000
    for key, value in PRECISIONS["fast"].items():
        if isinstance(value, (int, float)):
            assert value <= PRECISIONS["full"][key]


@pytest.mark.parametrize(
    "direction_flag, forecast_alarm, status",
    [(False, False, "ok"), (True, False, "review"), (False, True, "review"),
     (True, True, "review")],
)
def test_the_status_is_review_if_either_monitor_has_a_flag(direction_flag, forecast_alarm, status):
    out = monitoring_status(direction_flag, forecast_alarm)
    assert out["status"] == status
    assert (out["banner"] is None) == (status == "ok")
    if forecast_alarm:
        assert "interval" in out["banner"].lower()
    if direction_flag:
        assert "accuracy" in out["banner"].lower()


def _ar_series(n=60, seed=0, phi=0.3):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal(0, 0.2)
    return pd.Series(2.5 + x, index=_calendar(n))


def test_the_forecast_result_has_scores_a_decision_and_a_next_month_with_intervals():
    series = _ar_series()
    out = compute_forecast_from_series(series, PRECISIONS["fast"])
    _safe(out)
    assert {"last_month", "same_month_last_year", "ets_damped", "ets_seasonal", "ridge"} <= set(
        out["scores"]
    )
    assert out["decision"]["serving"] in {"last_month", "same_month_last_year", "ets_damped",
                                          "ets_seasonal", "ridge"}
    nxt = out["next_month"]
    assert nxt["month_id"] == next_month_id(int(series.index[-1]))
    assert nxt["lo90"] < nxt["lo80"] < nxt["point"] < nxt["hi80"] < nxt["hi90"]
    if out["decision"]["serving"] == "last_month":
        assert nxt["point"] == pytest.approx(series.iloc[-1])
    assert 0 < len(out["history"]) <= 48
    assert {"month_id", "actual", "point", "lo90", "hi90"} <= set(out["history"][0])
    assert set(out["fallbacks"]) >= {"ets_damped", "ets_seasonal"}


def _forecast_frame(outside):
    """A served-forecast frame whose last months are inside or outside their 90% interval."""
    n = len(outside)
    actual = np.where(outside, 5.0, 2.0)
    return pd.DataFrame(
        {"month_id": _calendar(n), "actual": actual, "point": 2.0, "lo80": 1.8, "hi80": 2.2,
         "lo90": 1.7, "hi90": 2.3, "scale": 0.1, "fallback": False}
    )


def _direction_bt(hits):
    """35-month-style backtest: the served model is right where `hits` is 1."""
    n = len(hits)
    y_true = np.array(["Flat"] * n, dtype=object)
    served = np.where(np.asarray(hits) == 1, "Flat", "Up").astype(object)
    return pd.DataFrame({"y_true": y_true, "seasonal": served, "persistence": y_true},
                        index=pd.Index(_calendar(n), name="month_id"))


def test_the_forecast_alarm_needs_three_misses_among_the_last_six_months():
    outside = [False] * 12 + [True, False, True, False, True, False]
    bt = _direction_bt([1] * len(outside))
    out = compute_monitoring_from(bt, "seasonal", _forecast_frame(outside), PRECISIONS["fast"])
    _safe(out)
    assert out["forecast"]["misses_in_window"] == 3 and out["forecast"]["alarm"] is True
    assert out["status"] == "review" and out["banner"]
    calm = [False] * 17 + [True]
    out2 = compute_monitoring_from(_direction_bt([1] * 18), "seasonal", _forecast_frame(calm),
                                   PRECISIONS["fast"])
    assert out2["forecast"]["misses_in_window"] == 1 and out2["forecast"]["alarm"] is False
    assert out2["status"] == "ok" and out2["banner"] is None


def test_a_perfect_record_is_never_flagged_even_though_its_threshold_sits_at_the_ceiling():
    out = compute_monitoring_from(_direction_bt([1] * 30), "seasonal",
                                  _forecast_frame([False] * 24), PRECISIONS["fast"])
    assert out["direction"]["threshold"] == 1.0 and out["direction"]["flag"] is False


def test_the_direction_flag_is_raised_when_the_latest_rolling_accuracy_is_at_the_threshold():
    rng = np.random.default_rng(0)
    hits = (rng.random(35) < 0.7).astype(int)
    hits[-6:] = [0, 0, 0, 0, 1, 1]  # the latest six months: 2 of 6 right
    out = compute_monitoring_from(_direction_bt(hits), "seasonal", _forecast_frame([False] * 24),
                                  PRECISIONS["fast"])
    d = out["direction"]
    assert d["latest_rolling"] == pytest.approx(2 / 6)
    assert d["flag"] == (d["latest_rolling"] <= d["threshold"]
                         and d["latest_rolling"] < d["match_rate"])
    assert d["match_rate"] == pytest.approx(hits.mean())
    assert len(d["rolling"]) == 35 - 6 + 1
    assert {"month_id", "accuracy"} <= set(d["rolling"][0])


def _adoption(months=48, seed=0):
    rng = np.random.default_rng(seed)
    effects = {"S1": 0.6, "S2": 0.2, "S3": 0.0, "S4": -0.4, "S5": -0.8}
    rows = []
    for m, month_id in enumerate(_calendar(months)):
        for specialty, effect in effects.items():
            for age in ("40 TO 59", "65 TO 74", "75 TO 84"):
                for gender in ("FEMALE", "MALE"):
                    t = int(rng.integers(300, 700))
                    p = 1 / (1 + np.exp(-(-3.4 + effect - 0.012 * m)))
                    rows.append((month_id, specialty, age, gender, int(rng.binomial(t, p)), t))
    return pd.DataFrame(
        rows, columns=["month_id", "specialty", "age_band", "gender", "zilretta", "category"]
    )


def test_the_findings_result_holds_the_trend_decomposition_adoption_and_verdicts():
    out = compute_findings_from_data(_adoption(), PRECISIONS["fast"])
    _safe(out)
    assert len(out["months"]) == len(out["series_pct"]) == 48
    assert len(out["trend"]["fitted"]) == 48
    assert out["trend"]["n_breaks"] == len(out["trend"]["break_months"])
    assert out["decomposition"]["chain"][0]["comparison"] == "1 to 2"
    for row in out["decomposition"]["chain"]:
        assert row["mix"] + row["rate"] == pytest.approx(row["total"], abs=1e-9)
    table = out["adoption"]["table"]
    assert {row["position"] for row in table} <= {"clearly above", "clearly below",
                                                  "not clearly different"}
    top = max(table, key=lambda r: r["adjusted_share"])
    assert top["specialty"] == "S1"  # the specialty simulated with the highest rate
    assert {"T1", "D1", "A1", "A2", "A3"} <= set(out["robustness"]["verdicts"])
    assert out["robustness"]["verdicts"]["A2"]["label"] in {"robust", "fragile"}


def _segment_gold(months=32, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for specialty in ("S1", "S2", "S3", "S4"):
        for age in ("40 TO 59", "65 TO 74", "75 TO 84"):
            for gender in ("FEMALE", "MALE"):
                rate = rng.uniform(0.01, 0.09)
                for month in _calendar(months):
                    t = int(rng.integers(80, 400))
                    rows.append((month, specialty, age, gender, int(rng.binomial(t, rate)), t))
    frame = pd.DataFrame(
        rows, columns=["month_id", "specialty_name", "age_band", "gender",
                       "branded_injectable_visits", "total_category_visits"]
    )
    frame["segment_visit_share"] = (
        frame["branded_injectable_visits"] / frame["total_category_visits"]
    )
    return frame


def test_the_segment_model_result_holds_scores_the_decision_calibration_and_size_table():
    out = compute_segment_model_from_data(
        _segment_gold(), PRECISIONS["fast"], trained=("logistic",), min_train=24
    )
    _safe(out)
    assert {"market", "last_month", "segment_history", "specialty_history", "logistic"} <= set(
        out["scores"]
    )
    assert out["decision"]["serving"] in {"logistic", out["best_baseline"]}
    assert len(out["calibration"]["table"]) == 10
    assert out["calibration"]["slope"] is not None
    assert [row["bucket"] for row in out["by_size"]] == ["<50", "50-100", "100-300", ">300"]
    assert {"balanced_accuracy", "accuracy"} <= set(next(iter(out["a2"]["scores"].values())))
    assert out["wins"]["logistic"]["log_loss"][1] == out["n_test_months"]


@pytest.mark.skipif(not REAL_DB.exists(), reason="the committed warehouse is not present")
@pytest.mark.skipif(
    os.environ.get("RUN_REAL_RESULTS") != "1",
    reason="takes about 5 minutes; set RUN_REAL_RESULTS=1 to run it",
)
def test_every_result_is_computed_at_reduced_precision_from_the_committed_warehouse():
    engine = create_engine(f"sqlite:///{REAL_DB.as_posix()}")
    results = compute_all(engine, "fast")
    assert set(results) == {"findings", "segment_model", "forecast", "direction", "monitoring"}
    for key, payload in results.items():
        _safe(payload)
        assert payload["precision"] == "fast", key
    findings = results["findings"]
    assert findings["trend"]["break_months"] == [202203]
    assert len(findings["months"]) == 72
    assert findings["robustness"]["verdicts"]["T1"]["label"] in {"robust", "fragile"}
    assert results["direction"]["decision"]["serving"] == "seasonal"
    assert results["forecast"]["decision"]["serving"] == "last_month"
    assert results["forecast"]["next_month"]["month_id"] == 202508
    assert results["segment_model"]["decision"]["serving"] in {
        "logistic", "gbm", "rf", results["segment_model"]["best_baseline"]
    }
    assert results["monitoring"]["status"] in {"ok", "review"}
    assert len(results["direction"]["power"]["table"]) > 0
    assert len(results["monitoring"]["direction"]["rolling"]) == 30
