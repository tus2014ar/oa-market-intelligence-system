"""The site itself (app/streamlit_app.py and its pages in app/views/), run headlessly with
Streamlit's test runner on the committed warehouse: every page runs, the one shared filter bar
behaves, and the numbers and caveats each page must show are there.

Each page is a separate run (the site is a set of pages with a navigation, not tabs on one page),
so a test asks for the page it checks. Needs the site's packages (app/requirements.txt); the CI job
`site` installs them and these tests are skipped where Streamlit is not installed.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit")
import streamlit as st  # noqa: E402
from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from oa_market_intelligence.serving import data, overview  # noqa: E402

APP_DIR = Path(__file__).resolve().parent.parent / "app"
APP = str(APP_DIR / "streamlit_app.py")
SCRIPTS = {title: script for title, script, *_ in overview.PAGES}
KEYS = ("ANTHROPIC_API_KEY", "ACCESS_CODE", "DAILY_TOKEN_BUDGET")
pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")


def run_page(title, **state):
    """A fresh run of one page, with any session values set first."""
    at = AppTest.from_file(APP, default_timeout=120)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    if title != "Overview":  # switching before the very first run is unreliable: run, then switch
        at.switch_page(SCRIPTS[title]).run()
    return at


def flt(**chosen):
    """The shared filters as the site stores them, with a few changed."""
    return {"specialty": None, "age": None, "gender": None, "period": "All", **chosen}


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    """Every test starts with no API key configured (the question box tests set their own)."""
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def page():
    """Each page's default run, made once and kept for the module."""
    with pytest.MonkeyPatch.context() as env:
        for name in KEYS:
            env.delenv(name, raising=False)
        cache = {}

        def get(title):
            if title not in cache:
                cache[title] = run_page(title)
            return cache[title]

        yield get


def _cards(at):
    """The navy cards on the page, in order."""
    return [m.value for m in at.markdown if 'class="oa-kpi"' in m.value]


def _card(at, label):
    return next(c for c in _cards(at) if f'<div class="l">{label}</div>' in c)


def _text(at):
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])


# ------------------------------------------------------------------ the pages and the navigation


def test_the_navigation_is_eleven_pages_each_a_real_file_with_its_own_address():
    assert [title for title, *_ in overview.PAGES] == list(overview.TAB_NAMES)
    assert all((APP_DIR / script).is_file() for script in SCRIPTS.values())
    addresses = [slug for *_, slug in overview.PAGES]
    assert len(set(addresses)) == 11 and all(a == a.lower() and " " not in a for a in addresses)
    assert all(icon.startswith(":material/") for _, _, icon, _ in overview.PAGES)


@pytest.mark.parametrize("title", overview.TAB_NAMES)
def test_every_page_runs_without_an_error_and_has_no_free_text_box(page, title):
    at = page(title)
    assert not at.exception
    assert not at.text_input and not at.text_area  # no free-text box in this phase


def test_every_page_but_the_overview_starts_with_a_note_on_how_old_the_data_are(page):
    for title in overview.TAB_NAMES[1:]:
        chips = [m.value for m in page(title).markdown if "-badge[Data to " in m.value]
        assert len(chips) == 1, title
        assert chips[0].startswith(":orange-badge[Data to Jul 2025")
        assert "454 days old, no newer extract" in chips[0]
    overview_page = page("Overview")
    assert not [m for m in overview_page.markdown if "-badge[Data to " in m.value]
    banners = [w.value for w in overview_page.warning if "No newer extract has arrived" in w.value]
    assert len(banners) == 1  # the long warning is on the Overview only


# ------------------------------------------------------------------ the shared filter bar


FILTERED = ["Market trend", "Segments", "Segment detail", "Forecasts"]


@pytest.mark.parametrize("title", FILTERED)
def test_the_filter_bar_is_on_the_three_pages_that_use_it(page, title):
    at = page(title)
    assert {"Specialty", "Age band", "Gender"} <= {s.label for s in at.selectbox}
    assert any(b.proto.label == "Period" for b in at.get("button_group"))
    assert any(b.label == "Reset" for b in at.button)
    assert any("This page follows the" in c.value for c in at.caption)


@pytest.mark.parametrize("title", [t for t in overview.TAB_NAMES if t not in FILTERED])
def test_the_other_pages_have_no_filter_bar(page, title):
    at = page(title)
    assert not {"Specialty", "Age band", "Gender"} & {s.label for s in at.selectbox}
    assert not any(b.label == "Reset" for b in at.button)


def test_each_page_says_which_filters_it_follows_and_the_forecasts_ignore_the_period(page):
    trend_note = next(
        c.value for c in page("Market trend").caption if "This page follows" in c.value
    )
    assert trend_note == "This page follows the specialty, age band, gender and period filters."
    forecast_at = page("Forecasts")
    note = next(c.value for c in forecast_at.caption if "This page follows" in c.value)
    assert (
        "specialty, age band and gender filters" in note and "period filter does not apply" in note
    )
    period = next(b for b in forecast_at.get("button_group") if b.proto.label == "Period")
    assert period.proto.disabled


def test_a_filter_chosen_on_one_page_follows_you_to_the_others_and_back(monkeypatch):
    at = run_page("Market trend", flt=flt(specialty="RHEUMATOLOGY"))
    assert next(s for s in at.selectbox if s.label == "Specialty").value == "RHEUMATOLOGY"
    for title in ("Segments", "Segment detail", "Forecasts"):
        at.switch_page(SCRIPTS[title]).run()
        assert next(s for s in at.selectbox if s.label == "Specialty").value == "RHEUMATOLOGY", (
            title
        )
    at.switch_page(SCRIPTS["Overview"]).run()  # a page with no bar: the widget is not drawn
    at.switch_page(SCRIPTS["Market trend"]).run()
    assert next(s for s in at.selectbox if s.label == "Specialty").value == "RHEUMATOLOGY"


