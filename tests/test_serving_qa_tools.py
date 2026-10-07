"""Tests for the tools Claude may call (src/.../serving/qa_tools.py).

The safety property: the model can only reach pre-written aggregate summaries. No tool takes
SQL, no tool returns patient-visit-level rows, and every result is plain JSON with a row cap.
"""

import json

import pandas as pd
import pytest

from oa_market_intelligence.serving.qa_tools import (
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


def _run(engine, name, arguments=None):
    return run_tool(name, arguments or {}, engine=engine, panel_loader=_panel)


def test_the_tool_list_is_small_named_and_takes_no_sql():
    names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert names == {
        "get_data_status", "get_market_trend", "get_segment_table", "get_model_results",
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
