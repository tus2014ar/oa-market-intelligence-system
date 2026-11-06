"""Tests for the Segments tab's logic (serving/segments.py) and its charts: the ranked groups, the
concentration, the grid and the yearly lines by hand on a small table, and against the real
warehouse's older segment function. No Streamlit needed."""

import math

import pandas as pd
import pytest

from modelling_fixtures import month_ids
from oa_market_intelligence.serving import data, segments, trend


def _seg():
    """24 months (Jan 2023 to Dec 2024). Specialty A and B each have four segments (two age bands
    x two genders) of 100 visits a month: A has 2 Zilretta visits a segment in the first 12 months
    and 3 in the last 12, B has 1 throughout. Specialty C has one segment of 20 visits a month
    with 5 Zilretta visits (too few visits to be ranked)."""
    rows = []
    for i, month_id in enumerate(month_ids(24, start=(2023, 1))):
        for specialty, early, late in (("A", 2, 3), ("B", 1, 1)):
            for age in ("40 TO 59", "65 TO 74"):
                for gender in ("FEMALE", "MALE"):
                    rows.append((month_id, specialty, age, gender, early if i < 12 else late, 100))
        rows.append((month_id, "C", "40 TO 59", "FEMALE", 5, 20))
    frame = pd.DataFrame(
        rows, columns=["month_id", "specialty", "age_band", "gender", "zilretta", "category"]
    )
    frame.insert(1, "month", pd.to_datetime(frame["month_id"].astype(str), format="%Y%m"))
    return frame


# ------------------------------------------------------------------ the ranked groups


def test_the_ranked_groups_by_hand_and_the_small_one_is_left_out():
    table = segments.group_table(_seg(), "specialty")
    assert list(table["group"]) == ["A", "B"] and table.attrs["left_out"] == 1  # C: 480 visits
    a, b = table.iloc[0], table.iloc[1]
    assert a["share_pct"] == pytest.approx(100 * 240 / 9600) and a["zilretta_visits"] == 240
    assert b["share_pct"] == pytest.approx(100 * 96 / 9600)
    assert a["pct_of_zilretta"] == pytest.approx(100 * 240 / 336)  # among the groups listed
    assert a["pct_of_zilretta"] + b["pct_of_zilretta"] == pytest.approx(100)
    assert not table["small"].any() and a["label"] == "A"


def test_the_change_is_the_last_12_months_against_the_12_before_whatever_the_period():
    for period in ("All", "1Y"):
        table = segments.group_table(_seg(), "specialty", period)
        assert table.set_index("group")["delta_pp"].to_dict() == pytest.approx({"A": 1.0, "B": 0.0})


def test_a_shorter_period_changes_the_share_and_the_visits():
    table = segments.group_table(_seg(), "specialty", "1Y")
    assert table["share_pct"].tolist() == pytest.approx([100 * 144 / 4800, 100 * 48 / 4800])
    assert table["category_visits"].tolist() == [4800, 4800]
    assert table["small"].all()  # under 5,000 visits in the period


def test_a_group_under_the_floor_has_no_change_and_the_floor_can_be_moved():
    table = segments.group_table(_seg(), "specialty", min_visits=100)
    assert list(table["group"]) == ["C", "A", "B"]  # C has the highest share (25%)
    assert math.isnan(table.set_index("group").loc["C", "delta_pp"])  # under the movers' floor
    assert table.attrs["left_out"] == 0


def test_only_the_three_groupings_are_allowed():
    with pytest.raises(ValueError, match="by must be one of"):
        segments.group_table(_seg(), "region")


def test_age_and_gender_groups_are_ranked_too():
    ages = segments.group_table(_seg(), "age_band")
    assert (
        set(ages["group"]) == {"40 TO 59", "65 TO 74"} and ages["label"].str.contains(" to ").all()
    )
    genders = segments.group_table(_seg(), "gender")
    assert set(genders["group"]) == {"FEMALE", "MALE"}


# ------------------------------------------------------------------ concentration and extremes


def test_the_concentration_by_hand():
    conc = segments.concentration(segments.group_table(_seg(), "specialty"))
    assert conc["top"] == "A" and conc["top_pct"] == pytest.approx(100 * 240 / 336)
    assert conc["top3_pct"] == pytest.approx(100.0)
    assert conc["n_groups"] == 2 and conc["n_for_80"] == 2  # A alone is 71%: two groups reach 80%


def test_how_many_groups_make_up_four_fifths_at_the_edges():
    table = pd.DataFrame(
        {
            "group": list("abcde"),
            "label": list("ABCDE"),
            "zilretta_visits": [80, 10, 5, 3, 2],
        }
    )
    assert segments.concentration(table)["n_for_80"] == 1  # exactly 80%: one group is enough
    table["zilretta_visits"] = [79, 11, 5, 3, 2]
    assert segments.concentration(table)["n_for_80"] == 2
    assert segments.concentration(table.assign(zilretta_visits=0))["top"] is None
    assert segments.concentration(table.head(0))["n_groups"] == 0


