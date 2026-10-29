"""Tests for the on-request charts (src/.../serving/charts.py): each chart has the data it should,
a tooltip on its marks, and fades small groups; `build` refuses a name it does not know."""

import altair as alt
import pytest

from oa_market_intelligence.serving import charts, data, qa_tools

pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")


@pytest.fixture(scope="module")
def snap():
    engine = data.open_engine()
    try:
        return qa_tools.load_snapshot(engine)
    finally:
        engine.dispose()


def _spec(chart):
    return chart.to_dict()


def _rows(chart, name=None):
    datasets = _spec(chart)["datasets"]
    if name is not None:
        return datasets[name]
    return next(iter(datasets.values()))


def _tooltip_titles(chart):
    found = []

    def walk(node):
        if isinstance(node, dict):
            for tip in node.get("encoding", {}).get("tooltip", []) or []:
                found.append(tip.get("title"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(_spec(chart))
    return found


def test_the_trend_has_a_month_for_every_row_and_can_be_cut_to_a_range(snap):
    full = charts.trend_chart(snap.monthly)
    assert len(_rows(full)) == 72
    cut = charts.trend_chart(snap.monthly, 202101, 202112)
    rows = _rows(cut)
    assert len(rows) == 12 and rows[0]["label"] == "Jan 2021" and rows[-1]["label"] == "Dec 2021"
    assert "Share (%)" in _tooltip_titles(full) and "Month" in _tooltip_titles(full)


def test_the_segments_chart_fades_small_groups_and_keeps_the_biggest_share_first(snap):
    chart = charts.segments_chart(snap.by_specialty, "specialty", 8)
    rows = _rows(chart)
    assert len(rows) == 8
    shares = [r["share_pct"] for r in rows]
    assert shares == sorted(shares, reverse=True)
    assert any(r["size"] == "Small group (noisy)" for r in rows)
    spec = _spec(chart)
    assert spec["encoding"]["opacity"]["scale"]["range"] == [1.0, 0.4]
    assert {"Specialty", "Share (%)", "Zilretta visits"} <= set(_tooltip_titles(chart))
    gender = charts.segments_chart(snap.by_gender, "gender", 5)
    assert {r["group"] for r in _rows(gender)} >= {"FEMALE", "MALE"}
    assert "Gender" in _tooltip_titles(gender)


def test_the_forecast_chart_joins_history_to_three_forecasts_with_their_range(snap):
    chart = charts.forecast_chart(snap.monthly, snap.pred)
    sets = _spec(chart)["datasets"].values()
    sizes = sorted(len(rows) for rows in sets)
    assert sizes == [3, 36]  # three forecast months and three years of history
    forecast = next(rows for rows in _spec(chart)["datasets"].values() if len(rows) == 3)
    assert [r["months_ahead"] for r in forecast] == [1, 3, 6]
    assert all(r["low_90_pct"] < r["forecast_pct"] < r["high_90_pct"] for r in forecast)
    assert {"Forecast (%)", "Likely low (%)", "Likely high (%)"} <= set(_tooltip_titles(chart))


def test_the_specialty_mix_is_the_graphs_weights(snap):
    chart = charts.specialty_mix_chart(snap.graph, 5)
    rows = _rows(chart)
    assert len(rows) == 5 and rows[0]["specialty"] == "ORTHOPEDIC SURGERY"
    assert rows[0]["share_pct"] == pytest.approx(37.6, abs=0.1)
    assert sum(r["share_pct"] for r in _rows(charts.specialty_mix_chart(snap.graph, 50))) == (
        pytest.approx(100.0, abs=0.1)
    )


def test_build_draws_each_named_chart_and_refuses_others(snap):
    for name, params in [
        ("trend", {}),
        ("trend", {"start_month_id": 202201}),
        ("segments", {"by": "age_band", "top": 5}),
        ("forecast", {}),
        ("specialty_mix", {"top": 6}),
    ]:
        built = charts.build(name, params, snap)
        assert isinstance(built, (alt.Chart, alt.LayerChart, alt.TopLevelMixin))
        built.to_dict()  # a valid Vega-Lite spec
    assert set(qa_tools.CHARTS) == {"trend", "segments", "forecast", "specialty_mix"}
    with pytest.raises(ValueError, match="unknown chart"):
        charts.build("pie", {}, snap)