def test_the_reset_button_puts_every_filter_back(monkeypatch):
    at = run_page("Market trend", flt=flt(specialty="RHEUMATOLOGY", age="65 TO 74", period="1Y"))
    next(b for b in at.button if b.label == "Reset").click().run()
    assert not at.exception
    assert {s.label: s.value for s in at.selectbox}["Specialty"] == "All"
    assert {s.label: s.value for s in at.selectbox}["Age band"] == "All"
    assert at.session_state["flt"] == flt()


# ------------------------------------------------------------------ the overview


def test_the_headline_numbers_are_the_warehouses(page):
    overview_page, status = page("Overview"), page("Data status")
    assert 'class="v">1.88%' in _card(overview_page, "Share in Jul 2025")
    assert 'class="v">3.38%' in _card(overview_page, "Peak (Dec 2021)")
    assert 'class="v">-44%' in _card(overview_page, "Change since the peak")
    assert 'class="v">72<' in _card(status, "Months of data")
    assert 'class="v">Jul 2025<' in _card(status, "Latest month")
    assert any(f'class="v">{w}<' in _card(status, "Data freshness") for w in ("Stale", "Fresh"))
    assert 'class="v">Ok<' in _card(status, "Last publish run")


def test_the_overview_caveats_are_on_the_page(page):
    at = page("Overview")
    text = " ".join([w.value for w in at.warning] + [m.value for m in at.markdown])
    assert "No newer extract has arrived" in text
    assert "run high" in text and "5.5% at 1 month and 10.9% at 3 months" in text
    assert "Payer, regional and prescription-volume data are not part of this analysis" in text


def test_the_overview_has_cards_a_forecast_chart_status_cards_and_two_downloads(page):
    at = page("Overview")
    assert at.get("vega_lite_chart")  # the forecast chart under the cards
    text = " ".join(m.value for m in at.markdown)
    for words in ("Rely on it as a baseline", "Use with care", "A supporting flag only"):
        assert words in text
    assert [d.proto.label for d in at.get("download_button")] == [
        "Download the monthly share (CSV)",
        "Download the forecast (CSV)",
    ]
    assert any("Use it for:" in m.value and "last month" in m.value for m in at.markdown)


def test_the_overview_has_small_lines_on_two_cards_a_guide_and_the_unverified_decline_note(page):
    text = " ".join(m.value for m in page("Overview").markdown)
    assert text.count('class="oa-spark"') == 2  # the share and the change since the peak
    assert text.count('class="oa-guide"') == 10 and "What each page answers" in text
    assert "the size of the visits decline is unverified: see the Context page" in text
    assert "Company sales kept rising while these visits fell" in text


def test_the_data_status_page_has_its_run_cards_versions_stages_and_extracts(page):
    at = page("Data status")
    text = " ".join(m.value for m in at.markdown)
    assert "Versions of this run" in text and "Earlier forecasts" in text
    assert "Stages of the last run" in text and "Input extracts" in text
    assert any("warehouse" in set(d.value["stage"]) for d in at.dataframe if "stage" in d.value)
    assert any("SHA-256" in c.value for c in at.caption)


def test_each_page_with_a_download_offers_exactly_its_own(page):
    expected = {
        "Market trend": ["Download this series (CSV)"],
        "Segments": ["Download this table (CSV)"],
        "Forecasts": ["Download these forecasts (CSV)"],
        "Findings": ["Download the findings (CSV)"],
        "Context": ["Download sales and visits (CSV)"],
    }
    for title, labels in expected.items():
        assert [d.proto.label for d in page(title).get("download_button")] == labels, title


# ------------------------------------------------------------------ the market trend page


def test_the_market_trend_page_opens_on_all_visits_with_four_cards_and_its_controls(page):
    at = page("Market trend")
    assert next(s for s in at.selectbox if s.label == "Specialty").value == "All"
    assert [t.label for t in at.toggle] == ["Trend line", "Markers"]
    cards = _cards(at)
    assert len(cards) == 4
    assert "Share in Jul 2025" in cards[0] and "1.88%" in cards[0] and "-0.10 pp" in cards[0]
    assert "3.38%" in cards[1] and "Dec 2021" in cards[1] and "44% below" in cards[1]
    assert "135,119" in cards[2] and "5,434,092" in cards[2]
    assert "Trend per year" in cards[3] and "a guide, not a forecast" in cards[3]
    text = " ".join(m.value for m in at.markdown)
    assert "Where the share stands" in text and "Next month (Aug 2025)" in text
    assert "Where it is moving, by specialty" in text and "The three classes" in text
    assert [text.count(f"**By {n}**") for n in ("specialty", "age band", "gender")] == [1, 1, 1]
    assert any("below its peak of 3.38% (Dec 2021)" in m.value for m in at.markdown)


def test_the_market_trend_page_keeps_its_caveats(page):
    text = _text(page("Market trend"))
    assert "not company sales" in text and "nothing here says why the share moved" in text
    assert "a guide, not a forecast" in text and "data-capture dip" in text
    assert 'The method is "same as last month": no model beat it' in text
    assert "the data cannot say why visits moved" in text


def test_choosing_a_group_filters_every_number_and_flags_a_small_one():
    at = run_page("Market trend", flt=flt(specialty="RHEUMATOLOGY"))
    assert not at.exception
    assert "Share in Jul 2025" in _cards(at)[0] and "1.88%" not in _cards(at)[0]
    text = " ".join(m.value for m in at.markdown)
    assert "Where it is moving, by age band" in text and "How this group compares" in text
    assert "The three classes" not in text  # the class split exists for the whole market only
    assert "Forecasts for a group are on the Forecasts page" in " ".join(
        c.value for c in at.caption
    )
    small = run_page(
        "Market trend",
        flt=flt(specialty="RHEUMATOLOGY", age="65 TO 74", gender="MALE"),  # ~100 visits a month
    )
    assert not small.exception
    assert any("This group is small" in w.value for w in small.warning)
    assert "Every filter is set" in " ".join(c.value for c in small.caption)


def test_a_shorter_period_changes_the_visit_total_but_not_the_peak():
    at = run_page("Market trend", flt=flt(period="1Y"))
    assert not at.exception
    cards = _cards(at)
    assert "135,119" not in cards[2] and "Last year" in cards[2]
    assert "3.38%" in cards[1] and "Dec 2021" in cards[1]
    quarters = run_page("Market trend", trend_grain="Quarterly")
    assert not quarters.exception and quarters.get("vega_lite_chart")


