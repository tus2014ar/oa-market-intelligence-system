"""Tests for the logic and wording of the "Run the model live" panel (src/.../serving/livepanel.py,
DL-122): the choices a visitor sees, the comparison of the live answer with the stored forecast,
the comparison of data versions, and the sentences. No Streamlit and no network."""

import pandas as pd
import pytest

from oa_market_intelligence.serving import livepanel as lp


def frame() -> pd.DataFrame:
    rows = [
        # specialty, age band, gender, visits, forecast
        ("RHEUMATOLOGY", "65 TO 74", "FEMALE", 169, 1.7326),
        ("RHEUMATOLOGY", "65 TO 74", "MALE", 90, 1.1),
        ("RHEUMATOLOGY", "40 TO 59", "FEMALE", 140, 2.0),
        ("PAIN MEDICINE", "60 TO 64", "MALE", 300, 3.5),
        ("PAIN MEDICINE", "60 TO 64", "FEMALE", 250, 3.0),
    ]
    return pd.DataFrame(
        rows, columns=["specialty", "age_band", "gender", "origin_visits", "forecast_pct"]
    )


# ------------------------------------------------------------------ the choices


def test_choices_list_only_what_has_a_forecast_busiest_first():
    view = frame()
    assert lp.specialties(view) == ["PAIN MEDICINE", "RHEUMATOLOGY"]
    assert lp.age_bands(view, "RHEUMATOLOGY") == ["65 TO 74", "40 TO 59"]
    assert lp.age_bands(view, "PAIN MEDICINE") == ["60 TO 64"]
    assert lp.genders(view, "RHEUMATOLOGY", "65 TO 74") == ["FEMALE", "MALE"]
    assert lp.genders(view, "PAIN MEDICINE", "60 TO 64") == ["MALE", "FEMALE"]
    assert lp.genders(view, "RHEUMATOLOGY", "40 TO 59") == ["FEMALE"]  # no male row, no option
    assert lp.age_bands(view, "NOT A SPECIALTY") == []


def test_the_empty_view_has_no_choices():
    empty = frame().iloc[0:0]
    assert lp.specialties(empty) == []


def test_the_stored_forecast_is_the_pages_value_for_that_segment_or_none():
    view = frame()
    assert lp.stored_forecast(view, "RHEUMATOLOGY", "65 TO 74", "FEMALE") == pytest.approx(1.7326)
    assert lp.stored_forecast(view, "RHEUMATOLOGY", "40 TO 59", "MALE") is None


# ------------------------------------------------------------------ the comparison


def test_a_live_forecast_equal_to_the_stored_one_matches():
    assert lp.compare(1.7326, 1.7326) == {"status": "match", "gap_pp": 0.0}
    close = lp.compare(1.7326, 1.73265)  # the service rounds to 4 decimals
    assert close["status"] == "match" and close["gap_pp"] == pytest.approx(5e-5)


def test_a_difference_of_a_thousandth_of_a_point_or_more_is_flagged():
    assert lp.compare(1.7326, 1.7336)["status"] == "differs"  # exactly 0.001 is not "within"
    assert lp.compare(1.7326, 1.7335)["status"] == "match"  # 0.0009 is
    far = lp.compare(2.0, 1.5)
    assert far["status"] == "differs" and far["gap_pp"] == pytest.approx(0.5)


def test_with_no_stored_forecast_there_is_nothing_to_compare():
    assert lp.compare(1.7, None) == {"status": "no_stored", "gap_pp": None}


# ------------------------------------------------------------------ data versions


def test_data_versions_are_the_same_different_or_unknown():
    assert lp.data_versions("28b9a7bd3234", "28b9a7bd3234") == "same"
    assert lp.data_versions("28b9a7bd3234", "aaaaaaaaaaaa") == "different"
    for local, live in [(None, "x"), ("x", None), ("", "x"), ("unknown", "x"), ("x", "mixed")]:
        assert lp.data_versions(local, live) == "unknown", (local, live)


def test_the_local_version_is_the_one_every_stored_forecast_carries():
    one = pd.DataFrame({"data_version": ["28b9a7bd3234"] * 3})
    assert lp.local_data_version(one) == "28b9a7bd3234"
    assert lp.local_data_version(pd.DataFrame({"data_version": ["a", "b"]})) == "mixed"
    assert lp.local_data_version(pd.DataFrame({"data_version": [None]})) == "unknown"
    assert lp.local_data_version(pd.DataFrame({"x": [1]})) == "unknown"


# ------------------------------------------------------------------ the wording


def test_the_verdict_says_same_when_equal_and_warns_when_not():
    ok = lp.verdict(lp.compare(1.7326, 1.7326), "same")
    assert ok[0] == "ok" and ok[1].startswith("Same as the forecast above")
    unknown = lp.verdict(lp.compare(1.7326, 1.7326), "unknown")
    assert unknown[0] == "ok"
    skewed = lp.verdict(lp.compare(1.7326, 1.7326), "different")
    assert skewed[0] == "warn" and "different data versions" in skewed[1]
    differs = lp.verdict(lp.compare(2.0, 1.5), "same")
    assert differs[0] == "warn" and "0.5000 points" in differs[1]
    explained = lp.verdict(lp.compare(2.0, 1.5), "different")
    assert "would explain it" in explained[1]
    none = lp.verdict(lp.compare(1.7, None), "same")
    assert none[0] == "warn" and "no stored forecast" in none[1]


def test_an_unavailable_service_leaves_the_page_untouched_and_says_so():
    text = lp.unavailable("The live model service did not answer.")
    assert text == "The live model service did not answer. The forecast shown above is unchanged."


def test_the_result_rows_name_the_forecast_the_change_the_visits_and_the_model():
    live = {
        "target_month": "Oct 2025", "forecast_pct": 1.7326, "last_month_pct": 2.6471,
        "change_pp": -0.9145, "origin_visits": 169, "small": False, "model": "logistic",
        "model_version": "ec08de463b", "known_bias_pct": 10.92,
    }  # fmt: skip
    rows = dict(lp.result_rows(live, "3 months"))
    assert rows["Live forecast, 3 months (Oct 2025)"] == "1.73%"
    assert rows["Last month's share"] == "2.65%"
    assert rows["Change"] == "-0.91 points"
    assert rows["Visits last month"] == "169"
    assert rows["Model"] == "logistic (ec08de463b)"
    assert rows["Ran high in testing by"] == "10.9%"
    small = dict(lp.result_rows({**live, "small": True, "known_bias_pct": None}, "1 month"))
    assert (
        small["Visits last month"] == "169 (small, noisy)" and "Ran high in testing by" not in small
    )


def test_the_caveats_and_the_off_message_are_in_plain_words_without_a_secret():
    for text in (lp.LEAD, lp.BIAS_NOTE, lp.SWITCHED_OFF):
        assert "http" not in text and "key" not in text.lower()
    assert "optimistic" in lp.BIAS_NOTE and "same as last month" in lp.BIAS_NOTE
