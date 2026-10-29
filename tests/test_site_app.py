"""The site itself (app/streamlit_app.py), run headlessly with Streamlit's test runner on the
committed warehouse: no exception, every tab present, and the numbers and caveats it must show.

Needs the site's packages (app/requirements.txt); the CI job `site` installs them and these tests
are skipped where Streamlit is not installed.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit")
import streamlit as st  # noqa: E402
from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from oa_market_intelligence.serving import data  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")
pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")


@pytest.fixture(scope="module")
def app():
    with pytest.MonkeyPatch.context() as env:  # the base page is tested with no key configured
        for name in ("ANTHROPIC_API_KEY", "ACCESS_CODE", "DAILY_TOKEN_BUDGET"):
            env.delenv(name, raising=False)
        yield AppTest.from_file(APP, default_timeout=90).run()


def test_the_page_runs_without_an_error_and_has_every_tab(app):
    assert not app.exception
    assert [t.label for t in app.tabs] == [
        "Overview",
        "Market trend",
        "Segments",
        "Forecasts",
        "Model results",
        "Data status",
        "Findings",
        "Market map",
        "Ask Claude",
    ]


def test_the_headline_numbers_are_the_warehouses(app):
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Share in Jul 2025"] == "1.88%"
    assert metrics["Peak (Dec 2021)"] == "3.38%" and metrics["Change since the peak"] == "-44%"
    assert metrics["Months of data"] == "72" and metrics["Latest month"] == "Jul 2025"
    assert metrics["Data freshness"] in ("Stale", "Fresh")


def test_the_caveats_are_on_the_page(app):
    page = " ".join([w.value for w in app.warning] + [m.value for m in app.markdown])
    assert "No newer extract has arrived" in page or "Fresh" in {m.value for m in app.metric}
    assert "run high" in page and "5.5% at 1 month and 10.9% at 3 months" in page
    assert "Payer, regional and prescription-volume data are not part of this analysis" in page
    infos = " ".join(i.value for i in app.info)
    assert "+5.5% too high" in infos and "+10.9% too high" in infos


def test_the_overview_has_cards_a_forecast_chart_status_cards_and_two_downloads(app):
    assert app.get("vega_lite_chart")  # the forecast chart under the cards
    badges = [m.value for m in app.markdown if m.value.startswith(":")]
    page = " ".join(m.value for m in app.markdown)
    for text in ("Rely on it as a baseline", "Use with care", "A supporting flag only"):
        assert text in page
    assert [d.proto.label for d in app.get("download_button")] == [
        "Download the monthly share (CSV)",
        "Download the forecast (CSV)",
    ]
    assert any("Use it for:" in m.value and "last month" in m.value for m in app.markdown)
    assert badges  # the data chips are badges too


def test_every_other_tab_starts_with_a_one_line_note_on_how_old_the_data_are(app):
    chips = [m.value for m in app.markdown if "-badge[Data to " in m.value]
    assert len(chips) == 8  # every tab but Overview
    assert all(c.startswith(":orange-badge[Data to Jul 2025") for c in chips)
    assert all("454 days old, no newer extract" in c for c in chips)
    banners = [w.value for w in app.warning if "No newer extract has arrived" in w.value]
    assert len(banners) == 1  # the long warning is on Overview only


def test_the_findings_tab_lists_all_seventeen(app):
    assert len(app.expander) == 17
    assert app.expander[0].label.startswith("1. ")


def test_no_secret_or_sql_input_exists_on_the_page(app):
    assert not app.text_input and not app.text_area  # no free-text box in this phase


# ------------------------------------------------------------------ the market map


def _map(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ACCESS_CODE", "DAILY_TOKEN_BUDGET"):
        monkeypatch.delenv(name, raising=False)
    return AppTest.from_file(APP, default_timeout=90).run()


def test_the_market_map_tab_opens_on_zilretta_with_its_facts_and_the_event_table(monkeypatch):
    at = _map(monkeypatch)
    assert not at.exception
    choice = next(s for s in at.selectbox if s.label == "Look closer at one thing in this map")
    assert choice.value == "product:ZILRETTA"
    page = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "Competitor groups are the project's own grouping" in page
    assert "Events are context only" in page
    assert any(s.value == "ZILRETTA" for s in at.subheader)
    assert any("Showing 10 of 10 connections" in c.value for c in at.caption)
    tables = [d.value for d in at.dataframe]
    assert any(len(frame) == 14 and "verified" in frame.columns for frame in tables)


def test_the_view_can_be_switched_and_every_view_runs(monkeypatch):
    at = _map(monkeypatch)
    view = next(r for r in at.radio if r.label == "What to show")
    assert view.options[0].startswith("Zilretta and the groups")
    for key in ("who_uses_it", "events", "competitive_set"):
        view.set_value(key).run()
        assert not at.exception
    view.set_value("events").run()
    slider = next(s for s in at.slider if s.label.startswith("How many manufacturers"))
    assert slider.proto.disabled  # the events view has nothing to count
    detail = next(s for s in at.selectbox if s.label == "Look closer at one thing in this map")
    assert len(detail.options) == 18 and detail.value == "product:ZILRETTA"  # 14 events + 4 others


def test_looking_closer_at_another_thing_shows_its_own_connections(monkeypatch):
    at = _map(monkeypatch)
    choice = next(s for s in at.selectbox if s.label == "Look closer at one thing in this map")
    choice.set_value("group:zilretta").run()
    assert any(s.value == "Zilretta" for s in at.subheader)
    assert any("connections. Sources:" in c.value for c in at.caption)


# ------------------------------------------------------------------ the question box


def _box(monkeypatch, **env):
    """The site run with the given secrets in the environment and a fresh set of limits."""
    for name in ("ANTHROPIC_API_KEY", "ACCESS_CODE", "DAILY_TOKEN_BUDGET"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    st.cache_resource.clear()
    return AppTest.from_file(APP, default_timeout=90).run()


def _stub_claude(monkeypatch, answer="Share was 1.88% in Jul 2025.", tokens=(100, 20), **extra):
    """Replace the network: a fake Anthropic client and a canned, streamed answer."""
    anthropic = pytest.importorskip("anthropic")
    from oa_market_intelligence.serving import qa

    calls = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: SimpleNamespace(**kw))

    def fake_ask(question, **kwargs):
        calls.append((question, kwargs))
        kwargs["on_round"]()
        kwargs["on_tool"]("get_data_status", {})
        for piece in answer.split(" "):
            kwargs["on_text"](piece + " ")
        return {
            "ok": True,
            "answer": answer,
            "tool_calls": [("get_data_status", {})],
            "sources": ["Overview tab (market series, forecasts, last run)"],
            "charts": [],
            "unchecked_numbers": [],
            "followups": ["How reliable are the forecasts?", "Show me the share trend as a chart"],
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            **extra,
        }

    monkeypatch.setattr(qa, "ask", fake_ask)
    return calls


def _say(at, text):
    return at.chat_input[0].set_value(text).run()


def _labels(at):
    return [b.label for b in at.button]


def test_without_a_key_the_box_is_off_and_the_site_still_works(monkeypatch):
    at = _box(monkeypatch)
    assert not at.exception and not at.chat_input and not at.text_area
    assert any("not switched on" in i.value for i in at.info)
    assert os.environ.get("ANTHROPIC_API_KEY") is None


def test_the_access_code_gates_the_box(monkeypatch):
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test", ACCESS_CODE="open-sesame")
    assert [t.label for t in at.text_input] == ["Access code"] and not at.chat_input
    assert at.text_input[0].proto.type == TextInputProto.PASSWORD  # typed characters are masked
    at.text_input[0].set_value("wrong").run()
    assert not at.chat_input and any("Enter the access code" in c.value for c in at.caption)
    at.text_input[0].set_value("open-sesame").run()
    assert at.chat_input


def test_the_box_opens_with_example_questions_for_two_audiences(monkeypatch):
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    text = " ".join(m.value for m in at.markdown)
    assert "For a brand manager" in text and "For an analyst" in text
    assert "How has Zilretta's share changed since its peak?" in _labels(at)
    assert len([b for b in at.button if b.label.endswith("?") or b.label.endswith(".")]) == 8


def test_an_answer_streams_in_with_its_sources_and_follow_ups_and_tokens_are_charged(monkeypatch):
    calls = _stub_claude(monkeypatch)
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test", DAILY_TOKEN_BUDGET="100")
    _say(at, "What is the latest share?")
    assert not at.exception
    question, kwargs = calls[0]
    assert question == "What is the latest share?" and kwargs["history"] == []
    assert kwargs["snapshot"].monthly.shape[0] == 72 and kwargs["snapshot"].graph is not None
    assert kwargs["client"].api_key == "sk-test"
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert any("1.88%" in m.value for m in at.markdown)
    assert any(c.value.startswith("Sources: Overview tab") for c in at.caption)
    assert any("get_data_status" in c.value for c in at.code)
    assert "How reliable are the forecasts?" in _labels(at)
    # 120 tokens were used against a budget of 100: the next question is refused, not sent
    _say(at, "And the peak?")
    assert len(calls) == 1 and any("daily limit" in w.value for w in at.warning)


def test_the_conversation_carries_over_and_can_be_cleared(monkeypatch):
    calls = _stub_claude(monkeypatch, tokens=(1, 1))
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _say(at, "What is the latest share?")
    _say(at, "And the peak?")
    history = calls[1][1]["history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "What is the latest share?"
    assert len(at.chat_message) == 4
    next(b for b in at.button if b.label == "Clear the conversation").click().run()
    assert len(
        at.chat_message
    ) == 0 and "How has Zilretta's share changed since its peak?" in _labels(at)


def test_clicking_a_follow_up_or_an_example_asks_it(monkeypatch):
    calls = _stub_claude(monkeypatch, tokens=(1, 1))
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    next(b for b in at.button if b.label == "Do the segment forecasts run high?").click().run()
    assert calls[0][0] == "Do the segment forecasts run high?"
    next(b for b in at.button if b.label == "How reliable are the forecasts?").click().run()
    assert calls[1][0] == "How reliable are the forecasts?"


def test_a_chart_and_the_figures_to_check_are_shown_with_the_answer(monkeypatch):
    _stub_claude(
        monkeypatch,
        charts=[{"chart": "trend", "params": {"start_month_id": 202101}}],
        unchecked_numbers=["2.5%"],
    )
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _say(at, "Show me the share trend as a chart.")
    assert not at.exception and at.get("vega_lite_chart")
    assert any(c.value.startswith("Check these figures: 2.5%") for c in at.caption)


def test_an_empty_or_over_long_question_is_refused_before_anything_is_sent(monkeypatch):
    calls = _stub_claude(monkeypatch)
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _say(at, "   ")
    assert not calls and any("empty" in w.value.lower() for w in at.warning)
    _say(at, "x" * 600)
    assert not calls and any("too long" in w.value.lower() for w in at.warning)


def test_one_visitor_is_limited_to_eight_questions_an_hour(monkeypatch):
    calls = _stub_claude(monkeypatch, tokens=(1, 1))
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    for i in range(9):
        _say(at, f"Question {i}?")
    assert len(calls) == 8 and any("Too many questions" in w.value for w in at.warning)


def test_a_failed_answer_is_shown_but_not_sent_back_as_history(monkeypatch):
    calls = _stub_claude(monkeypatch, tokens=(0, 0), ok=False)
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _say(at, "First?")
    _say(at, "Second?")
    assert calls[1][1]["history"] == [{"role": "user", "content": "First?"}]


def test_the_key_never_appears_on_the_page(monkeypatch):
    _stub_claude(monkeypatch)
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test-secret", ACCESS_CODE="code-xyz")
    page = " ".join(
        [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info]
    )
    assert "sk-test-secret" not in page and "code-xyz" not in page