# ------------------------------------------------------------------ the segments page


def test_the_segments_page_opens_on_specialties_with_four_cards_and_the_grid(page):
    at = page("Segments")
    cards = _cards(at)
    assert len(cards) == 4
    assert "Highest share" in cards[0] and "Physical Medicine" in cards[0]
    assert "Largest source of visits" in cards[1] and "Orthopedic Surgery" in cards[1]
    assert "Biggest rise" in cards[2] and "Family Practice" in cards[2]
    assert "Biggest fall" in cards[3] and "Sports Medicine" in cards[3]
    text = " ".join(m.value for m in at.markdown)
    assert "How concentrated are the visits?" in text and "Where the share is highest" in text
    assert "Specialty over time" in text and "Three largest together" in text
    assert any(
        "among 14 specialty groups with at least 5,000 visits" in m.value for m in at.markdown
    )


def test_the_segments_page_keeps_its_caveats(page):
    text = _text(page("Segments"))
    assert "one prescription moves a small group's share a lot" in text
    assert "not company sales" in text and "Small groups are noisy" in text
    assert "too few to show a rate" in text


def test_grouping_by_age_or_gender_and_changing_the_period_all_run():
    for state in (
        {"seg_by": "age_band"},
        {"seg_by": "gender"},
        {"flt": flt(period="1Y")},
        {"seg_show": "visits"},
        {"seg_show": "change", "seg_by": "age_band"},
    ):
        assert not run_page("Segments", **state).exception, state
    age = run_page("Segments", seg_by="age_band")
    assert any("Age band over time" in m.value for m in age.markdown)
    assert any("65 to 74" in m.value for m in age.markdown)


def test_the_shared_filters_narrow_the_segments_and_a_thin_selection_does_not_break_the_page():
    narrowed = run_page("Segments", seg_by="age_band", flt=flt(specialty="RHEUMATOLOGY"))
    assert not narrowed.exception
    wide = run_page("Segments", seg_by="age_band")
    narrow_cards, wide_cards = _cards(narrowed), _cards(wide)
    assert narrow_cards[1] != wide_cards[1]  # the largest source is now within rheumatology
    thin = run_page(
        "Segments", flt=flt(specialty="RHEUMATOLOGY", age="40 TO 59", gender="MALE", period="1Y")
    )
    assert not thin.exception  # almost no visits: every card says n/a or has a figure, no error


# ------------------------------------------------------------------ the forecasts page


def test_the_forecasts_page_opens_with_the_three_national_forecasts_and_the_range_held(page):
    at = page("Forecasts")
    cards = _cards(at)
    assert len(cards) == 4
    assert "In 1 month (Aug 2025)" in cards[0] and "1.88%" in cards[0]
    assert "1.58% to 2.15% (90%)" in cards[0] and "same as last month" in cards[0]
    assert "In 3 months (Oct 2025)" in cards[1] and "In 6 months (Jan 2026)" in cards[2]
    assert "Range held at 1 month" in cards[3] and "96%" in cards[3]
    text = " ".join(m.value for m in at.markdown)
    assert "How far to trust it, 1 month ahead" in text and "Segment forecasts" in text
    assert "Decline alert, 1 month ahead" in text
    assert "Flagged as likely to fall 30% or more" in text
    assert "ran 5.5% too high in testing: read it as optimistic" in text


def test_the_forecasts_page_keeps_its_caveats(page):
    text = _text(page("Forecasts"))
    assert "no model beat that simple rule at any horizon" in text
    assert "have already passed: no newer extract has arrived" in text
    assert "not as a prediction for one segment" in text
    assert "never as a reason to act on its own" in text and "a 1-month flag only" in text
    assert "Segment forecasts run high: 5.5% at 1 month and 10.9% at 3 months in testing" in text


def test_each_horizon_and_a_chosen_specialty_changes_the_forecasts_page():
    three = run_page("Forecasts", fc_horizon=3)
    assert not three.exception
    text3 = " ".join(m.value for m in three.markdown)
    assert "ran 10.9% too high in testing" in text3
    assert "How far to trust it, 3 months ahead" in text3 and "Range held at 3 months" in text3
    six = run_page("Forecasts", fc_horizon=6)
    assert not six.exception
    text6 = " ".join(m.value for m in six.markdown)
    assert "the segment forecast is the simple rule" in text6
    assert "too high in testing: read" not in text6
    one = run_page("Forecasts", flt=flt(specialty="RHEUMATOLOGY"))
    assert not one.exception
    assert "Segments in this selection" in " ".join(m.value for m in one.markdown)


def test_age_and_gender_filters_narrow_the_forecast_table_and_an_empty_selection_is_handled():
    at = run_page("Forecasts", flt=flt(specialty="RHEUMATOLOGY", age="65 TO 74"))
    assert not at.exception
    tables = [d.value for d in at.dataframe if "Forecast, 1 mo (%)" in d.value.columns]
    assert tables and set(tables[0]["Age band"]) == {"65 to 74"}
    nothing = run_page(
        "Forecasts", flt=flt(specialty="OTHER SURGERY", age="00 TO 02", gender="MALE")
    )
    assert not nothing.exception


# ------------------------------------------------------------------ the model results page


def test_the_model_results_page_opens_with_four_headlines_and_marks_round_2_provisional(page):
    at = page("Model results")
    cards = _cards(at)
    assert len(cards) == 4
    assert "No model wins" in cards[0] and "+0.4% / +0.2%" in cards[1]
    assert "1 of 3" in cards[2] and "3 of 7" in cards[3] and "none adopted" in cards[3]
    text = " ".join(m.value for m in at.markdown)
    assert "Round 2: improvements tried after round 1" in text
    assert "Nothing from round 2 is used by the site yet" in text
    assert text.count("Passes (provisional)") == 3 and text.count("No pass") == 4
    for name in ("Bias correction factor", "Rolling 36-month window", "Lasting-decline top 10"):
        assert name in text
    assert "Ranking winners and losers" in text and "+0.38" in text  # the mean-reversion check
    assert "What the segment model relies on" in text
    assert "own recent share" in text  # the apostrophe is escaped in the HTML


