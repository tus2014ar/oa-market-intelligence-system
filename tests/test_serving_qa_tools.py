"""Tests for the tools Claude may call (src/.../serving/qa_tools.py).

The safety property: the model can only reach pre-written aggregate summaries. No tool takes
SQL, no tool returns patient-visit-level rows, and every result is plain JSON with a row cap.
"""

import json

import pandas as pd
import pytest

from oa_market_intelligence.serving.qa_tools import (
    MAX_RESULT_ROWS,
    MAX_SEGMENT_ROWS,
    TOOL_DEFINITIONS,
    run_tool,
)


def _panel():
    return {
        "scores": pd.DataFrame(
            {"balanced_accuracy": [0.333, 0.419, 0.369], "accuracy": [0.714, 0.657, 0.4]},
            index=["always-majority", "seasonal", "logistic"],
        ),
        "chance": pd.DataFrame(
            {"mean": [0.327], "p05": [0.213], "p95": [0.462]}, index=["balanced_accuracy"]
        ),
        "decision": {"promoted": False, "serving": "seasonal", "reason": "not above chance"},
        "n_test": 35,
        "label_counts": {"Flat": 25, "Down": 6, "Up": 4},
    }


def _results():
    """Stand-ins shaped like the stored Phase 4 results."""
    specialties = [
        {"specialty": f"S{i}", "adjusted_share": 0.01 * (i + 1), "low": 0.005 * (i + 1),
         "high": 0.015 * (i + 1), "visit_share_pct": 2.0, "position": "clearly above"}
        for i in range(40)
    ]
    return {
        "findings": {
            "months": [201908, 202507],
            "trend": {"break_months": [202203], "intervals": [[202105, 202207]], "n_breaks": 1,
                      "p_values": [0.001, 0.28], "events_overlap": [[]],
                      "segments": [{"from": 201908, "to": 202202, "mean_pct": 2.62,
                                    "slope_pp_per_year": 0.48}],
                      "other_categories": {"nsaid_otc": {"n_breaks": 0, "p_values": [0.75]}},
                      "fitted": [2.0] * 72},
            "series_pct": [2.0] * 72,
            "decomposition": {
                "windows": [[201908, 202007], [202008, 202107]],
                "chain": [{"comparison": "1 to 2", "share_a": 0.02, "share_b": 0.03,
                           "total": 0.01, "mix": 0.0, "rate": 0.01}],
                "bootstrap": {"first_to_last": {"total": {"estimate": -0.003, "low": -0.005,
                                                         "high": -0.001}}},
                "top_specialties": [{"group": f"G{i}", "mix": 0.0, "rate": 0.0, "total": 0.0}
                                    for i in range(30)],
                "granularity": {"specialty": {"mix": 0.0008, "rate": -0.004, "total": -0.003}},
            },
            "adoption": {"overall_share": 0.0249, "table": specialties,
                         "deviance": {"specialty": 0.51, "age_band": 0.5, "gender": 0.003,
                                      "dispersion": 1.7},
                         "stability": {"spearman": 0.78, "low": 0.75, "high": 0.93,
                                       "same_side_fraction": 0.82,
                                       "table": [{"specialty": "S1", "first_half": 0.04,
                                                  "second_half": 0.02, "flipped": True}]}},
            "robustness": {"verdicts": {"T1": {"label": "robust", "failed": []},
                                        "A2": {"label": "robust", "failed": [],
                                               "by_specialty": {"S1": {"label": "robust"}}}}},
            "precision": "full",
        },
        "segment_model": {
            "n_test_months": 46, "best_baseline": "last_month",
            "scores": {"last_month": {"log_loss": 0.11317, "mae": 0.015},
                       "logistic": {"log_loss": 0.11274, "mae": 0.0112}},
            "summaries": {"logistic": {"estimate": 0.00044, "low": 0.00037}},
            "decision": {"promoted": ["logistic"], "serving": "logistic", "tie_break": "t"},
            "calibration": {"slope": 1.1, "mean_pred": 0.0265, "mean_obs": 0.0248,
                            "table": [{"pred": 0.01, "obs": 0.009}] * 10},
            "by_size": [{"bucket": "<50", "improvement": 0.0077}],
            "wins": {"logistic": {"log_loss": [44, 46]}}, "precision": "full",
        },
        "forecast": {
            "n_test_months": 48, "best_baseline": "last_month",
            "scores": {"last_month": {"mae": 0.125, "cover90": 0.958},
                       "ridge": {"mae": 0.147, "cover90": 0.896}},
            "decision": {"promoted": [], "serving": "last_month"},
            "next_month": {"month_id": 202508, "model": "last_month", "point": 1.88, "lo80": 1.65,
                           "hi80": 2.1, "lo90": 1.56, "hi90": 2.2, "fallback": False},
            "history": [{"month_id": 202507, "actual": 1.88}] * 48, "precision": "full",
        },
        "direction": {
            "n_test": 35, "scores": {"rf": {"balanced_accuracy": 0.326},
                                     "gbm": {"balanced_accuracy": 0.406}},
            "power": {"smallest_detectable_gain": 0.3, "persistence_accuracy": 0.629,
                      "table": [{"delta": 0.1, "power": 0.1}] * 15},
            "predictions": [{"month_id": 202507, "y_true": "Flat"}] * 35, "precision": "full",
        },
        "monitoring": {
            "status": "review", "banner": "At least 3 of the last 6 months fell outside.",
            "direction": {"match_rate": 0.657, "threshold": 0.333, "latest_rolling": 0.5,
                          "flag": False, "random_line": 0.167,
                          "rolling": [{"month_id": 202507, "accuracy": 0.5}] * 30},
            "forecast": {"rule": "R90", "alarm": True, "misses_in_window": 3,
                         "false_alarm_rate": 0.004,
                         "recent": [{"month_id": 202507, "outside": True}] * 6},
            "detection": [{"scenario": "volatility 0.30", "rule": "R90", "detection_rate": 0.54}],
        },
    }


