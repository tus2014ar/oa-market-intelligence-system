"""Tests for the Ask Claude page's logic and wording (serving/askview.py): the example questions,
what each would look up (run on the committed warehouse), the eleven tools in plain words, the page
behind each source, and the plain account of what the box can and cannot do. No model is called."""

import json

import pytest

from oa_market_intelligence.serving import askview, data, overview, qa, qa_tools


@pytest.fixture(scope="module")
def snap():
    if not data.DB_PATH.exists():
        pytest.skip("no published warehouse")
    engine = data.open_engine()
    try:
        return qa_tools.load_snapshot(engine)
    finally:
        engine.dispose()


def _examples():
    return [q for group in askview.EXAMPLE_QUESTIONS.values() for q in group]


def test_there_are_four_example_questions_for_each_of_two_audiences():
    assert list(askview.EXAMPLE_QUESTIONS) == ["For a brand manager", "For an analyst"]
    assert [len(v) for v in askview.EXAMPLE_QUESTIONS.values()] == [4, 4]
    assert len(set(_examples())) == 8


def test_every_example_has_lookups_made_of_real_tools_and_nothing_else_does():
    assert set(askview.LOOKUPS) == set(_examples())
    for question, lookups in askview.LOOKUPS.items():
        assert lookups, question
        for tool, arguments in lookups:
            assert tool in qa_tools.TOOLS and isinstance(arguments, dict), (question, tool)


def test_the_eleven_tools_are_each_named_in_plain_words_with_their_page():
    defined = {tool["name"] for tool in qa_tools.TOOL_DEFINITIONS}
    assert set(askview.TOOL_PLAIN) == defined == set(qa_tools.TOOLS) and len(defined) == 11
    for tool, (label, page) in askview.TOOL_PLAIN.items():
        assert label and (page is None or page in overview.TAB_NAMES), tool
    assert askview.TOOL_PLAIN["show_chart"][1] is None  # it draws; it reads nothing
    assert {p for _, p in askview.TOOL_PLAIN.values() if p} == {
        "Overview",
        "Market trend",
        "Segments",
        "Forecasts",
        "Model results",
        "Findings",
        "Market map",
    }


def test_each_source_the_box_can_give_points_at_the_page_it_came_from():
    for tool, source in qa.TABLE_SOURCES.items():
        assert askview.source_page(source) == askview.TOOL_PLAIN[tool][1], tool
    assert askview.source_page("Knowledge graph (FDA label, CMS)") == "Market map"
    assert askview.source_page("Something else") is None
    assert askview.source_page("") is None


def test_the_pages_behind_a_list_of_sources_come_once_each_in_order():
    sources = [
        qa.TABLE_SOURCES["get_forecasts"],
        qa.TABLE_SOURCES["get_model_results"],
        "Knowledge graph (CMS)",
        qa.TABLE_SOURCES["get_forecasts"],
        "unknown",
    ]
    assert askview.source_pages(sources) == ["Forecasts", "Model results", "Market map"]
    assert askview.source_pages([]) == []


def test_every_example_question_has_data_to_show_and_none_of_it_is_an_error(snap):
    for question in _examples():
        found = askview.lookup_preview(question, snap)
        assert found and len(found) == len(askview.LOOKUPS[question]), question
        for look in found:
            assert "error" not in look["output"], (question, look["tool"], look["output"])
            assert look["label"] == askview.TOOL_PLAIN[look["tool"]][0]
            assert look["page"] == askview.TOOL_PLAIN[look["tool"]][1]
            json.dumps(look["output"])  # the page shows it as JSON: it must be JSON-safe


def test_a_question_that_is_not_an_example_has_nothing_to_preview(snap):
    assert askview.lookup_preview("What is the weather?", snap) == []


def test_the_preview_is_the_same_data_the_tool_gives_claude(snap):
    [look] = askview.lookup_preview("Who are Zilretta's competitors in this project?", snap)
    assert look["output"] == qa_tools.run_tool("get_competitors", {}, snapshot=snap)
    chart = askview.lookup_preview("Show me the share trend as a chart.", snap)[0]
    assert chart["output"]["chart"] == "trend"


def test_the_account_of_how_it_works_is_honest_about_limits_and_testing():
    text = " ".join(askview.HOW_IT_WORKS)
    assert "read-only" in text and "patient-level" in text and "can still be wrong" in text
    assert "not yet been tried with a live model" in text
    assert f"{askview.PER_VISITOR_PER_HOUR} questions an hour" in text
    assert askview.PER_VISITOR_PER_HOUR == 8 and askview.SITE_PER_HOUR == 200
    assert askview.DEFAULT_DAILY_TOKENS == 300_000
    for banned in ("deployed", "live now", "guarantee", "always correct"):
        assert banned not in text.lower()