def test_the_model_results_page_keeps_its_caveats_and_the_scoreboard(page):
    at = page("Model results")
    text = _text(at)
    assert "Do the segment forecasts run high?" in text and "+18.2%" in text
    assert "one-line rule" in text and "reliable only at 1 month" in text
    assert "designed after seeing round 1" in text
    assert "not influence which model was chosen" in text
    boards = [d.value for d in at.dataframe if "In plain words" in d.value.columns]
    assert len(boards) == 1 and len(boards[0]) == 9
    assert set(boards[0]["Task"]) == {"National share", "Segment share", "Decline alert"}


def test_the_scoreboard_can_be_cut_to_one_task_and_the_explanation_to_three_months():
    at = run_page("Model results", mr_task="Segment share", mr_shap_h=3)
    assert not at.exception
    boards = [d.value for d in at.dataframe if "In plain words" in d.value.columns]
    assert len(boards) == 1 and set(boards[0]["Task"]) == {"Segment share"} and len(boards[0]) == 3
    assert any("Physician Assistant, ages 65 to 74, female" in c.value for c in at.caption)


# ------------------------------------------------------------------ the findings page


def test_the_findings_page_lists_all_seventeen_in_five_themes_with_four_to_read_first(page):
    text = " ".join(m.value for m in page("Findings").markdown)
    assert text.count('class="oa-find"') == 17
    assert "Read these first" in text and "Finding 16" in text
    for title in (
        "The gap between sales and visits",
        "Checked against outside data",
        "Things that might explain it",
        "What limits the data",
        "What the models showed",
    ):
        assert title in text
    assert "Company sales kept rising while IQVIA&#x27;s Zilretta visits fell" in text
    assert "must not be quoted as a market fact" in text
    assert "not adopted and is shown on the Model results page" in text


def test_a_theme_can_be_chosen_on_the_findings_page():
    at = run_page("Findings", find_theme="The models")
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert text.count('class="oa-find"') == 4
    assert any("4 of 17 findings" in c.value for c in at.caption)
    assert "Segment forecasts gain a little at 1 and 3 months and run too high" in text


# ------------------------------------------------------------------ the context page


def test_the_context_page_sets_sales_beside_visits_and_keeps_the_decline_caveat(page):
    at = page("Context")
    sales = _card(at, "Net sales, 2024 vs 2021")
    visits = _card(at, "IQVIA visits, 2024 vs 2021")
    assert "+15%" in sales and "6.3%" in sales
    assert "-41%" in visits and "-35.9%" in visits
    adoption = _card(at, "Medicare providers billing it, 2024")
    assert "1.47%" in adoption and "Peak 1.78% in 2021" in adoption
    assert "None" in _card(at, "Tested explanations for the gap")
    text = " ".join(m.value for m in at.markdown)
    assert "Company sales against IQVIA visits" in text
    assert "must not be quoted as a market fact" in text
    assert "Ask IQVIA whether panel coverage changed" in text
    notes = " ".join(c.value for c in at.caption)
    assert "different sources and are set side by side, not combined" in notes


def test_the_context_page_reports_what_was_tested_without_naming_a_cause(page):
    text = _text(page("Context"))
    for words in (
        "What was tested to explain the gap",
        "Is it the population (age, payer mix)?",
        "Not supported",
        "Supported on direction only",
        "Concentrated",
        "none of the four accounts for the size of the gap",
        "None of this identifies a cause",
        "company-side causes",
    ):
        assert words in text


def test_the_context_page_has_the_state_map_the_unverified_events_and_the_medicare_caveat(page):
    text = _text(page("Context"))
    assert "Medicare adoption by state" in text and "Furthest below the national rate" in text
    assert "It is not IQVIA data" in text and "Medicare Advantage patients are not in it" in text
    assert "National, 2024" in text and "Order kept, 2022 to 2024" in text
    assert "Events, oldest first" in text and text.count('class="oa-tl"') == 14
    assert text.count("not verified against its source") == 2
    assert "nothing here says an event changed the share" in text


def test_another_year_can_be_chosen_for_the_state_map():
    at = run_page("Context", ctx_year=2021)
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "National, 2021" in text and "1.78%" in text


# ------------------------------------------------------------------ the market map


def test_the_market_map_page_opens_on_zilretta_with_its_facts_and_the_event_table():
    at = run_page("Market map")
    assert not at.exception
    choice = next(s for s in at.selectbox if s.label == "Look closer at one thing in this map")
    assert choice.value == "product:ZILRETTA"
    text = _text(at)
    assert "Competitor groups are the project's own grouping" in text
    assert "Events are context only" in text
    assert any(s.value == "ZILRETTA" for s in at.subheader)
    assert any("Showing 10 of 10 connections" in c.value for c in at.caption)
    tables = [d.value for d in at.dataframe]
    assert any(len(frame) == 14 and "verified" in frame.columns for frame in tables)


def test_the_view_can_be_switched_and_every_view_runs():
    at = run_page("Market map")
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


def test_looking_closer_at_another_thing_shows_its_own_connections():
    at = run_page("Market map")
    choice = next(s for s in at.selectbox if s.label == "Look closer at one thing in this map")
    choice.set_value("group:zilretta").run()
    assert any(s.value == "Zilretta" for s in at.subheader)
    assert any("connections. Sources:" in c.value for c in at.caption)


# ------------------------------------------------------------------ the question box


def _box(monkeypatch, **env):
    """The AI Question Box page with the given secrets in the environment and fresh limits."""
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    st.cache_resource.clear()
    return run_page("AI Question Box")


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
            "sources": ["Overview page (market series, forecasts, last run)"],
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
    assert not at.exception and not at.text_area
    [box] = at.chat_input  # the chat box is there, visibly switched off
    assert box.proto.disabled and "switched on" in box.proto.placeholder
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
    assert any(c.value.startswith("Sources: Overview page") for c in at.caption)
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
    text = " ".join(
        [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info]
    )
    assert "sk-test-secret" not in text and "code-xyz" not in text


