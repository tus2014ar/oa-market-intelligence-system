"""Tests for the Market trend tab's logic (serving/trend.py, serving/style.py, the chart in
serving/charts.py): the filters, windows, headline numbers, movers and profile by hand on a small
table, and the HTML builders. No Streamlit needed."""

import math

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import month_ids
from oa_market_intelligence.serving import style, trend


def _seg():
    """24 months (Jan 2023 to Dec 2024), two specialties, two age bands, two genders: eight
    segments of 100 visits a month. Specialty A has 2 Zilretta visits a segment in the first 12
    months and 3 in the last 12; specialty B has 1 throughout."""
    rows = []
    for i, month_id in enumerate(month_ids(24, start=(2023, 1))):
        for specialty, early, late in (("A", 2, 3), ("B", 1, 1)):
            for age in ("40 TO 59", "65 TO 74"):
                for gender in ("FEMALE", "MALE"):
                    rows.append(
                        dict(
                            month_id=month_id,
                            specialty=specialty,
                            age_band=age,
                            gender=gender,
                            zilretta=early if i < 12 else late,
                            category=100,
                        )
                    )
    frame = pd.DataFrame(rows)
    frame.insert(1, "month", pd.to_datetime(frame["month_id"].astype(str), format="%Y%m"))
    return frame


# ------------------------------------------------------------------ names, filters, windows


def test_names_are_written_for_a_reader():
    assert trend.pretty("65 TO 74") == "65 to 74" and trend.pretty("85 +") == "85+"
    assert trend.pretty("UNSPECIFIED") == "Not specified"
    assert trend.pretty("PHYSICAL MEDICINE & REHAB") == "Physical Medicine & Rehab"


def test_the_filters_narrow_the_rows_and_none_means_all():
    seg = _seg()
    assert len(trend.select(seg)) == len(seg)
    assert len(trend.select(seg, specialty="A")) == len(seg) // 2
    assert len(trend.select(seg, "A", "40 TO 59", "FEMALE")) == 24


def test_the_monthly_series_covers_every_month_even_where_a_group_has_no_visits():
    seg = _seg()
    full = trend.monthly(seg)
    assert len(full) == 24 and full["category"].eq(800).all()
    assert full["share"].iloc[0] == pytest.approx(12 / 800)  # 4 x 2 + 4 x 1
    assert full["share"].iloc[-1] == pytest.approx(16 / 800)  # 4 x 3 + 4 x 1
    none = trend.monthly(seg, specialty="ZZZ")
    assert len(none) == 24 and none["zilretta"].eq(0).all() and none["share"].isna().all()
    assert full["label"].iloc[0] == "Jan 2023"


def test_the_period_keeps_the_last_months_and_all_keeps_everything():
    full = trend.monthly(_seg())
    assert len(trend.window(full, "1Y")) == 12 and len(trend.window(full, "5Y")) == 24
    assert trend.window(full, "All") is full
    assert trend.window(full, "1Y")["month_id"].iloc[0] == 202401


def test_a_part_quarter_is_dropped_and_a_full_quarter_is_summed():
    full = trend.monthly(_seg()).iloc[1:]  # starts in February: the first quarter is part
    q = trend.quarterly(full)
    assert len(q) == 7 and q["month_id"].iloc[0] == 202304
    assert q["share"].iloc[0] == pytest.approx(3 * 12 / (3 * 800))
    assert q["label"].iloc[0] == "Q2 2023" and q["label"].iloc[-1] == "Q4 2024"


# ------------------------------------------------------------------ the straight line


def test_the_trend_line_recovers_a_known_slope_in_points_a_year():
    months = pd.date_range("2023-01-01", periods=24, freq="MS")
    frame = pd.DataFrame({"month": months, "share_pct": 2.0 - 0.01 * np.arange(24)})
    fitted, slope = trend.trend_line(frame)
    assert slope == pytest.approx(-0.12)  # 0.01 a month, 12 months
    assert fitted.to_numpy() == pytest.approx(frame["share_pct"].to_numpy())


def test_the_trend_line_needs_three_months_with_a_share():
    months = pd.date_range("2023-01-01", periods=5, freq="MS")
    frame = pd.DataFrame({"month": months, "share_pct": [1.0, 2.0, np.nan, np.nan, np.nan]})
    fitted, slope = trend.trend_line(frame)
    assert slope is None and fitted.isna().all()


# ------------------------------------------------------------------ the headline numbers


def test_the_headline_numbers_by_hand():
    full = trend.monthly(_seg())
    k = trend.kpis(full, full)
    assert k["latest_month_id"] == 202412 and k["latest_share"] == pytest.approx(0.02)
    assert k["year_ago_share"] == pytest.approx(12 / 800)
    assert k["change_vs_year_ago_pp"] == pytest.approx(0.5)
    assert k["peak_share"] == pytest.approx(0.02) and k["peak_month_id"] == 202401
    assert k["change_since_peak"] == pytest.approx(0.0)
    assert k["period_zilretta_visits"] == 12 * 12 + 12 * 16
    assert k["period_category_visits"] == 24 * 800
    assert k["period_share"] == pytest.approx(336 / 19200)
    assert k["slope_pp_per_year"] > 0 and k["n_months"] == 24