def _loader(results=None):
    store = results if results is not None else _results()
    return lambda key: store.get(key)


def _run(engine, name, arguments=None, results=None):
    return run_tool(name, arguments or {}, engine=engine, panel_loader=_panel,
                    results_loader=_loader(results))


def test_the_tool_list_is_small_named_and_takes_no_sql():
    names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert names == {
        "get_data_status", "get_market_trend", "get_segment_table", "get_model_results",
        "get_findings", "get_forecast_and_monitoring",
    }
    for tool in TOOL_DEFINITIONS:
        assert tool["input_schema"]["type"] == "object"
        assert tool["description"]
        properties = set(tool["input_schema"].get("properties", {}))
        assert not properties & {"sql", "query", "statement", "table"}


def test_data_status_matches_the_warehouse(tiny_gold_engine):
    result = _run(tiny_gold_engine, "get_data_status")
    assert result["n_months"] == 2
    assert result["zilretta_visits"] == 30


def test_market_trend_can_be_limited_to_a_month_range(tiny_gold_engine):
    result = _run(tiny_gold_engine, "get_market_trend", {"start_month": 201909})
    assert [row["month_id"] for row in result["months"]] == [201909]
    assert result["months"][0]["direction_label"] == "Flat"


def test_segment_table_returns_the_observed_vs_expected_columns(tiny_gold_engine):
    result = _run(tiny_gold_engine, "get_segment_table", {"by": "specialty"})
    rows = {row["group"]: row for row in result["rows"]}
    assert rows["A"]["ratio"] == pytest.approx(2.0, abs=0.01)
    assert set(rows["A"]) == {
        "group", "zilretta_visits", "category_visits", "observed_share",
        "expected_share", "ratio", "ci_low", "ci_high",
    }
    assert "caveat" in result  # the "a lead, not proof" reminder travels with the data


def test_segment_table_rows_are_capped(tiny_gold_engine):
    result = _run(tiny_gold_engine, "get_segment_table", {"by": "specialty", "top_n": 10_000})
    assert len(result["rows"]) <= MAX_SEGMENT_ROWS


def test_a_bad_grouping_is_an_error_result_not_an_exception(tiny_gold_engine):
    result = _run(tiny_gold_engine, "get_segment_table", {"by": "patient_id"})
    assert "error" in result


def test_model_results_come_from_the_panel_and_are_json_safe(tiny_gold_engine):
    result = _run(tiny_gold_engine, "get_model_results")
    assert result["decision"]["serving"] == "seasonal"
    assert result["n_test_months"] == 35
    json.dumps(result, allow_nan=False)


def test_an_unknown_tool_is_an_error_result(tiny_gold_engine):
    assert "error" in _run(tiny_gold_engine, "run_sql", {"sql": "SELECT * FROM fact_visits"})


@pytest.mark.parametrize("arguments", [None, "x", [], {"start_month": "soon"}, {"top_n": "many"}])
def test_malformed_arguments_never_raise(tiny_gold_engine, arguments):
    for name in ("get_market_trend", "get_segment_table"):
        result = run_tool(name, arguments, engine=tiny_gold_engine, panel_loader=_panel)
        json.dumps(result, allow_nan=False)