# ---------------------- links, deep links and the Segment detail page


def _hrefs(at):
    """Every address in the page's HTML, in order."""
    import html
    import re

    found = re.findall(r'href="([^"]+)"', " ".join(m.value for m in at.markdown))
    return [html.unescape(address) for address in found]  # the page shows &amp; for &


def test_the_overviews_cards_and_guide_each_link_to_the_page_behind_them(page):
    at = page("Overview")
    cards = _cards(at)
    assert [("href=" in c) for c in cards] == [True, True, True, True]
    assert cards[0].count('href="market-trend?from=overview"') == 1
    assert 'href="forecasts?from=overview"' in cards[3]
    hrefs = _hrefs(at)
    for slug in ("market-trend", "segments", "segment-detail", "forecasts", "model-results"):
        assert any(h.startswith(slug) for h in hrefs), slug
    assert any(h.startswith("forecasts?from=overview#decline-alert-1-month-ahead") for h in hrefs)
    assert any(h.startswith("forecasts?from=overview#segment-forecasts") for h in hrefs)


def test_every_link_on_every_page_points_at_a_real_page(page):
    slugs = {slug for *_, slug in overview.PAGES}
    for title in overview.TAB_NAMES:
        for address in _hrefs(page(title)):
            if address.startswith("#"):
                continue
            path = address.split("?")[0].split("#")[0]
            assert path in slugs or path == "./", (title, address)


def test_every_finding_section_a_link_names_is_a_heading_on_its_page(page):
    from oa_market_intelligence.serving import links

    for finding_id, target in links.FINDING_LINKS.items():
        if target is None or target[1] is None:
            continue
        title, section = target
        headings = [m.value for m in page(title).markdown]
        assert f"##### {section}" in headings, (finding_id, title, section)


def test_the_findings_page_links_fifteen_findings_to_their_evidence_and_two_to_none(page):
    text = " ".join(m.value for m in page("Findings").markdown)
    assert text.count('class="oa-find-see"') == 15
    assert "See it on Forecasts →" in text and "See it on Context →" in text
    assert "See it on Model results →" in text and "See it on Data status →" in text


def test_the_segments_cards_link_to_the_group_they_name(page):
    cards = _cards(page("Segments"))
    import html

    assert (
        "segment-detail?specialty=PHYSICAL%20MEDICINE%20%26%20REHAB&from=segments"
        in html.unescape(cards[0])
    )
    assert "segment-detail?specialty=ORTHOPEDIC%20SURGERY&from=segments" in html.unescape(cards[1])
    by_age = _cards(run_page("Segments", seg_by="age_band"))
    assert all("segment-detail?age=" in c for c in by_age if "href=" in c)


def test_a_card_keeps_the_filters_when_it_links_to_a_group():
    at = run_page("Segments", seg_by="age_band", flt=flt(specialty="RHEUMATOLOGY", period="3Y"))
    cards = [c for c in _cards(at) if "segment-detail" in c]
    assert cards and all("specialty=RHEUMATOLOGY" in c and "period=3Y" in c for c in cards)


def test_the_market_trend_rows_bars_and_card_link_to_the_segment_detail(page):
    at = page("Market trend")
    hrefs = [h for h in _hrefs(at) if h.startswith("segment-detail")]
    assert len([h for h in hrefs if "from=market-trend" in h]) >= 12  # 5 rows, 3 bars of 6, card
    assert any("specialty=" in h for h in hrefs) and any("age=" in h for h in hrefs)
    assert any("gender=" in h for h in hrefs)
    filtered = run_page("Market trend", flt=flt(specialty="RHEUMATOLOGY"))
    card = _cards(filtered)[0]
    import html

    assert "segment-detail?specialty=RHEUMATOLOGY&from=market-trend" in html.unescape(card)


def test_the_model_results_headlines_and_the_context_cards_land_on_their_subject(page):
    model = _cards(page("Model results"))
    assert 'href="forecasts?from=model-results"' in model[0]
    assert "forecasts?from=model-results#segment-forecasts" in model[1]
    assert "forecasts?from=model-results#decline-alert-1-month-ahead" in model[2]
    assert 'href="#round-2-improvements-tried-after-round-1"' in model[3]
    context_cards = _cards(page("Context"))
    assert 'href="#company-sales-against-iqvia-visits"' in context_cards[0]
    assert 'href="#company-sales-against-iqvia-visits"' in context_cards[1]
    assert 'href="#medicare-adoption-by-state"' in context_cards[2]
    assert 'href="#what-was-tested-to-explain-the-gap"' in context_cards[3]


def test_the_forecasts_page_points_to_how_the_models_were_tested(page):
    hrefs = _hrefs(page("Forecasts"))
    assert hrefs.count("model-results?from=forecasts#how-the-models-scored") == 2


def test_the_segment_detail_page_with_nothing_chosen_offers_the_biggest_groups_to_click(page):
    at = page("Segment detail")
    text = " ".join(m.value for m in at.markdown)
    assert "Pick a group below" in text
    assert text.count("By specialty") == 1 and text.count("By age band") == 1
    hrefs = [h for h in _hrefs(at) if h.startswith("segment-detail?")]
    assert len(hrefs) >= 15 and all("from=segment-detail" in h for h in hrefs)
    assert not _cards(at)  # nothing to summarise yet


def test_the_segment_detail_page_for_a_specialty_has_its_numbers_its_neighbours_and_its_way_on():
    at = run_page("Segment detail", flt=flt(specialty="ORTHOPEDIC SURGERY"))
    assert not at.exception
    cards = _cards(at)
    assert len(cards) == 4
    assert "Share in Jul 2025" in cards[0] and "1.44%" in cards[0] and "-0.44 pp" in cards[0]
    assert "The whole market: 1.88%" in cards[0]
    assert "Zilretta visits, all months" in cards[1]
    assert "-0.53 pp" in cards[2] and " of 22" in cards[3]
    assert "Rank among specialties" in cards[3]
    text = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert "oa-crumb" in text and "<b>Orthopedic Surgery</b>" in text
    assert "This group against all visits" in text and "Forecast for this group" in text
    assert "Next to its neighbours, by specialty" in text and "▶ Orthopedic Surgery" in text
    assert "Its trend on Market trend →" in text and "Its forecast on Forecasts →" in text
    assert "0.44 points below the whole market&#x27;s 1.88%" in text or (
        "0.44 points below the whole market's 1.88%" in text
    )
    assert at.get("vega_lite_chart")


