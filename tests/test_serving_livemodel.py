"""Tests for the wording of the Live model page (src/.../serving/livemodel.py): the structure the
page draws, and the rules that keep it honest (the figures in it equal the code and the documents
they come from, and it never claims more than the system has)."""

import re
from pathlib import Path

from oa_market_intelligence.serving import livemodel, style
from oa_market_intelligence.serving import livepanel as lp

ROOT = Path(__file__).resolve().parent.parent


def test_the_page_has_six_steps_each_with_a_plain_sentence_and_a_technical_detail():
    assert len(livemodel.STEPS) == 6 and len(livemodel.FLOW) == 6
    for title, plain, detail in livemodel.STEPS:
        assert title and len(plain) > 60 and len(detail) > 60


def test_the_brand_manager_cards_and_the_controls_are_complete():
    assert len(livemodel.BRAND_MANAGER) == 4
    assert all(len(card) == 3 and all(card) for card in livemodel.BRAND_MANAGER)
    assert len(livemodel.DEPENDABLE) == 7
    assert all(len(row) == 3 and all(row) for row in livemodel.DEPENDABLE)
    assert len(livemodel.LIMITS) == 4


def test_the_service_limits_the_page_quotes_equal_the_code():
    import sys

    sys.path.insert(0, str(ROOT / "app"))
    text = " ".join(detail for _, _, detail in livemodel.STEPS)
    assert "20 live runs an hour" in text and "200" in text and "ten-minute cache" in text
    panel = (ROOT / "app" / "live_panel.py").read_text("utf-8")
    assert "PER_VISITOR_PER_HOUR = 20  #" in panel
    assert "SITE_PER_HOUR = 200  #" in panel
    assert "ANSWER_SECONDS = 600  #" in panel  # ten minutes
    api = (ROOT / "src" / "oa_market_intelligence" / "api" / "main.py").read_text("utf-8")
    assert "DEFAULT_RATE_PER_MINUTE = 60" in api or "default 60" in api
    remote = (ROOT / "src" / "oa_market_intelligence" / "api" / "remote_data.py").read_text("utf-8")
    assert "DEFAULT_REFRESH_SECONDS = 300" in remote  # five minutes
    assert "five minutes" in text


def test_the_load_test_numbers_equal_the_load_test_document():
    doc = (ROOT / "docs" / "loadtest.md").read_text("utf-8")
    row = next(r for r in livemodel.DEPENDABLE if r[0] == "A load test")
    assert "| 50 | after | **193.1** | 51 ms | **190 ms**" in doc
    assert "193 requests a second" in row[1] and "190 ms" in row[1]
    assert "Not yet run against the live function with a key" in row[1]


def test_the_page_does_not_overclaim():
    everything = " ".join(
        [livemodel.HEADLINE, livemodel.DEMO]
        + [" ".join(step) for step in livemodel.STEPS]
        + [" ".join(card) for card in livemodel.BRAND_MANAGER]
        + [" ".join(row) for row in livemodel.DEPENDABLE]
        + list(livemodel.LIMITS)
    ).lower()
    banned = ("production-grade", "guaranteed", "always accurate", "100% uptime", "enterprise")
    for phrase in banned:
        assert phrase not in everything
    limits = " ".join(livemodel.LIMITS)
    assert "not a what-if tool" in limits and "production-style" in limits
    assert "free plan" in limits and "staging" in limits


def test_every_document_the_page_points_to_exists():
    for _, _, where in livemodel.DEPENDABLE:
        for part in re.findall(r"(?:docs|tests|\.github/workflows)/[\w./-]+", where):
            assert (ROOT / part.rstrip(",")).exists(), part
    assert (ROOT / "docs" / "decision_log.md").read_text("utf-8").count("**DL-124") == 1


def test_the_flow_row_draws_every_box_with_arrows_between():
    html = style.flow_row(livemodel.FLOW)
    assert html.count("oa-flow-box") == 6 and html.count("oa-flow-arrow") == 5
    assert "AWS Lambda" in html and "<script" not in html


def test_the_panel_says_what_it_compares_with_in_words_that_make_sense_on_its_own_page():
    assert "stored" in lp.LEAD and "above" not in lp.LEAD
    assert "above" not in lp.SWITCHED_OFF and "above" not in lp.UNAVAILABLE_TAIL
