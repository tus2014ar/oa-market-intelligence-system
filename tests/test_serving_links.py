"""Tests for the links between pages (serving/links.py) and the clickable HTML builders in
serving/style.py: addresses with the filters in them, read back and validated, the back link's
source, the findings' targets, and links that are escaped and open in the same tab."""

import pytest

from oa_market_intelligence.serving import links, overview, style

VALID = {
    "specialty": {"ORTHOPEDIC SURGERY", "RHEUMATOLOGY"},
    "age": {"65 TO 74", "40 TO 59"},
    "gender": {"FEMALE", "MALE"},
}


# ------------------------------------------------------------------ the address of a page


def test_a_page_address_is_relative_and_carries_only_what_was_chosen():
    assert links.href("Segments") == "segments"
    assert links.href("Forecasts", specialty="RHEUMATOLOGY") == "forecasts?specialty=RHEUMATOLOGY"
    assert links.href("Market trend", age="65 TO 74", gender="MALE", period="3Y") == (
        "market-trend?age=65%20TO%2074&gender=MALE&period=3Y"
    )
    assert links.href("Overview") == "./"
    assert links.href("Overview", source="Segments") == "./?from=segments"


def test_the_period_all_is_left_out_because_it_is_the_default():
    assert links.href("Segments", period="All") == "segments"
    assert links.href("Segments", period="3Y") == "segments?period=3Y"
    assert links.href("Segments", period="7Y") == "segments"  # not a period: dropped


def test_the_source_page_and_a_section_are_in_the_address():
    address = links.href(
        "Forecasts", specialty="OTHER", source="Overview", section="Decline alert, 1 month ahead"
    )
    assert address == "forecasts?specialty=OTHER&from=overview#decline-alert-1-month-ahead"


def test_a_value_with_spaces_ampersands_or_slashes_is_quoted():
    address = links.href("Segment detail", specialty="PHYSICAL MEDICINE & REHAB")
    assert address == "segment-detail?specialty=PHYSICAL%20MEDICINE%20%26%20REHAB"
    assert "&REHAB" not in address


def test_an_unknown_page_is_refused():
    with pytest.raises(ValueError, match="no page called"):
        links.href("Nowhere")


def test_every_page_has_an_address_and_a_title_back():
    for title, _, _, slug in overview.PAGES:
        assert links.SLUGS[title] == slug and links.TITLES[slug] == title


def test_section_anchors_are_what_streamlit_makes_of_a_heading():
    assert links.anchor_id("Decline alert, 1 month ahead") == "decline-alert-1-month-ahead"
    assert links.anchor_id("Segment forecasts") == "segment-forecasts"
    assert links.anchor_id("Round 2: improvements tried after round 1") == (
        "round-2-improvements-tried-after-round-1"
    )
    assert links.anchor_id("  Events,   oldest first ") == "events-oldest-first"


# ------------------------------------------------------------------ reading an address back


def test_the_filters_and_the_source_are_read_back_from_an_address():
    params = {"specialty": "RHEUMATOLOGY", "age": "65 TO 74", "period": "3Y", "from": "segments"}
    filters, source = links.parse(params, VALID)
    assert filters == {
        "specialty": "RHEUMATOLOGY",
        "age": "65 TO 74",
        "gender": None,
        "period": "3Y",
    }
    assert source == "Segments"


def test_a_value_the_data_does_not_have_is_dropped_never_an_error():
    filters, source = links.parse(
        {"specialty": "MADE UP", "gender": "MALE", "period": "9Y", "from": "nowhere"}, VALID
    )
    assert filters == {"specialty": None, "age": None, "gender": "MALE", "period": "All"}
    assert source is None


def test_no_parameters_is_the_unfiltered_default():
    assert links.parse({}, VALID) == (
        {"specialty": None, "age": None, "gender": None, "period": "All"},
        None,
    )


def test_a_list_or_none_in_a_parameter_is_ignored():
    filters, _ = links.parse({"specialty": None, "age": ["65 TO 74"]}, VALID)
    assert filters["specialty"] is None and filters["age"] is None


def test_what_is_written_to_the_address_reads_back_the_same():
    chosen = {"specialty": "ORTHOPEDIC SURGERY", "age": None, "gender": "FEMALE", "period": "5Y"}
    written = links.to_params(chosen, "Segment detail")
    assert written == {
        "specialty": "ORTHOPEDIC SURGERY",
        "gender": "FEMALE",
        "period": "5Y",
        "from": "segment-detail",
    }
    back, source = links.parse(written, VALID)
    assert back == chosen and source == "Segment detail"


def test_an_unfiltered_page_has_a_clean_address():
    assert links.to_params({"specialty": None, "age": None, "gender": None, "period": "All"}) == {}


# ------------------------------------------------------------------ the findings' targets


def test_every_finding_has_an_entry_and_its_target_is_a_real_page():
    assert sorted(links.FINDING_LINKS) == list(range(1, 18))
    for target in links.FINDING_LINKS.values():
        assert target is None or target[0] in links.SLUGS


def test_a_finding_link_says_where_it_goes_and_a_finding_without_a_page_has_none():
    label, address = links.finding_link(16)
    assert label == "See it on Forecasts"
    assert address == "forecasts?from=findings#decline-alert-1-month-ahead"
    assert links.finding_link(1)[1].startswith("context?from=findings#company-sales")
    assert links.finding_link(10) is None and links.finding_link(11) is None


