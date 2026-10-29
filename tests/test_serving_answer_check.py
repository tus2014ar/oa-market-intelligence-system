"""Tests for the number check (src/.../serving/answer_check.py): which figures in an answer were not
in the data Claude looked up. Every rule is checked by hand on small inputs."""

from oa_market_intelligence.serving.answer_check import unchecked_numbers

OUT = [
    {
        "latest_share_pct": 1.8837,
        "change_since_peak_pct": -44.2,
        "visits": 135119,
        "bias": 0.0549,
        "coverage": "the 90% range held in 96% of months",
    }
]


def _missing(answer, outputs=OUT, question=""):
    return unchecked_numbers(answer, outputs, question)


def test_a_figure_that_rounds_to_a_looked_up_value_is_found():
    assert _missing("Share was 1.88% in Jul 2025.") == []
    assert _missing("Share was 1.9%.") == []
    assert _missing("Share was 2%.") == []  # rounded to a whole number as well


def test_the_sign_and_the_percent_scale_do_not_matter():
    assert _missing("It is 44% below the peak.") == []  # the tool says -44.2
    assert _missing("It is -44.2% from the peak.") == []
    assert _missing("The forecast runs 5.5% high.") == []  # the tool holds the fraction 0.0549


def test_a_thousands_figure_must_match_exactly_after_rounding():
    assert _missing("There were 135,119 visits.") == []
    assert _missing("There were 135,000 visits.") == ["135,000"]


def test_numbers_inside_the_looked_up_text_count_as_looked_up():
    assert _missing("The range held in 96% of months.") == []


def test_a_figure_nowhere_in_the_data_is_listed_once_as_written():
    assert _missing("Share fell to 1.2%, and 1.2% again.") == ["1.2%"]
    assert _missing("It is 7.7% and 3.14.") == ["7.7%", "3.14"]


def test_the_questions_own_numbers_are_allowed():
    assert _missing("About 2,500 visits, as you said.", question="Is it 2,500 visits?") == []


def test_years_small_whole_numbers_dates_ids_and_design_constants_are_ignored():
    answer = (
        "In 2021 and 2025, at 1, 3 and 6 months, on 2021-11-19, see event:11, "
        "list item 12, a 90% range and 100% of cases."
    )
    assert _missing(answer, outputs=[]) == []


def test_a_list_comma_is_not_read_as_a_thousands_separator():
    assert _missing("Look at 1, 3 and 6 months ahead.", outputs=[]) == []


def test_booleans_none_and_nested_values_are_handled():
    outputs = [{"flag": True, "none": None, "rows": [{"share": 2.75}], "deep": {"a": [{"b": 9.5}]}}]
    assert _missing("Rows show 2.75 and 9.5.", outputs) == []
    assert _missing("It is 1.0 and 0.0.", outputs) == ["1.0", "0.0"]  # True is not the number 1


def test_nothing_looked_up_means_every_real_figure_is_listed():
    assert _missing("Share is 1.88%.", outputs=[]) == ["1.88%"]
