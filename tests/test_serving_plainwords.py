"""Tests for the plain-words layer (serving/plainwords.py): every page has its paragraph and its
terms, every term a page lists is defined, every chart note the pages ask for exists (and every note
is used), and the wording keeps the caveats the numbers need and claims no cause."""

import re
from pathlib import Path

import pytest

from oa_market_intelligence.serving import overview, plainwords, style

APP = Path(__file__).resolve().parent.parent / "app"


def _words(text):
    return len(text.split())


def test_every_page_has_a_plain_words_paragraph_of_a_few_sentences():
    assert set(plainwords.PAGE_SUMMARY) == set(overview.TAB_NAMES)
    for page, text in plainwords.PAGE_SUMMARY.items():
        assert 20 <= _words(text) <= 80, page
        assert plainwords.summary_for(page) == text
    assert plainwords.summary_for("Nowhere") is None


def test_every_term_a_page_lists_is_defined_and_every_definition_is_used():
    assert set(plainwords.PAGE_TERMS) == set(overview.TAB_NAMES)
    listed = {term for terms in plainwords.PAGE_TERMS.values() for term in terms}
    assert listed <= set(plainwords.GLOSSARY)
    assert set(plainwords.GLOSSARY) <= listed  # no definition nobody reads
    for terms in plainwords.PAGE_TERMS.values():
        assert len(terms) == len(set(terms))  # none twice on a page


def test_a_pages_terms_come_with_their_meanings_in_order():
    entries = plainwords.terms_for("Forecasts")
    assert [term for term, _ in entries] == list(plainwords.PAGE_TERMS["Forecasts"])
    assert all(meaning == plainwords.GLOSSARY[term] for term, meaning in entries)
    assert plainwords.terms_for("Nowhere") == []


def test_each_chart_note_says_how_to_read_it_and_what_it_does_not_tell_you():
    for key, note in plainwords.EXPLAIN.items():
        assert set(note) == {"read", "not"}, key
        assert 12 <= _words(note["read"]) <= 80, key
        assert 6 <= _words(note["not"]) <= 50, key
        assert plainwords.note_for(key) == note
    assert plainwords.note_for("nothing") is None


def test_every_note_the_pages_ask_for_exists_and_every_note_is_asked_for():
    asked = set()
    for path in APP.glob("*.py"):
        asked |= set(re.findall(r'common\.explain\("([a-z_0-9]+)"\)', path.read_text("utf-8")))
    assert asked == set(plainwords.EXPLAIN)


def test_the_wording_keeps_the_caveats_and_claims_no_cause():
    summary = plainwords.PAGE_SUMMARY
    assert "not verified" in summary["Overview"] and "last month's value" in summary["Overview"]
    assert "last month's value" in summary["Forecasts"] and "ran high" in summary["Forecasts"]
    assert "lead to confirm" in summary["Model results"]
    assert "Nothing here says what caused" in summary["Context"]
    assert "not that one causes" in summary["Market map"]
    assert "provisional" in plainwords.GLOSSARY["Round 2 (provisional)"].lower()
    assert "never a reason to act" in plainwords.GLOSSARY["Decline alert"]
    assert "not IQVIA" in plainwords.EXPLAIN["ctx_map"]["not"]
    assert "unverified" in plainwords.EXPLAIN["ctx_company"]["not"]
    assert "not verified" in plainwords.EXPLAIN["map_events"]["not"]
    everything = " ".join(
        [*plainwords.PAGE_SUMMARY.values(), *plainwords.GLOSSARY.values()]
        + [v for note in plainwords.EXPLAIN.values() for v in note.values()]
    ).lower()
    for banned in ("deployed", "awaiting", "will cause", "proves", "guarantee"):
        assert banned not in everything, banned


def test_the_plain_words_box_and_the_term_list_escape_what_they_show():
    box = style.plain_box("a < b & c")
    assert "In plain words" in box and "a &lt; b &amp; c" in box
    rows = style.glossary_rows([("<b>", "x & y")])
    assert "&lt;b&gt;" in rows and "x &amp; y" in rows and rows.count("oa-gloss") == 1


@pytest.mark.parametrize("page", overview.TAB_NAMES)
def test_every_page_has_no_figure_that_could_drift_from_the_data(page):
    # the plain-words layer gives no measured value: only fixed facts of the design appear
    text = plainwords.PAGE_SUMMARY[page].replace("Round 2", "Round two")
    for number in re.findall(r"\d[\d.,]*%?", text):
        assert number in {"1", "3", "6", "4"} or number.rstrip(".,") in {"1", "3", "6", "4"}, (
            page,
            number,
        )