def test_a_failing_query_becomes_an_error_result(tiny_gold_engine):
    def broken_panel():
        raise RuntimeError("database is locked")

    result = run_tool("get_model_results", {}, engine=tiny_gold_engine, panel_loader=broken_panel)
    assert "error" in result
    assert "locked" not in json.dumps(result)  # internal detail is not passed to the model


def test_findings_summary_gives_the_headlines_in_percent_with_the_caveats(tiny_gold_engine):
    out = _run(tiny_gold_engine, "get_findings")
    json.dumps(out, allow_nan=False)
    assert out["trend"]["break_months"] == [202203]
    assert out["robustness"]["T1"] == "robust"
    assert out["specialties"][0]["adjusted_share_pct"] == pytest.approx(40.0)  # largest first
    assert out["caveats"] and any("cause" in c.lower() for c in out["caveats"])
    assert len(out["specialties"]) <= MAX_RESULT_ROWS


@pytest.mark.parametrize("section", ["trend", "decomposition", "specialties", "robustness"])
def test_each_findings_section_is_json_safe_and_capped(tiny_gold_engine, section):
    out = _run(tiny_gold_engine, "get_findings", {"section": section})
    json.dumps(out, allow_nan=False)
    assert "error" not in out
    for value in out.values():
        if isinstance(value, list):
            assert len(value) <= MAX_RESULT_ROWS


def test_the_specialty_section_shows_adjusted_shares_positions_and_stability(tiny_gold_engine):
    out = _run(tiny_gold_engine, "get_findings", {"section": "specialties"})
    assert out["rows"][0]["position"] == "clearly above"
    assert out["rows_shown"] == MAX_RESULT_ROWS and out["rows_total"] == 40
    assert out["stability"]["spearman"] == pytest.approx(0.78)


def test_an_unknown_findings_section_or_missing_results_is_an_error_result(tiny_gold_engine):
    assert "error" in _run(tiny_gold_engine, "get_findings", {"section": "payers"})
    assert "error" in _run(tiny_gold_engine, "get_findings", results={})  # nothing published


def test_the_forecast_tool_gives_next_month_with_ranges_and_the_monitoring_status(
    tiny_gold_engine,
):
    out = _run(tiny_gold_engine, "get_forecast_and_monitoring")
    json.dumps(out, allow_nan=False)
    assert out["next_month"]["month_id"] == 202508
    assert out["next_month"]["range90_pp"] == [1.56, 2.2]
    assert out["serving"] == "last_month" and out["typical_error_pp"] == pytest.approx(0.125)
    assert out["monitoring"]["status"] == "review"
    assert out["monitoring"]["forecast_alarm"]["alarm"] is True
    assert out["limits"] and len(out["monitoring"]["detection"]) <= MAX_RESULT_ROWS


def test_model_results_by_task_cover_segment_forecast_and_direction(tiny_gold_engine):
    segment = _run(tiny_gold_engine, "get_model_results", {"task": "segment"})
    assert segment["serving"] == "logistic" and segment["calibration_slope"] == 1.1
    assert segment["by_size"][0]["bucket"] == "<50"
    forecast = _run(tiny_gold_engine, "get_model_results", {"task": "forecast"})
    assert forecast["serving"] == "last_month" and forecast["promoted"] == []
    direction = _run(tiny_gold_engine, "get_model_results", {"task": "direction"})
    assert direction["decision"]["serving"] == "seasonal"  # the panel, as before
    assert direction["power"]["smallest_detectable_gain"] == 0.3
    assert direction["tree_models"]["rf"]["balanced_accuracy"] == pytest.approx(0.326)
    everything = _run(tiny_gold_engine, "get_model_results", {"task": "all"})
    assert set(everything) >= {"direction", "segment", "forecast"}
    json.dumps(everything, allow_nan=False)


def test_model_results_without_published_results_still_answers_for_direction_only(
    tiny_gold_engine,
):
    plain = run_tool("get_model_results", {}, engine=tiny_gold_engine, panel_loader=_panel)
    assert plain["decision"]["serving"] == "seasonal"  # the earlier behaviour, unchanged
    missing = _run(tiny_gold_engine, "get_model_results", {"task": "segment"}, results={})
    assert "error" in missing


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key).lower()
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_no_tool_result_contains_patient_level_fields(tiny_gold_engine):
    """Every tool returns aggregates only: no field keyed like a visit or patient record."""
    forbidden = {"patient_id", "visit_id", "npi", "sql", "predictions"}
    for name, arguments in [("get_findings", {}), ("get_forecast_and_monitoring", {}),
                            ("get_model_results", {"task": "all"})]:
        out = _run(tiny_gold_engine, name, arguments)
        assert not forbidden & set(_keys(out)), name
