"""Tests for the pure helpers of the raw-data profiling runner."""

import pandas as pd
import pytest

from oa_market_intelligence.external.profile_all import (
    asp_quarter,
    crosswalk_candidates,
    find_asp_header,
    matching_names,
    normalise_specialty,
    observe,
    sec_period,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("january-2021-asp-pricing-file.zip", "2021Q1"),
        ("April_2020_ASP_Pricing_File_updated_022421.zip", "2020Q2"),
        ("jul_2019_asp_pricing_file_updated_052920.zip", "2019Q3"),
        ("october-2025-asp-pricing-final-file.zip", "2025Q4"),
        ("january-2025-asp-pricing-file-03-11-25-final-file.zip", "2025Q1"),
        ("notes.zip", None),
    ],
)
def test_asp_file_names_map_to_a_quarter(name, expected):
    assert asp_quarter(name) == expected


def test_the_asp_header_is_found_below_any_number_of_title_lines():
    lines = [
        "Title line,,",
        "",
        "HCPCS Code,Short Description,HCPCS Code Dosage,Payment Limit",
        "J3304,x,1 MG,1.0",
    ]
    assert find_asp_header(lines) == 2
    assert find_asp_header(["no header here"]) is None


def test_the_sec_period_is_read_from_the_cover_page_through_markup():
    html = "<p>For the quarterly period ended <b>September&#160;30,</b> 2021</p>".replace(
        "&#160;", " "
    )
    assert sec_period(html) == "September 30, 2021"
    annual = "<span>For the fiscal year ended</span> <span>December 31, 2020</span>"
    assert sec_period(annual) == "December 31, 2020"
    assert sec_period("nothing") is None


def test_specialty_names_are_normalised_for_comparison():
    assert normalise_specialty("Physical Medicine & Rehab") == "PHYSICAL MEDICINE AND REHAB"
    assert normalise_specialty("Orthopedic Surgery ") == "ORTHOPEDIC SURGERY"


def test_crosswalk_candidates_flag_exact_matches_and_suggest_near_ones_for_review():
    rows = crosswalk_candidates(
        ["PHYSICAL MEDICINE & REHAB", "RHEUMATOLOGY", "RARE (grouped)"],
        ["Physical Medicine and Rehabilitation", "Rheumatology", "Anesthesiology"],
    )
    by_name = {r["left_name"]: r for r in rows}
    assert by_name["RHEUMATOLOGY"]["exact_match"] is True
    assert (
        by_name["PHYSICAL MEDICINE & REHAB"]["suggestion_1"]
        == "Physical Medicine and Rehabilitation"
    )
    assert by_name["PHYSICAL MEDICINE & REHAB"]["exact_match"] is False
    assert all(r["decision"] == "" for r in rows)  # decisions are made by a person, not the code


def test_matching_names_is_case_insensitive_and_ignores_empties():
    series = pd.Series(["ZILRETTA", "Kenalog-40", "", "  ", "Tylenol", "zilretta"])
    hits = matching_names(series, "zilretta|kenalog")
    assert list(hits) == ["ZILRETTA", "Kenalog-40", "zilretta"]


def test_observe_passes_chunks_through_unchanged_while_the_callback_sees_each():
    chunks = [pd.DataFrame({"a": ["1"]}), pd.DataFrame({"a": ["2", "3"]})]
    seen = []
    out = list(observe(iter(chunks), lambda c: seen.append(len(c))))
    assert [len(c) for c in out] == [1, 2] and seen == [1, 2]