def test_a_month_with_too_few_visits_cannot_be_the_peak():
    full = trend.monthly(_seg())
    full.loc[5, ["zilretta", "category"]] = [3, 4]  # 75% of 4 visits
    full["share"] = full["zilretta"] / full["category"]
    k = trend.kpis(full, full)
    assert k["peak_share"] == pytest.approx(0.02)  # not the 75%
    thin = full.assign(category=50)
    assert trend.kpis(thin, thin)["peak_share"] is None
    assert trend.kpis(thin, thin)["change_since_peak"] is None


def test_the_period_decides_the_visit_totals_but_not_the_peak():
    full = trend.monthly(_seg())
    k = trend.kpis(full, trend.window(full, "1Y"))
    assert k["period_zilretta_visits"] == 12 * 16 and k["n_months"] == 12
    assert k["peak_month_id"] == 202401  # the peak is taken over the whole series


def test_a_small_group_is_flagged():
    seg = _seg()
    big, small = trend.monthly(seg), trend.monthly(seg, "A", "40 TO 59", "FEMALE")
    assert not trend.is_small(trend.kpis(big, big)) and trend.is_small(trend.kpis(small, small))


def test_the_sentence_names_the_group_and_the_changes():
    full = trend.monthly(_seg())
    text = trend.sentence(trend.kpis(full, full), "All visits")
    assert text.startswith("All visits: Zilretta's share of visits was 2.00% in Dec 2024, ")
    assert "0.50 points above a year earlier" in text
    assert text.endswith("and level with its peak of 2.00% (Jan 2024).")
    fall = full.copy()
    fall.loc[fall.index[-1], ["zilretta", "share", "share_pct"]] = [8, 0.01, 1.0]
    assert "50% below its peak of 2.00% (Jan 2024)." in trend.sentence(trend.kpis(fall, fall), "G")
    gone = trend.monthly(_seg(), specialty="ZZZ")
    assert "had no visits in Dec 2024" in trend.sentence(trend.kpis(gone, gone), "Group Z")


# ------------------------------------------------------------------ movers and profile


def test_the_movers_compare_the_last_12_months_with_the_12_before_by_hand():
    table = trend.movers(_seg(), "specialty")
    assert list(table["group"]) == ["A", "B"]  # largest Zilretta visits first
    a, b = table.iloc[0], table.iloc[1]
    assert a["share_pct"] == pytest.approx(3.0) and a["prior_pct"] == pytest.approx(2.0)
    assert a["delta_pp"] == pytest.approx(1.0) and b["delta_pp"] == pytest.approx(0.0)
    assert a["label"] == "A" and a["zilretta_visits"] == 144


def test_a_group_under_the_visit_floor_is_left_out_and_a_short_series_has_no_movers():
    assert list(trend.movers(_seg(), "specialty", min_visits=5000)["group"]) == []
    assert trend.movers(_seg()[lambda f: f["month_id"] >= 202401], "specialty").empty


def test_the_callouts_name_the_biggest_rise_and_fall_only_when_there_is_one():
    seg = _seg()
    seg.loc[(seg["specialty"] == "B") & (seg["month_id"] >= 202401), "zilretta"] = 0
    notes = trend.callouts(trend.movers(seg, "specialty"))
    assert notes["rising"] == "Biggest rise: A +1.00 pp"
    assert notes["falling"] == "Biggest fall: B -1.00 pp"
    flat = trend.callouts(trend.movers(_seg().assign(zilretta=1), "specialty"))
    assert flat == {"rising": None, "falling": None}
    assert trend.callouts(pd.DataFrame(columns=["delta_pp"])) == {"rising": None, "falling": None}


def test_the_profile_gives_each_groups_share_of_zilretta_visits_with_the_rest_as_other():
    sel = pd.DataFrame({"g": ["a", "b", "c", "a"], "zilretta": [3, 3, 1, 3], "x": 0})
    out = trend.composition(sel, "g", top=1)
    assert list(out["label"]) == ["A", "Other"]
    assert list(out["pct"]) == pytest.approx([60.0, 40.0])
    assert trend.composition(sel.assign(zilretta=0), "g").empty


def test_the_class_mix_by_hand_and_a_short_series_is_empty():
    months = month_ids(24, start=(2023, 1))
    national = pd.DataFrame(
        {
            "month_id": months,
            "zilretta": [1] * 12 + [2] * 12,
            "corticosteroid": [90] * 24,
            "nsaid_otc": [9] * 12 + [8] * 12,
        }
    )
    mix = trend.class_mix(national)
    assert list(mix["label"]) == ["Zilretta", "Generic steroid injections", "NSAID and OTC"]
    assert mix["pct"].iloc[0] == pytest.approx(100 * 2 / 100)  # 2 of 2 + 90 + 8
    assert mix["prior_pct"].iloc[0] == pytest.approx(100 * 1 / 100)
    assert mix["delta_pp"].sum() == pytest.approx(0.0)
    assert trend.class_mix(national.head(10)).empty