# ------------------------------------------------------------------ clickable HTML


def test_a_link_opens_in_the_same_tab_and_is_escaped():
    html = style.link("segments?a=1&b=2", "<b>x</b>", "oa-rowlink")
    assert 'target="_self"' in html and 'href="segments?a=1&amp;b=2"' in html
    assert 'class="oa-link oa-rowlink"' in html and html.endswith("</a>")
    assert style.link(None, "inner") == "inner" and style.link("", "inner") == "inner"
    assert '"' not in style.link('x"onmouseover="bad', "t").split('href="')[1].split('"')[0]


def test_a_card_with_an_address_is_one_link_with_an_arrow_and_one_without_is_not():
    plain = style.kpi_card("L", "1%", "c")
    linked = style.kpi_card("L", "1%", "c", href="market-trend?from=overview")
    assert "oa-go" not in plain and "<a " not in plain
    assert linked.startswith('<a class="oa-link"') and "oa-go" in linked
    assert 'href="market-trend?from=overview"' in linked and linked.count("</a>") == 1


def test_rows_become_links_only_where_they_have_an_address():
    rows = [("A", "1%", 0.1, "+0.10 pp"), ("B", "2%", -0.1, "-0.10 pp")]
    html = style.rank_rows(rows, ("g", "s", "c"), ["x", None])
    assert html.count("<a ") == 1 and 'href="x"' in html
    assert style.rank_rows(rows, ("g", "s", "c")).count("<a ") == 0
    bars = style.bar_rows([("A", 5.0, "5%"), ("B", 3.0, "3%")], links=[None, "y"])
    assert bars.count("<a ") == 1 and 'href="y"' in bars
    guide = style.guide_rows([("Tab", "q?")], ["t"])
    assert guide.count("<a ") == 1 and "oa-guide-tab" in guide


def test_a_finding_shows_its_see_it_link_after_the_evidence_when_there_is_one():
    with_link = style.finding_card(
        1, "T", "M", "E", "S", ("See it on Context", "context?from=findings")
    )
    without = style.finding_card(1, "T", "M", "E", "S")
    assert "oa-find-see" in with_link and "See it on Context →" in with_link
    assert with_link.index("</details>") < with_link.index("oa-find-see")
    assert "oa-find-see" not in without


def test_a_small_see_link_has_an_arrow_and_an_escaped_address():
    html = style.link_line("Go", "forecasts?a=1&b=2#x")
    assert html == ('<a class="oa-see" href="forecasts?a=1&amp;b=2#x" target="_self">Go →</a>')


# ------------------------------------------------------------------ what a click in a chart chose


def test_a_click_gives_the_values_of_the_fields_asked_for():
    selection = {"pick": [{"group": "RHEUMATOLOGY", "vlc_ignored": 1}]}
    assert links.picked(selection, "pick", ["group"]) == {"group": "RHEUMATOLOGY"}
    cell = {"pick": [{"row": "OTHER", "column": "65 TO 74"}]}
    assert links.picked(cell, "pick", ["row", "column"]) == {"row": "OTHER", "column": "65 TO 74"}


def test_no_click_or_an_odd_one_never_gives_a_choice():
    assert links.picked(None, "pick", ["group"]) is None
    assert links.picked({}, "pick", ["group"]) is None
    assert links.picked({"pick": []}, "pick", ["group"]) is None
    assert links.picked({"pick": "x"}, "pick", ["group"]) is None
    assert links.picked({"pick": [None]}, "pick", ["group"]) is None
    assert links.picked({"pick": [{"other": 1}]}, "pick", ["group"]) is None  # field missing
    assert links.picked({"pick": [{"row": "A"}]}, "pick", ["row", "column"]) is None
    assert links.picked({"another": [{"group": "A"}]}, "pick", ["group"]) is None


def test_only_the_first_point_clicked_is_used():
    selection = {"pick": [{"group": "A"}, {"group": "B"}]}
    assert links.picked(selection, "pick", ["group"]) == {"group": "A"}


# ------------------------------------------------------------------ a range dragged on a chart


def test_a_dragged_range_gives_its_start_and_end_in_milliseconds():
    selection = {"brush": {"month": [1646092800000, 1719792000000.5]}}
    assert links.brushed(selection, "brush", "month") == (1646092800000.0, 1719792000000.5)


def test_no_range_or_an_odd_one_never_narrows_anything():
    assert links.brushed(None, "brush", "month") is None
    assert links.brushed({}, "brush", "month") is None
    assert links.brushed({"brush": {}}, "brush", "month") is None
    assert links.brushed({"brush": {"other": [1, 2]}}, "brush", "month") is None
    assert links.brushed({"brush": {"month": [1]}}, "brush", "month") is None
    assert links.brushed({"brush": {"month": [1, 2, 3]}}, "brush", "month") is None
    assert links.brushed({"brush": {"month": ["a", "b"]}}, "brush", "month") is None
    assert links.brushed({"brush": {"month": [True, 5]}}, "brush", "month") is None
    assert links.brushed({"brush": {"month": [float("nan"), 5]}}, "brush", "month") is None
    assert links.brushed({"brush": {"month": [5, 5]}}, "brush", "month") is None  # empty range
    assert links.brushed({"brush": {"month": [9, 5]}}, "brush", "month") is None  # backwards
    assert links.brushed({"brush": "x"}, "brush", "month") is None