def _small_table():
    return pd.DataFrame(
        {
            "label": ["Tiny", "Big", "Mid", "Low"],
            "group": ["t", "b", "m", "l"],
            "share_pct": [20.0, 3.0, 2.0, 0.5],
            "delta_pp": [5.0, 0.4, -0.7, None],
            "small": [True, False, False, False],
        }
    )


def test_a_small_group_is_never_named_in_a_headline():
    notes = segments.extremes(_small_table())
    assert notes["highest"] == ("Big", 3.0) and notes["lowest"] == ("Low", 0.5)
    assert notes["rising"] == ("Big", 0.4) and notes["falling"] == ("Mid", -0.7)


def test_if_every_group_is_small_they_are_all_considered():
    table = _small_table().assign(small=True)
    assert segments.extremes(table)["highest"] == ("Tiny", 20.0)


def test_no_rise_or_fall_is_named_when_nothing_moved_that_way():
    table = _small_table().assign(delta_pp=[None, 0.0, 0.0, None])
    notes = segments.extremes(table)
    assert notes["rising"] is None and notes["falling"] is None
    assert segments.extremes(pd.DataFrame(columns=_small_table().columns))["highest"] is None


# ------------------------------------------------------------------ the grid


def test_the_grid_by_hand_and_a_thin_cell_is_not_shown_as_a_rate():
    cells = segments.cross_table(_seg(), top_rows=3)
    assert list(dict.fromkeys(cells["row"])) == ["A", "C", "B"]  # most Zilretta visits first
    assert list(dict.fromkeys(cells["column"])) == ["40 TO 59", "65 TO 74"]
    a40 = cells[(cells["row"] == "A") & (cells["column"] == "40 TO 59")].iloc[0]
    assert (
        a40["zilretta"] == 120
        and a40["category"] == 4800
        and a40["share_pct"] == pytest.approx(2.5)
    )
    thin = segments.cross_table(_seg(), top_rows=3, min_cell=5000)
    assert not thin["shown"].any() and thin["share_pct"].isna().all()
    assert thin["zilretta"].sum() == 240 + 96 + 120  # the visits are kept, only the rate is hidden


def test_a_column_under_the_floor_is_dropped_and_a_missing_cell_is_empty():
    cells = segments.cross_table(_seg(), top_rows=3, min_column=5000)
    assert set(cells["column"]) == {"40 TO 59", "65 TO 74"}
    c_old = cells[(cells["row"] == "C") & (cells["column"] == "65 TO 74")].iloc[0]
    assert c_old["category"] == 0 and not c_old["shown"] and math.isnan(c_old["share_pct"])
    assert set(segments.cross_table(_seg(), min_column=20_000)["column"]) == set()


# ------------------------------------------------------------------ over time, wording, download


def test_the_yearly_lines_by_hand_and_a_part_year_is_marked():
    trend = segments.group_trend(_seg(), "specialty", ["A", "B"])
    a = trend[trend["group"] == "A"].set_index("year")
    assert a["share_pct"].to_dict() == pytest.approx({2023: 2.0, 2024: 3.0})
    assert not trend["partial_year"].any()
    part = segments.group_trend(_seg()[lambda f: f["month_id"] <= 202410], "specialty", ["A"])
    assert part["partial_year"].tolist() == [False, True] and part["months"].tolist() == [12, 10]


def test_the_sentence_names_the_range_and_what_was_left_out():
    table = segments.group_table(_seg(), "specialty")
    text = segments.sentence(table, "specialty", "All")
    assert text.startswith("Over all months, among 2 specialty groups with at least 5,000 visits")
    assert "from 1.00% (B) to 2.50% (A)" in text and text.endswith("1 under 1,000 visits left out.")
    short = segments.sentence(segments.group_table(_seg(), "specialty", "1Y"), "specialty", "1Y")
    assert short.startswith("Over the last year, among 2 specialty groups")
    assert "drawn faded" in segments.sentence(
        segments.group_table(_seg(), "specialty", "1Y").assign(small=[True, False]),
        "specialty",
        "1Y",
    )
    empty = segments.sentence(table.head(0), "specialty", "3Y")
    assert empty == "No specialty has enough visits over the last 3 years to be ranked."


def test_the_download_has_one_row_per_listed_group():
    lines = segments.csv(segments.group_table(_seg(), "specialty")).decode().strip().splitlines()
    assert lines[0] == (
        "group,zilretta_share_pct,zilretta_visits,three_class_visits,"
        "pct_of_zilretta_visits,change_last_12_months_pp"
    )
    assert lines[1].startswith("A,2.5,240,9600,71.43,1.0") and len(lines) == 3


# ------------------------------------------------------------------ against the real warehouse


@pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")
def test_over_all_months_the_shares_equal_the_older_segment_function():
    engine = data.open_engine()
    try:
        seg = data.segment_monthly(engine)
        for by in ("specialty", "age_band", "gender"):
            old = data.segments(engine, by).set_index("group")
            new = segments.group_table(seg, by).set_index("group")
            assert set(new.index) == set(old.index)
            assert (100 * old.loc[new.index, "share"]).to_numpy() == pytest.approx(
                new["share_pct"].to_numpy()
            )
            assert new["zilretta_visits"].sum() <= old["zilretta_visits"].sum()
    finally:
        engine.dispose()


# ------------------------------------------------------------------ the charts


def test_the_ranked_bars_draw_each_measure_and_colour_a_fall():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    table = segments.group_table(_seg(), "specialty")
    for show in ("share", "visits", "change"):
        spec = charts.group_chart(table, show).to_dict()
        assert spec["mark"]["type"] == "bar"
    change = charts.group_chart(table, "change").to_dict()
    assert change["encoding"]["color"]["field"] == "direction"  # blue up, red down
    assert "datum" not in str(charts.group_chart(table, "share").to_dict()["encoding"]["color"])


def test_the_grid_has_filled_cells_grey_cells_and_numbers():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    spec = charts.share_grid(segments.cross_table(_seg(), top_rows=3)).to_dict()
    assert [layer["mark"]["type"] for layer in spec["layer"]] == ["rect", "rect", "text"]


def test_the_yearly_lines_chart_has_a_line_and_points():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    frame = segments.group_trend(_seg(), "specialty", ["A", "B"])
    spec = charts.group_trend_chart(frame).to_dict()
    assert [layer["mark"]["type"] for layer in spec["layer"]] == ["line", "circle"]


# ---------------- a narrowed selection keeps the calendar


def test_a_filter_does_not_shift_the_window_when_the_whole_calendar_is_given():
    seg = _seg()
    calendar = sorted(seg["month_id"].unique())
    early = seg[(seg["specialty"] == "A") & (seg["month_id"] <= 202312)]  # nothing after 2023
    # on its own the frame's last 12 months are 2023, so A looks present
    assert list(segments.group_table(early, "specialty", "1Y")["group"]) == ["A"]
    # against the real calendar the last 12 months are 2024, where A has no visits
    assert segments.group_table(early, "specialty", "1Y", calendar=calendar).empty
    assert segments.months_in(early, "1Y", calendar)[0] == 202401
    assert segments.months_in(early, "1Y")[0] == 202301


def test_the_movers_use_the_real_calendar_too():
    seg = _seg()
    calendar = sorted(seg["month_id"].unique())
    early = seg[(seg["specialty"] == "A") & (seg["month_id"] <= 202312)]
    assert trend.movers(early, "specialty").empty  # only 12 months of its own: nothing to compare
    assert trend.movers(early, "specialty", calendar=calendar).empty  # and none in the last 12
    kept = trend.movers(seg, "specialty", calendar=calendar)
    assert list(kept["group"]) == list(trend.movers(seg, "specialty")["group"])


def test_a_part_year_is_judged_on_the_series_not_on_the_group():
    seg = _seg()
    calendar = sorted(seg["month_id"].unique())
    short = seg[(seg["specialty"] == "A") & (seg["month_id"] <= 202406)]
    own = segments.group_trend(short, "specialty", ["A"])
    assert own["partial_year"].tolist() == [False, True]  # 2024 looks part: it has only 6 months
    real = segments.group_trend(short, "specialty", ["A"], calendar=calendar)
    assert real["partial_year"].tolist() == [False, False]  # the series has all 12 months of 2024


def test_a_click_on_a_bar_or_a_grid_cell_is_read_from_the_group_it_names():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    bars = charts.group_chart(segments.group_table(_seg(), "specialty"), "share").to_dict()
    assert bars["params"][0]["name"] == "pick" and bars["params"][0]["select"]["fields"] == [
        "group"
    ]
    assert bars["mark"]["cursor"] == "pointer"
    grid = charts.share_grid(segments.cross_table(_seg(), top_rows=3)).to_dict()
    picks = grid["params"]  # a layered chart keeps its selections at the top
    assert [p["select"]["fields"] for p in picks] == [["row", "column"]]


def test_the_yearly_lines_stand_out_under_the_pointer_and_the_rest_fade():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    frame = segments.group_trend(_seg(), "specialty", ["A", "B"])
    spec = charts.group_trend_chart(frame).to_dict()
    [hover] = spec["params"]
    assert hover["select"]["on"] == "pointerover" and hover["select"]["fields"] == ["label"]
    assert hover["select"]["nearest"] is True  # client-side only: no page run on a hover
    line = spec["layer"][0]["encoding"]
    assert line["opacity"]["condition"]["param"] == hover["name"] and line["opacity"]["value"] < 1
