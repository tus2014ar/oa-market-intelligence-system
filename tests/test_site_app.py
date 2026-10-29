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


def test_the_findings_tab_lists_all_seventeen(app):
    assert len(app.expander) == 17
    assert app.expander[0].label.startswith("1. ")


def test_no_secret_or_sql_input_exists_on_the_page(app):
    assert not app.text_input and not app.text_area  # no free-text box in this phase


# ------------------------------------------------------------------ the question box


def _box(monkeypatch, **env):
    """The site run with the given secrets in the environment and a fresh set of limits."""
    for name in ("ANTHROPIC_API_KEY", "ACCESS_CODE", "DAILY_TOKEN_BUDGET"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    st.cache_resource.clear()
    return AppTest.from_file(APP, default_timeout=90).run()


def _ask_button(at):
    return next(b for b in at.button if b.label == "Ask")


def _stub_claude(monkeypatch, answer="Share was 1.88% in Jul 2025.", tokens=(100, 20)):
    """Replace the network: a fake Anthropic client and a canned answer, recording the calls."""
    anthropic = pytest.importorskip("anthropic")
    from oa_market_intelligence.serving import qa

    calls = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: SimpleNamespace(**kw))

    def fake_ask(question, **kwargs):
        calls.append((question, kwargs))
        return {
            "ok": True,
            "answer": answer,
            "tool_calls": [("get_data_status", {})],
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
        }

    monkeypatch.setattr(qa, "ask", fake_ask)
    return calls


def test_without_a_key_the_box_is_off_and_the_site_still_works(monkeypatch):
    at = _box(monkeypatch)
    assert not at.exception and not at.text_area
    assert any("not switched on" in i.value for i in at.info)
    assert os.environ.get("ANTHROPIC_API_KEY") is None


def test_the_access_code_gates_the_box(monkeypatch):
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test", ACCESS_CODE="open-sesame")
    assert [t.label for t in at.text_input] == ["Access code"] and not at.text_area
    assert at.text_input[0].proto.type == TextInputProto.PASSWORD  # typed characters are masked
    at.text_input[0].set_value("wrong").run()
    assert not at.text_area and any("Enter the access code" in c.value for c in at.caption)
    at.text_input[0].set_value("open-sesame").run()
    assert at.text_area and _ask_button(at)


def test_a_question_is_answered_from_the_tools_and_the_tokens_are_charged(monkeypatch):
    calls = _stub_claude(monkeypatch)
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test", DAILY_TOKEN_BUDGET="100")
    at.text_area[0].set_value("What is the latest share?")
    _ask_button(at).click().run()
    assert not at.exception
    assert any("1.88%" in m.value for m in at.markdown)
    assert any("get_data_status" in c.value for c in at.code)
    question, kwargs = calls[0]
    assert question == "What is the latest share?" and kwargs["snapshot"].monthly.shape[0] == 72
    assert kwargs["client"].api_key == "sk-test"
    # 120 tokens were used against a budget of 100: the next question is refused, not sent
    _ask_button(at).click().run()
    assert len(calls) == 1 and any("daily limit" in w.value for w in at.warning)


def test_an_empty_question_is_refused_before_anything_is_sent(monkeypatch):
    calls = _stub_claude(monkeypatch)
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _ask_button(at).click().run()
    assert not calls and any("empty" in w.value.lower() for w in at.warning)


def test_one_visitor_is_limited_to_eight_questions_an_hour(monkeypatch):
    calls = _stub_claude(monkeypatch, tokens=(1, 1))
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    at.text_area[0].set_value("What is the latest share?")
    for _ in range(9):
        _ask_button(at).click().run()
    assert len(calls) == 8 and any("Too many questions" in w.value for w in at.warning)


def test_the_key_never_appears_on_the_page(monkeypatch):
    _stub_claude(monkeypatch)
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test-secret", ACCESS_CODE="code-xyz")
    page = " ".join(
        [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info]
    )
    assert "sk-test-secret" not in page and "code-xyz" not in page