def test_a_narrow_group_compares_across_its_neighbours_in_the_most_specific_part():
    at = run_page(
        "Segment detail", flt=flt(specialty="ORTHOPEDIC SURGERY", age="65 TO 74", gender="FEMALE")
    )
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Next to its neighbours, by gender" in text
    assert "Ages 65 to 74" in text and "<b>Female</b>" in text
    assert "Orthopedic Surgery</a>" in text  # a crumb back up to the specialty


def test_a_group_with_no_visits_or_no_forecast_opens_without_an_error():
    at = run_page("Segment detail", flt=flt(specialty="ONCOLOGY", age="00 TO 02", gender="MALE"))
    assert not at.exception


def test_a_link_from_another_page_opens_the_page_already_filtered_with_a_way_back():
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["specialty"] = "RHEUMATOLOGY"
    at.query_params["period"] = "3Y"
    at.query_params["from"] = "segments"
    at.run()
    assert not at.exception
    assert at.session_state["flt"] == flt(specialty="RHEUMATOLOGY", period="3Y")
    back = at.get("page_link")
    assert [b.proto.label for b in back] == ["Back to Segments"]
    at.switch_page(SCRIPTS["Segments"]).run()
    assert next(s for s in at.selectbox if s.label == "Specialty").value == "RHEUMATOLOGY"
    assert dict(at.query_params) == {"specialty": "RHEUMATOLOGY", "period": "3Y"}


def test_an_address_with_a_value_the_data_does_not_have_opens_unfiltered():
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["specialty"] = "NOT A SPECIALTY"
    at.query_params["period"] = "99Y"
    at.query_params["from"] = "nowhere"
    at.run()
    assert not at.exception
    assert at.session_state["flt"] == flt()
    assert not at.get("page_link")  # no valid source: no back link


# ---------------------- clicks inside charts and tables


def _click(group_field_values):
    """What Streamlit hands the page after a click in a chart (the selection called pick)."""
    return {"selection": {"pick": [group_field_values]}}


def _landed(at, who):
    """The page is Segment detail for the group named, reached by a click."""
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert f"<b>{who}</b>" in text and "Share in Jul 2025" in text
    return text


def test_a_click_on_a_segments_bar_opens_that_group_with_the_filters_kept():
    at = run_page("Segments", flt=flt(period="3Y"), seg_bars_0=_click({"group": "RHEUMATOLOGY"}))
    _landed(at, "Rheumatology")
    assert dict(at.session_state["flt"]) == flt(specialty="RHEUMATOLOGY", period="3Y")
    assert at.session_state["from"] == ("Segments", "Segment detail")


def test_a_click_on_an_age_bar_opens_that_age_band():
    at = run_page("Segments", seg_by="age_band", seg_bars_0=_click({"group": "65 TO 74"}))
    _landed(at, "Ages 65 to 74")
    assert at.session_state["flt"]["age"] == "65 TO 74"
    assert at.session_state["flt"]["specialty"] is None


def test_a_click_on_a_grid_cell_opens_that_specialty_and_age_band():
    at = run_page(
        "Segments",
        seg_grid_0=_click({"row": "RHEUMATOLOGY", "column": "65 TO 74"}),
    )
    text = _landed(at, "Ages 65 to 74")
    assert "Rheumatology</a>" in text  # the path to the group names the specialty too
    assert at.session_state["flt"]["specialty"] == "RHEUMATOLOGY"
    assert at.session_state["flt"]["age"] == "65 TO 74"


def test_the_filter_bar_shows_the_group_a_click_opened():
    at = run_page("Segments", seg_bars_0=_click({"group": "RHEUMATOLOGY"}))
    assert at.session_state["w_specialty"] == "RHEUMATOLOGY"
    assert any(s.value == "RHEUMATOLOGY" for s in at.selectbox)


def test_an_odd_click_does_not_move_the_page():
    for odd in (
        {"selection": {}},
        {"selection": {"pick": []}},
        {"selection": {"pick": [{"x": 1}]}},
    ):
        at = run_page("Segments", seg_bars_0=odd)
        assert not at.exception and "from" not in at.session_state
        assert at.session_state["flt"]["specialty"] is None


def test_a_click_on_the_forecasts_specialty_bar_opens_that_specialty():
    at = run_page("Forecasts", fc_spec_0=_click({"specialty": "ORTHOPEDIC SURGERY"}))
    _landed(at, "Orthopedic Surgery")
    assert at.session_state["from"] == ("Forecasts", "Segment detail")


def test_a_click_on_a_row_of_the_forecast_or_alert_table_opens_that_segment():
    for table in ("fc_table_0", "fc_alerts_0"):
        at = run_page("Forecasts", **{table: {"selection": {"rows": [0], "columns": []}}})
        assert not at.exception, table
        assert at.session_state["from"] == ("Forecasts", "Segment detail"), table
        chosen = at.session_state["flt"]
        assert chosen["specialty"] and chosen["age"] and chosen["gender"], table
        assert "Share in Jul 2025" in " ".join(m.value for m in at.markdown), table


def test_the_page_a_click_leaves_offers_a_way_back():
    at = run_page("Segments", seg_bars_0=_click({"group": "RHEUMATOLOGY"}))
    assert [b.proto.label for b in at.get("page_link")] == ["Back to Segments"]


def test_a_click_on_a_state_tile_opens_that_states_panel_and_close_clears_it():
    at = run_page("Context", ctx_map_0=_click({"state_code": "TX"}))
    assert not at.exception and at.session_state["ctx_state"] == "TX"
    text = " ".join([m.value for m in at.markdown] + [t.value for t in at.text])
    assert "Texas" in text and "of the 51 states" not in text
    assert at.get("vega_lite_chart")
    shown = " ".join(c.value for c in at.caption)
    assert "Original Medicare billing, not IQVIA" in shown
    close = [b for b in at.button if b.key == "ctx_state_close"]
    assert close
    close[0].click().run()
    assert at.session_state["ctx_state"] is None
    assert not [b for b in at.button if b.key == "ctx_state_close"]


