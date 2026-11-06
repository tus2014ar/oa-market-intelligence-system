"""Tests for the Overview and Data status logic (serving/overview.py) and the pieces of
serving/style.py they use (the card sparkline and the tab guide). No Streamlit needed."""

import pytest

from oa_market_intelligence.serving import overview, style

STATUS = {
    "run_status": "ok",
    "finished_at": "2026-10-28T21:18:22+00:00",
    "months": 72,
    "last_month_id": 202507,
    "freshness": "stale",
    "age_days": 454,
    "max_age_days": 100,
    "versions": {"data": "28b9a7bd3234", "code": "6cdadcc127", "environment": "9a63b5dfe9be"},
}


# ------------------------------------------------------------------ the tabs and their guide


def test_the_tab_row_is_ten_distinct_names_starting_with_the_overview():
    assert len(overview.TAB_NAMES) == 10 and len(set(overview.TAB_NAMES)) == 10
    assert overview.TAB_NAMES[0] == "Overview" and overview.TAB_NAMES[-1] == "Ask Claude"


def test_the_guide_covers_every_tab_but_the_overview_in_the_order_of_the_row():
    guide = overview.guide()
    assert [tab for tab, _ in guide] == list(overview.TAB_NAMES[1:])
    assert all(question.strip() and question[0].isupper() for _, question in guide)
    assert all(question.endswith(("?", ".", ")")) for _, question in guide)


def test_the_guide_does_not_promise_a_live_question_box():
    ask = dict(overview.guide())["Ask Claude"]
    assert "off until a key is configured" in ask


def test_the_guide_html_escapes_and_has_one_row_per_tab():
    html = style.guide_rows([("<b>Tab</b>", "Why & how?"), ("Other", "q")])
    assert html.count('class="oa-guide"') == 2 and "<b>" not in html
    assert "&lt;b&gt;" in html and "Why &amp; how?" in html and style.guide_rows([]) == ""


# ------------------------------------------------------------------ the run cards


def test_the_four_run_cards_by_hand():
    cards = overview.run_cards(STATUS)
    assert [c[0] for c in cards] == [
        "Last publish run",
        "Months of data",
        "Latest month",
        "Data freshness",
    ]
    assert cards[0][1:] == ("Ok", "Finished 2026-10-28.")
    assert cards[1][1] == "72" and cards[2][1] == "Jul 2025"
    assert (
        cards[3][1] == "Stale"
        and cards[3][2] == "454 days since the latest month; stale after 100."
    )


def test_a_run_with_nothing_on_record_reads_n_a_and_never_fails():
    cards = overview.run_cards({})
    assert cards[0][1:] == ("Unknown", "No run on record.")
    assert cards[1][1] == "n/a" and cards[2][1] == "n/a" and cards[3][1] == "N/A"
    assert cards[3][2] == "No freshness check on record."


def test_the_versions_are_listed_with_capitalised_names():
    assert overview.version_rows(STATUS) == [
        ("Data", "28b9a7bd3234"),
        ("Code", "6cdadcc127"),
        ("Environment", "9a63b5dfe9be"),
    ]
    assert overview.version_rows({}) == []


# ------------------------------------------------------------------ the card sparkline


def test_a_sparkline_runs_from_the_left_edge_to_the_right_and_keeps_its_range():
    svg = style.sparkline([1.0, 2.0, 3.0], width=100, height=40)
    points = svg.split('points="')[-1].split('"')[0].split()
    xs = [float(p.split(",")[0]) for p in points]
    ys = [float(p.split(",")[1]) for p in points]
    assert len(points) == 3 and xs == pytest.approx([3.0, 50.0, 97.0])
    assert ys[0] == pytest.approx(37.0) and ys[-1] == pytest.approx(3.0)  # low at the bottom
    assert 'class="oa-spark"' in svg and 'aria-hidden="true"' in svg


def test_a_flat_or_short_or_gappy_series_does_not_break_the_sparkline():
    assert style.sparkline([2.0, 2.0, 2.0]) != ""  # a flat line, no division by zero
    assert style.sparkline([1.0]) == "" and style.sparkline([]) == ""
    assert style.sparkline([1.0, float("nan"), 3.0]).count(",") >= 2  # the gap is dropped
    assert style.sparkline([float("nan"), float("nan")]) == ""


def test_an_area_sparkline_is_filled_and_a_line_one_is_not():
    assert "<polygon" in style.sparkline([1, 2, 3], area=True)
    assert "<polygon" not in style.sparkline([1, 2, 3])


def test_a_card_has_a_sparkline_only_when_given_one():
    plain = style.kpi_card("L", "1%", "c")
    lined = style.kpi_card("L", "1%", "c", spark=[1.0, 2.0, 3.0])
    assert "oa-spark" not in plain and "oa-spark" in lined
    assert lined.index('class="c"') < lined.index("oa-spark")  # the line sits under the caption