# ------------------------------------------------------------------ markers, years, download


def test_the_markers_are_the_registered_events_the_peak_and_the_dip():
    full = trend.monthly(_seg())
    assert list(trend.events(full)["label"]) == [
        "Peak: 2.00% in Jan 2024",
        "Data-capture dip begins (March to July 2024)",
    ]
    assert trend.dip_range(full) == (pd.Timestamp("2024-03-01"), pd.Timestamp("2024-07-01"))
    early = full[full["month_id"] < 202403]
    assert trend.dip_range(early) is None
    assert list(trend.events(early)["label"]) == ["Peak: 2.00% in Jan 2024"]


def test_the_yearly_table_marks_a_part_year():
    full = trend.monthly(_seg()).iloc[:-3]
    year = trend.yearly(full)
    assert list(year["year"]) == [2023, 2024] and list(year["partial_year"]) == [False, True]
    assert year["share_pct"].iloc[0] == pytest.approx(100 * 12 / 800)


def test_the_download_has_the_month_the_visits_and_the_share():
    text = trend.csv(trend.monthly(_seg())).decode()
    lines = text.strip().splitlines()
    assert lines[0] == "month,zilretta_visits,three_class_visits,zilretta_share_pct"
    assert lines[1] == "2023-01,12,800,1.5" and len(lines) == 25


# ------------------------------------------------------------------ the HTML builders


def test_a_change_is_up_down_or_flat_and_always_has_a_sign_as_text():
    assert [style.tone(v) for v in (0.5, -0.5, 0.001, None)] == ["up", "down", "flat", "flat"]
    assert style.signed(0.5) == "+0.50 pp" and style.signed(-0.1) == "-0.10 pp"
    assert style.signed(None) == "–"


def test_data_is_escaped_before_it_goes_into_html():
    card = style.kpi_card("<b>x</b>", "1 & 2", "<script>")
    assert "<b>x</b>" not in card and "&lt;b&gt;" in card and "&amp;" in card
    assert "<script>" not in style.callout("<script>") and "&lt;script&gt;" in style.callout(
        "<script>"
    )
    rows = style.rank_rows([("<i>n</i>", "1", 1.0, "+1.00 pp")], ("<h>", "a", "b"))
    assert "<i>n</i>" not in rows and "<h>" not in rows and "oa-up" in rows


def test_a_card_shows_its_change_pill_in_the_right_colour():
    up = style.kpi_card("L", "1%", "c", delta=0.3, pill="+0.30 pp")
    down = style.kpi_card("L", "1%", "c", delta=-0.3, pill="-0.30 pp")
    assert 'oa-pill up">+0.30 pp' in up and 'oa-pill down">-0.30 pp' in down
    assert "oa-pill" not in style.kpi_card("L", "1%", "c")


def test_bar_widths_are_relative_and_stay_between_zero_and_a_hundred():
    html = style.bar_rows([("a", 50.0, "50%"), ("b", 25.0, "25%"), ("c", -5.0, "0%")], scale_to=50)
    assert "width:100.0%" in html and "width:50.0%" in html and "width:0.0%" in html
    assert style.bar_rows([("a", 150.0, "x")], scale_to=100).count("width:100.0%") == 1
    assert style.bar_rows([]) == ""


def test_the_stylesheet_is_one_style_block():
    assert style.CSS.startswith("<style>") and style.CSS.endswith("</style>")
    assert style.CSS.count("{") == style.CSS.count("}")


# ------------------------------------------------------------------ the chart


def test_the_chart_has_the_share_the_guide_the_dip_and_the_markers_when_asked():
    pytest.importorskip("altair")  # the CI jobs without the site packages skip this
    from oa_market_intelligence.serving import charts

    full = trend.monthly(_seg())
    fitted, _ = trend.trend_line(full)
    spec = charts.market_trend_chart(
        full, fitted, trend.events(full), trend.dip_range(full)
    ).to_dict()
    marks = [layer["mark"]["type"] for layer in spec["layer"]]
    assert marks == ["rect", "line", "circle", "line", "rule"]
    bare = charts.market_trend_chart(full).to_dict()
    assert [layer["mark"]["type"] for layer in bare["layer"]] == ["line", "circle"]


def test_a_chart_of_quarters_draws_from_the_quarterly_frame():
    pytest.importorskip("altair")  # the CI jobs without the site packages skip this
    from oa_market_intelligence.serving import charts

    q = trend.quarterly(trend.monthly(_seg()))
    spec = charts.market_trend_chart(q).to_dict()
    assert spec["datasets"] and not math.isnan(q["share_pct"].iloc[0])