def test_a_state_code_the_data_does_not_have_shows_no_panel():
    at = run_page("Context", ctx_map_0=_click({"state_code": "ZZ"}))
    assert not at.exception
    assert not [b for b in at.button if b.key == "ctx_state_close"]


# ---------------------- the plain-words layer


def test_every_page_opens_with_its_plain_words_paragraph_and_ends_with_its_terms(page):
    import html

    from oa_market_intelligence.serving import plainwords

    for title in overview.TAB_NAMES:
        at = page(title)
        text = " ".join(m.value for m in at.markdown)
        assert (
            "In plain words" in text and html.escape(plainwords.PAGE_SUMMARY[title][:40]) in text
        ), title
        labels = [e.proto.label for e in at.expander]
        assert labels[-1] == "Words used on this page", title
        for term, _ in plainwords.terms_for(title):
            assert term in text, (title, term)  # the list is drawn, closed, in the page


def test_a_pages_paragraph_comes_before_its_filter_bar_and_its_terms_after_the_page(page):
    at = page("Segments")
    markdown = [m.value for m in at.markdown]
    where = [i for i, m in enumerate(markdown) if "In plain words" in m]
    terms = [i for i, m in enumerate(markdown) if 'class="oa-gloss"' in m]
    assert where and terms and where[0] < 5 and terms[0] > len(markdown) - 4


def test_the_charts_have_a_what_does_this_mean_button_where_a_chart_is_drawn(page):
    counts = {title: len(page(title).get("popover")) for title in overview.TAB_NAMES}
    assert counts == {
        "Overview": 1,
        "Market trend": 1,
        "Segments": 3,
        "Segment detail": 0,  # nothing chosen yet: no chart
        "Forecasts": 4,
        "Model results": 3,
        "Data status": 0,
        "Findings": 0,
        "Context": 2,
        "Market map": 1,
        "AI Question Box": 1,
    }


def test_the_button_text_is_the_chart_note_with_its_caveat(page):
    from oa_market_intelligence.serving import plainwords

    text = " ".join(m.value for m in page("Overview").markdown)
    note = plainwords.EXPLAIN["overview_fan"]
    assert f"**How to read it.** {note['read']}" in text
    assert f"**What it does not tell you.** {note['not']}" in text


def test_the_segment_detail_chart_and_the_state_panel_each_add_a_button():
    detail = run_page("Segment detail", flt=flt(specialty="ORTHOPEDIC SURGERY"))
    assert len(detail.get("popover")) == 1
    context_page = run_page("Context", ctx_state="TX")
    assert len(context_page.get("popover")) == 3


# ---------------------- dragging a range on a chart


def _drag(start, end):
    """What Streamlit hands the page after a range is dragged on a chart's time axis."""
    import pandas as pd

    ms = [pd.Timestamp(d).timestamp() * 1000 for d in (start, end)]
    return {"selection": {"brush": {"month": ms}}}


ALL_KEY = "trend_brush_0_None|None|None|All|Monthly|1|1"


def test_without_a_range_the_market_trend_cards_describe_the_period(page):
    at = page("Market trend")
    text = " ".join(c for c in _cards(at))
    assert "All months" in text and "Selected range" not in text
    assert not [b for b in at.button if b.key == "trend_clear"]
    assert not any("Showing" in m.value for m in at.markdown)


def test_a_range_dragged_on_the_trend_chart_moves_the_cards_to_its_months():
    at = run_page("Market trend", **{ALL_KEY: _drag("2022-03-01", "2024-07-01")})
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Showing Mar 2022 to Jul 2024 (29 months)" in text
    cards = _cards(at)
    assert "Share in Jul 2024" in cards[0] and "Selected range" in cards[2]
    assert "Mar 2022" in cards[1]  # the peak of the range, not of the whole series
    assert "through the range" in cards[3]
    assert [b for b in at.button if b.key == "trend_clear"]


def test_the_range_leaves_the_filters_and_the_period_alone():
    at = run_page("Market trend", **{ALL_KEY: _drag("2022-03-01", "2024-07-01")})
    assert dict(at.session_state["flt"]) == flt()


def test_a_range_follows_the_group_it_was_dragged_on():
    key = "trend_brush_0_RHEUMATOLOGY|None|None|All|Monthly|1|1"
    at = run_page(
        "Market trend",
        flt=flt(specialty="RHEUMATOLOGY"),
        **{key: _drag("2022-03-01", "2024-07-01")},
    )
    assert any("Showing Mar 2022 to Jul 2024" in m.value for m in at.markdown)
    other = run_page(
        "Market trend", flt=flt(specialty="OTHER"), **{key: _drag("2022-03-01", "2024-07-01")}
    )
    assert not any("Showing" in m.value for m in other.markdown)  # that range was not dragged here


def test_a_range_under_two_months_says_so_and_does_not_change_the_cards():
    at = run_page("Market trend", **{ALL_KEY: _drag("2024-01-05", "2024-01-20")})
    assert not at.exception
    assert any("under two months" in c.value for c in at.caption)
    assert "Selected range" not in " ".join(_cards(at))


def test_clearing_the_range_puts_the_period_back():
    at = run_page("Market trend", **{ALL_KEY: _drag("2022-03-01", "2024-07-01")})
    [clear] = [b for b in at.button if b.key == "trend_clear"]
    clear.click().run()
    assert not at.exception
    assert not any("Showing" in m.value for m in at.markdown)
    assert "Selected range" not in " ".join(_cards(at))
    assert not [b for b in at.button if b.key == "trend_clear"]


def test_changing_the_chart_view_starts_it_again_with_no_range():
    at = run_page("Market trend", **{ALL_KEY: _drag("2022-03-01", "2024-07-01")})
    assert any("Showing" in m.value for m in at.markdown)
    at.toggle(key="trend_line").set_value(False).run()
    assert not at.exception
    assert not any("Showing" in m.value for m in at.markdown)  # a new view, a new chart
    assert "Selected range" not in " ".join(_cards(at))


