"""Tests for the tools Claude may call (src/.../serving/qa_tools.py) on the committed warehouse:
every answer is JSON-safe, aggregate-only and equal to what the site's tabs show, and bad input
comes back as an error for Claude to read, never as an exception or a query."""

import json

import pytest

from oa_market_intelligence.serving import data, qa_tools

pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")

FORBIDDEN = ("patient", "npi", "provider_id", "person_id", "claim")


@pytest.fixture(scope="module")
def snap():
    engine = data.open_engine()
    try:
        return qa_tools.load_snapshot(engine)
    finally:
        engine.dispose()


def _run(snap, name, **arguments):
    return qa_tools.run_tool(name, arguments, snapshot=snap)


def _keys(value):
    """Every dict key anywhere in an answer (the prose may say 'no patient-level rows')."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


CALLS = [
    ("get_data_status", {}),
    ("get_market_trend", {}),
    ("get_market_trend", {"by_year": True}),
    ("get_segments", {"by": "specialty"}),
    ("get_segments", {"by": "age_band"}),
    ("get_segments", {"by": "gender"}),
    ("get_forecasts", {"view": "national"}),
    ("get_forecasts", {"view": "specialty"}),
    ("get_forecasts", {"view": "alerts"}),
    ("get_model_results", {}),
    ("get_findings", {}),
]


@pytest.mark.parametrize(("name", "arguments"), CALLS)
def test_every_tool_answer_is_json_safe_small_and_aggregate_only(snap, name, arguments):
    out = qa_tools.run_tool(name, arguments, snapshot=snap)
    assert "error" not in out
    text = json.dumps(out, allow_nan=False)  # raises on NaN or inf
    assert len(text) < 12_000
    assert not any(word in key.lower() for key in _keys(out) for word in FORBIDDEN)


def test_the_definitions_match_the_tools_and_none_takes_sql(snap):
    names = {d["name"] for d in qa_tools.TOOL_DEFINITIONS}
    assert names == set(qa_tools.TOOLS)
    for definition in qa_tools.TOOL_DEFINITIONS:
        properties = definition["input_schema"]["properties"]
        assert not {"sql", "query", "table", "column"} & set(properties)


def test_the_headline_numbers_are_the_sites(snap):
    out = _run(snap, "get_data_status")
    assert out["latest_month"] == "Jul 2025" and out["latest_share_pct"] == 1.88
    assert out["peak_month"] == "Dec 2021" and out["peak_share_pct"] == 3.38
    assert out["change_since_peak_pct"] == pytest.approx(-44.2, abs=0.1)
    assert out["months_of_data"] == 72 and out["freshness"] in ("stale", "fresh")
    assert any("prescription-volume" in item for item in out["limits"])
    assert any("sales rose" in item for item in out["limits"])


def test_the_trend_can_be_cut_to_a_range_and_summed_by_year(snap):
    out = _run(snap, "get_market_trend", start_month_id=202110, end_month_id=202203)
    assert [r["month"] for r in out["rows"]][:2] == ["Oct 2021", "Nov 2021"]
    assert len(out["rows"]) == 6
    assert out["rows"][2]["share_pct"] == pytest.approx(3.38, abs=0.01)  # Dec 2021 is the peak
    years = _run(snap, "get_market_trend", by_year=True)["rows"]
    assert [r["year"] for r in years][:2] == [2019, 2020] and years[0]["partial_year"] is True
    assert "error" in _run(snap, "get_market_trend", start_month_id=210001)


def test_segments_are_the_sites_groups_with_a_cap_and_a_small_flag(snap):
    out = _run(snap, "get_segments", by="specialty", top=3)
    assert len(out["rows"]) == 3
    shares = [r["share_pct"] for r in out["rows"]]
    assert shares == sorted(shares, reverse=True)
    assert len(_run(snap, "get_segments", by="specialty", top=1000)["rows"]) <= qa_tools.MAX_TOP
    assert {r["group"] for r in _run(snap, "get_segments", by="gender")["rows"]} >= {
        "FEMALE",
        "MALE",
    }
    top25 = _run(snap, "get_segments", by="specialty", top=25)["rows"]
    assert any(r["small_segment"] for r in top25)


def test_the_forecasts_carry_their_range_their_bias_and_the_alert_precision(snap):
    national = _run(snap, "get_forecasts", view="national")
    assert [r["months_ahead"] for r in national["rows"]] == [1, 3, 6]
    assert {r["model"] for r in national["rows"]} == {"same as last month"}
    assert all(r["low_90_pct"] < r["forecast_pct"] < r["high_90_pct"] for r in national["rows"])
    assert "90% range" in national["coverage_in_testing"]
    specialty = _run(snap, "get_forecasts", view="specialty", top=5)
    assert len(specialty["rows"]) == 5 and "run high" in specialty["bias"]
    alerts = _run(snap, "get_forecasts", view="alerts", top=5)
    assert alerts["summary"]["flagged"] >= len(alerts["rows"])
    assert all(r["alert_flag"] == 1 for r in alerts["rows"])
    assert 0 < alerts["summary"]["precision"] < 1


def test_the_model_results_say_what_to_use_and_what_not_to(snap):
    out = _run(snap, "get_model_results")
    board = {(r["task"], r["horizon"]): r for r in out["scoreboard"]}
    assert len(board) == 9
    assert board[("National share", 1)]["verdict"] == "No model beat the simple rule"
    assert board[("Decline alert", 3)]["adopted"] is False
    assert [r["what"] for r in out["use_and_dont"]][0] == "National share forecast"
    assert out["decision"] == "DL-79"
    assert [r["horizon"] for r in out["segment_bias"]] == [1, 3, 6]


def test_the_findings_can_be_filtered_by_a_word(snap):
    everything = _run(snap, "get_findings")
    assert everything["count"] == 17
    sales = _run(snap, "get_findings", keyword="SALES")
    assert 1 <= sales["count"] < 17
    assert all(
        "sales" in " ".join([f["finding"], f["evidence"], f["impact"]]).lower()
        for f in sales["findings"]
    )
    assert _run(snap, "get_findings", keyword="zzzz")["count"] == 0


def test_bad_input_returns_an_error_for_claude_to_read(snap):
    for name, arguments in [
        ("run_sql", {"sql": "SELECT 1"}),
        ("get_segments", {"by": "patient_id"}),
        ("get_segments", {}),
        ("get_forecasts", {"view": "everything"}),
        ("get_market_trend", {"start_month_id": "2021"}),
        ("get_market_trend", {"start_month_id": True}),
    ]:
        out = qa_tools.run_tool(name, arguments, snapshot=snap)
        assert "error" in out, (name, arguments)
    assert "error" in qa_tools.run_tool("get_segments", None, snapshot=snap)


def test_a_tool_that_breaks_is_reported_without_a_stack_trace(snap, monkeypatch):
    def boom(snapshot, arguments):
        raise RuntimeError("secret detail /path/to/file")

    monkeypatch.setitem(qa_tools.TOOLS, "get_findings", boom)
    out = _run(snap, "get_findings")
    assert "secret detail" not in json.dumps(out) and "error" in out
