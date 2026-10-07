"""The site's sentences and frames, and a smoke test that the whole page renders."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from oa_market_intelligence.serving.results import store_result
from oa_market_intelligence.serving.site_views import (
    adoption_frame,
    decomposition_frame,
    decomposition_headline,
    forecast_caveat,
    forecast_frame,
    forecast_headline,
    model_serving_lines,
    month_label,
    robustness_headline,
    rolling_frame,
    specialty_headline,
    trend_frame,
    trend_headline,
)
from stored_results import stored_results
from tiny_gold import build_tiny_gold

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


def test_the_trend_headline_says_rose_then_fell_without_giving_a_cause():
    text = trend_headline(stored_results()["findings"])
    assert "rose until about Jun 2020 and has fallen since" in text
    assert "because" not in text and "caused" not in text


def test_the_trend_headline_handles_no_break_and_a_reversed_pattern():
    findings = stored_results()["findings"]
    flat = {**findings, "trend": {**findings["trend"], "break_months": [], "segments": []}}
    assert "no clear change" in trend_headline(flat)
    reversed_ = {**findings, "trend": {**findings["trend"], "segments": [
        {"slope_pp_per_year": -1.0}, {"slope_pp_per_year": 1.0}]}}
    assert "fell until about Jun 2020 and has risen since" in trend_headline(reversed_)


def test_the_decomposition_headline_names_the_larger_part():
    text = decomposition_headline(stored_results()["findings"])
    assert "12 months to Dec 2020 and the 12 months to Dec 2021" in text
    assert "-1.00 percentage points" in text and "share within specialties" in text
    assert "-1.10 points" in text and "+0.10 points" in text


def test_the_specialty_headline_counts_clear_differences_and_reports_stability():
    text = specialty_headline(stored_results()["findings"])
    assert "1 specialties use Zilretta clearly more" in text and "1 clearly less" in text
    assert "stable" in text and "0.80" in text


def test_the_robustness_headline_names_what_did_not_hold_up():
    text = robustness_headline(stored_results()["findings"])
    assert "3 of 4" in text and "A3" in text


def test_the_forecast_text_gives_the_ranges_and_is_honest_about_the_baseline():
    forecast = stored_results()["forecast"]
    headline = forecast_headline(forecast)
    assert "Jan 2021" in headline and "1.40% to 2.20%" in headline
    caveat = forecast_caveat(forecast)
    assert "last month baseline" in caveat and "not a prediction" in caveat
    promoted = {**forecast, "decision": {"promoted": ["ridge"], "serving": "ridge"}}
    assert "beat the baselines" in forecast_caveat(promoted)


def test_the_frames_convert_to_percent_and_sort():
    findings = stored_results()["findings"]
    assert list(adoption_frame(findings)["specialty"])[0] == "Pain Medicine"
    assert adoption_frame(findings)["adjusted_share"].iloc[0] == pytest.approx(6.0)
    assert decomposition_frame(findings)["rate"].iloc[0] == pytest.approx(-1.1)
    assert len(trend_frame(findings)) == 12
    assert len(forecast_frame(stored_results()["forecast"])) == 12
    assert len(rolling_frame(stored_results()["monitoring"])) == 2
    assert month_label(202506) == "Jun 2025"


def test_the_serving_table_lists_every_task_and_never_claims_a_promotion_that_did_not_happen():
    rows = {row["task"]: row for row in model_serving_lines(stored_results())}
    assert rows["Segment share, next month"]["trained model promoted"] == "yes"
    assert rows["Overall share forecast"]["trained model promoted"] == "no"
    assert rows["Direction (Up/Flat/Down)"]["serving"] == "seasonal"
    assert model_serving_lines({}) == []


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """A file database (tiny tables plus stored results) for the page to open."""
    from sqlalchemy import text

    def make(review=True, with_results=True):
        path = tmp_path / "warehouse.db"
        path.unlink(missing_ok=True)
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        build_tiny_gold(engine)
        if with_results:
            for key, payload in stored_results(review=review).items():
                store_result(engine, key, payload, precision="fast")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        monkeypatch.setenv("OA_DB_PATH", str(path))
        return path

    return make


def _run_app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    return at


def _texts(at):
    return " ".join(
        [m.value for m in at.markdown] + [w.value for w in at.warning]
        + [i.value for i in at.info] + [s.value for s in at.success]
        + [c.value for c in at.caption]
    )


def test_the_page_renders_every_tab_with_the_stored_results_and_a_review_banner(app_db):
    app_db(review=True)
    at = _run_app()
    assert not at.exception
    assert [t.label for t in at.tabs] == [
        "Overview", "Market trend", "Segments", "Forecast & monitoring", "Model results",
        "Ask Claude", "About & limits",
    ]
    text = _texts(at)
    assert "Review suggested." in text
    assert "rose until about Jun 2020 and has fallen since" in text
    assert "Jan 2021" in text and "not a prediction" in text


def test_the_page_shows_no_banner_when_the_monitors_are_quiet(app_db):
    app_db(review=False)
    at = _run_app()
    assert not at.exception
    assert "Review suggested." not in _texts(at)
    assert "No monitor is signalling a problem." in _texts(at)


def test_a_database_published_without_results_still_renders_with_plain_messages(app_db):
    app_db(with_results=False)
    at = _run_app()
    assert not at.exception
    text = _texts(at)
    assert "published without" in text
    assert "Review suggested." not in text