def test_a_range_dragged_on_the_segment_detail_chart_is_averaged_against_the_market():
    key = "detail_brush_0_ORTHOPEDIC SURGERY|None|None|All"
    at = run_page(
        "Segment detail",
        flt=flt(specialty="ORTHOPEDIC SURGERY"),
        **{key: _drag("2024-01-01", "2024-06-01")},
    )
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Jan 2024 to Jun 2024 (6 months)" in text and "Zilretta&#x27;s share averaged" in text
    plain = run_page("Segment detail", flt=flt(specialty="ORTHOPEDIC SURGERY"))
    assert "share averaged" not in " ".join(m.value for m in plain.markdown)


def test_the_groups_over_time_picker_and_the_shap_switch_still_work():
    at = run_page("Segments")
    picker = at.multiselect(key="seg_pick_specialty")
    first = picker.value[0]
    picker.set_value([first]).run()
    assert not at.exception and at.multiselect(key="seg_pick_specialty").value == [first]
    model = run_page("Model results")
    model.get("segmented_control")  # the switch is drawn inside the fragment
    assert not model.exception


# ---------------------- the AI Question Box page, brought up to the site's standard


def test_without_a_key_the_page_shows_what_the_box_would_look_up(monkeypatch):
    at = _box(monkeypatch)
    assert not at.exception and all(c.proto.disabled for c in at.chat_input)
    [pick] = [s for s in at.selectbox if s.key == "ask_preview"]
    assert (
        len(pick.options) == 8 and pick.value == "How has Zilretta's share changed since its peak?"
    )
    text = " ".join(m.value for m in at.markdown)
    assert any(e.proto.label.startswith("What it would look up") for e in at.expander)
    assert "The headline numbers, how old the data are" in text
    assert len(at.get("json")) == 1  # the one lookup's data, folded
    assert any("not switched on" in i.value for i in at.info)


def test_choosing_another_example_shows_its_own_lookups(monkeypatch):
    at = _box(monkeypatch)
    [pick] = [s for s in at.selectbox if s.key == "ask_preview"]
    pick.select("Which segments does the decline alert flag, and how reliable is it?").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "The forecasts and the decline-alert list" in text
    assert "How each forecast scored, and what to use it for" in text
    assert len(at.get("json")) == 2
    pick = [s for s in at.selectbox if s.key == "ask_preview"][0]
    pick.select("Show me the share trend as a chart.").run()
    assert "Draws one of four charts" in " ".join(m.value for m in at.markdown)


def test_the_page_explains_what_it_can_and_cannot_do_with_or_without_a_key(monkeypatch):
    for env in ({}, {"ANTHROPIC_API_KEY": "sk-test"}):
        at = _box(monkeypatch, **env)
        [how] = [e for e in at.expander if e.proto.label.startswith("How it works")]
        text = " ".join(m.value for m in at.markdown)
        assert "not yet been tried with a live model" in text
        assert "The eleven things it can look at" in text
        assert text.count("page)") >= 9  # each tool that reads says which page shows the same


def test_an_answers_sources_open_the_page_behind_them_and_the_chat_is_kept(monkeypatch):
    _stub_claude(
        monkeypatch,
        sources=[
            "Forecasts page (pred_batch, model_scores)",
            "Model results page (model_scores, modelling_posthoc.json)",
            "Forecasts page (pred_batch, model_scores)",
        ],
    )
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _say(at, "How reliable are the forecasts?")
    assert not at.exception
    labels = _labels(at)
    assert labels.count("Open Forecasts") == 1 and labels.count("Open Model results") == 1
    assert any(c.value.startswith("Sources: Forecasts page") for c in at.caption)
    next(b for b in at.button if b.label == "Open Forecasts").click().run()
    assert not at.exception
    assert at.session_state["from"] == ("AI Question Box", "Forecasts")
    assert len(at.session_state["chat"]) == 2  # the conversation survives the move
    assert [b.proto.label for b in at.get("page_link")] == ["Back to AI Question Box"]


def test_an_answer_with_no_page_behind_its_source_has_no_open_button(monkeypatch):
    _stub_claude(monkeypatch, sources=["Something with no page"])
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _say(at, "Anything?")
    assert not [label for label in _labels(at) if label.startswith("Open ")]
    assert any(c.value == "Sources: Something with no page" for c in at.caption)


def test_the_key_and_the_access_code_never_appear_in_the_new_page_text(monkeypatch):
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test-secret", ACCESS_CODE="code-xyz")
    text = " ".join(
        [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info]
    )
    assert "sk-test-secret" not in text and "code-xyz" not in text


# ---------------------- a chat box first, the example questions an optional extra


def test_the_box_is_a_chat_box_first_and_the_examples_are_a_folded_extra(monkeypatch):
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    [box] = at.chat_input
    assert not box.proto.disabled and "your own words" in box.proto.placeholder
    [examples] = [e for e in at.expander if "example questions" in e.proto.label.lower()]
    assert "optional" in examples.proto.label and not examples.proto.expanded
    text = " ".join(m.value for m in at.markdown)
    assert "Type any question in the box at the bottom of the page, in your own words" in text


def test_a_question_typed_in_the_box_is_sent_as_written_without_using_an_example(monkeypatch):
    calls = _stub_claude(monkeypatch, tokens=(1, 1))
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    typed = "Is the fall in visits since 2022 a fact I can quote?"
    _say(at, typed)
    assert not at.exception and calls[0][0] == typed
    assert [m.name for m in at.chat_message] == ["user", "assistant"]


def test_once_a_conversation_has_started_the_examples_are_gone_but_the_box_stays(monkeypatch):
    _stub_claude(monkeypatch, tokens=(1, 1))
    at = _box(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    _say(at, "What is the latest share?")
    assert not [e for e in at.expander if "example questions" in e.proto.label.lower()]
    assert len(at.chat_input) == 1 and not at.chat_input[0].proto.disabled
