"""Tests for the Segment detail page's logic (serving/detail.py): the path to a group, how it is
compared with its neighbours, its summary against the market, the change over the year, its
forecast and its sentence, by hand on a small table and on the real warehouse."""

import pandas as pd
import pytest

from modelling_fixtures import month_ids
from oa_market_intelligence.serving import data, detail

CAL = month_ids(24, start=(2023, 1))


def _seg():
    """24 months. Specialty A: two age bands (X, Y) x two genders, 100 visits a segment a month,
    2 Zilretta visits a segment in the first 12 months and 3 in the last 12. Specialty B: the same
    four segments with 1 Zilretta visit throughout."""
    rows = []
    for i, month_id in enumerate(CAL):
        for specialty, early, late in (("A", 2, 3), ("B", 1, 1)):
            for age in ("X", "Y"):
                for gender in ("FEMALE", "MALE"):
                    rows.append((month_id, specialty, age, gender, early if i < 12 else late, 100))
    frame = pd.DataFrame(
        rows, columns=["month_id", "specialty", "age_band", "gender", "zilretta", "category"]
    )
    frame.insert(1, "month", pd.to_datetime(frame["month_id"].astype(str), format="%Y%m"))
    return frame


def _market():
    """The whole market: 12 + 4 = 16 Zilretta visits of 800 (2%) in the last 12 months."""
    seg = _seg()
    grouped = seg.groupby("month_id")[["zilretta", "category"]].sum().reset_index()
    grouped["visit_share"] = grouped["zilretta"] / grouped["category"]
    return grouped


# ------------------------------------------------------------------ the path and the neighbours


def test_the_path_runs_from_everything_to_the_most_specific_part_chosen():
    assert detail.crumbs(None, None, None) == ["All visits"]
    assert detail.crumbs("ORTHOPEDIC SURGERY", None, None) == ["All visits", "Orthopedic Surgery"]
    assert detail.crumbs("RHEUMATOLOGY", "65 TO 74", "FEMALE") == [
        "All visits",
        "Rheumatology",
        "Ages 65 to 74",
        "Female",
    ]
    assert detail.crumbs(None, None, "MALE") == ["All visits", "Male"]


def test_a_group_exists_only_when_something_is_chosen():
    assert not detail.chosen(None, None, None)
    assert detail.chosen("A", None, None) and detail.chosen(None, "X", None)
    assert detail.chosen(None, None, "MALE")


def test_neighbours_are_compared_along_the_most_specific_part_chosen():
    assert detail.peer_split(None, None, None) is None
    assert detail.peer_split("A", None, None) == ("specialty", (None, None, None))
    assert detail.peer_split("A", "X", None) == ("age_band", ("A", None, None))
    assert detail.peer_split("A", "X", "MALE") == ("gender", ("A", "X", None))
    assert detail.peer_split(None, None, "MALE") == ("gender", (None, None, None))


def test_the_neighbours_table_ranks_the_groups_and_marks_the_chosen_one_by_hand():
    table = detail.peers(_seg(), "B", None, None, "All", CAL)
    assert list(table["group"]) == ["A", "B"]  # A: 2.5% over all months, B: 1.0%
    assert list(table["rank"]) == [1, 2] and list(table["current"]) == [False, True]
    assert detail.rank_of(table) == (2, 2)
    assert table.attrs["by"] == "specialty"


def test_within_a_specialty_the_neighbours_are_its_other_age_bands():
    table = detail.peers(_seg(), "A", "X", None, "All", CAL)
    assert table.attrs["by"] == "age_band" and set(table["group"]) == {"X", "Y"}
    assert table.loc[table["current"], "group"].tolist() == ["X"]
    assert detail.peers(_seg(), None, None, None, "All", CAL).empty


def test_a_group_with_too_few_visits_to_be_ranked_has_no_rank():
    seg = _seg()
    thin = seg[~((seg["specialty"] == "A") & (seg["month_id"] < 202412))]  # one month of A
    table = detail.peers(thin, "A", None, None, "All", CAL)
    assert detail.rank_of(table) is None
    assert detail.rank_of(pd.DataFrame()) is None


# ------------------------------------------------------------------ against the market


def test_the_group_series_sits_beside_the_markets_for_the_months_of_the_period():
    series = detail.compare_series(_seg(), "A", None, None, _market(), "1Y")
    assert len(series) == 12 and series["month_id"].iloc[0] == 202401
    assert series["group_pct"].tolist() == pytest.approx([3.0] * 12)  # A in 2024: 3 of 100
    assert series["market_pct"].tolist() == pytest.approx([2.0] * 12)  # 16 of 800
    assert list(series.columns) == [
        "month_id",
        "month",
        "label",
        "group_pct",
        "category",
        "market_pct",
    ] or ({"group_pct", "market_pct", "label", "month"} <= set(series.columns))


def test_the_change_over_the_year_is_points_of_share_by_hand_and_none_when_noisy():
    full = detail.trend.monthly(_seg(), "A")
    assert detail.change_12m(full) == pytest.approx(1.0)  # 3% against 2%
    assert detail.change_12m(full.tail(20)) is None  # under 24 months
    assert detail.change_12m(full, minimum=10**9) is None  # too few visits for the floor
    flat = detail.trend.monthly(_seg(), "B")
    assert detail.change_12m(flat) == pytest.approx(0.0)


def test_the_summary_sets_the_share_against_the_market_and_counts_the_visits():
    s = detail.summary(_seg(), "A", None, None, "1Y", _market())
    assert s["latest_month_id"] == 202412 and s["latest_share"] == pytest.approx(0.03)
    assert s["market_share"] == pytest.approx(0.02) and s["gap_to_market_pp"] == pytest.approx(1.0)
    assert s["period_zilretta_visits"] == 12 * 12 and s["period_category_visits"] == 12 * 400
    assert s["change_12m_pp"] == pytest.approx(1.0) and not s["small"]


def test_a_small_group_is_flagged_in_its_summary():
    seg = _seg()
    s = detail.summary(seg, "A", "X", "FEMALE", "All", _market())  # 100 visits a month
    assert s["small"] and s["median_monthly_visits"] == 100


def test_the_sentence_says_which_side_of_the_market_and_which_way_over_the_year():
    s = detail.summary(_seg(), "A", None, None, "All", _market())
    text = detail.sentence("A", s)
    assert text == (
        "A: Zilretta's share of visits was 3.00% in Dec 2024, 1.00 points above the whole "
        "market's 2.00%, and 1.00 points up on a year earlier."
    )
    below = detail.summary(_seg(), "B", None, None, "All", _market())
    assert "1.00 points below the whole market's 2.00%" in detail.sentence("B", below)
    empty = detail.summary(_seg(), "ZZZ", None, None, "All", _market())
    assert detail.sentence("Z", empty) == "Z had no visits in Dec 2024."


# ------------------------------------------------------------------ the forecast


def _pred(known_bias=0.05):
    rows = []
    for horizon in (1, 3, 6):
        for age, visits, point in (("X", 300, 0.03), ("Y", 100, 0.01)):
            rows.append(
                dict(
                    run_month_id=202412,
                    target_month_id=202500 + horizon,
                    horizon=horizon,
                    level="segment",
                    specialty_name="A",
                    age_band=age,
                    gender="FEMALE",
                    model="logistic",
                    point_share=point,
                    origin_visits=visits,
                    alert_score=0.9
                    if (horizon == 1 and age == "X")
                    else (0.1 if horizon == 1 else None),
                    alert_flag=(1.0 if age == "X" else 0.0) if horizon == 1 else None,
                    known_bias=known_bias,
                )
            )
    return pd.DataFrame(rows)


def test_the_group_forecast_is_weighted_by_visits_with_the_alert_count():
    seg = _seg()
    block = detail.forecast_block(_pred(), seg, "A", None, None)
    assert [r["horizon"] for r in block["rows"]] == [1, 3, 6]
    first = block["rows"][0]
    assert first["forecast_pct"] == pytest.approx((3.0 * 300 + 1.0 * 100) / 400)
    assert first["segments"] == 2 and first["method"] == "logistic regression"
    assert block["alerts_scored"] == 2 and block["alerts_flagged"] == 1


def test_a_group_with_no_scored_segment_has_no_forecast():
    block = detail.forecast_block(_pred(), _seg(), "B", None, None)
    assert block == {"rows": [], "alerts_flagged": 0, "alerts_scored": 0}


# ------------------------------------------------------------------ the real warehouse


@pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")
def test_the_real_detail_of_orthopedic_surgery_matches_the_figures_on_the_other_pages():
    engine = data.open_engine()
    try:
        seg, market, pred = (
            data.segment_monthly(engine),
            data.market_monthly(engine),
            data.predictions(engine),
        )
    finally:
        engine.dispose()
    s = detail.summary(seg, "ORTHOPEDIC SURGERY", None, None, "All", market)
    assert round(100 * s["latest_share"], 2) == 1.44  # Orthopedic Surgery in Jul 2025
    assert round(100 * s["market_share"], 2) == 1.88  # the Overview's share
    assert round(s["gap_to_market_pp"], 2) == -0.44 and round(s["change_12m_pp"], 2) == -0.53
    calendar = sorted(seg["month_id"].unique())
    table = detail.peers(seg, "ORTHOPEDIC SURGERY", None, None, "All", calendar)
    rank, total = detail.rank_of(table)
    assert total == 22 and 1 <= rank <= total  # 22 specialties are ranked on the Segments page
    block = detail.forecast_block(pred, seg, "ORTHOPEDIC SURGERY", None, None)
    assert len(block["rows"]) == 3 and block["rows"][0]["segments"] >= 1


# ------------------------------------------------------------------ a range dragged on the chart


def _ms(text):
    return pd.Timestamp(text).timestamp() * 1000


def test_over_a_range_the_group_and_the_market_are_averaged_by_hand():
    series = detail.compare_series(_seg(), "A", None, None, _market(), "All")
    r = detail.range_compare(series, (_ms("2023-10-01"), _ms("2024-03-01")))
    # A is 2% in Oct to Dec 2023 and 3% in Jan to Mar 2024; the market is 1.5% then 2%
    assert r["months"] == 6 and r["from"] == "Oct 2023" and r["to"] == "Mar 2024"
    assert r["group_pct"] == pytest.approx(2.5) and r["market_pct"] == pytest.approx(1.75)
    assert r["gap_pp"] == pytest.approx(0.75)
    text = detail.range_sentence("Specialty A", r)
    assert "Oct 2023 to Mar 2024 (6 months)" in text and "averaged 2.50% against 1.75%" in text
    assert "0.75 points above" in text


def test_a_range_below_the_market_says_below_and_a_thin_range_gives_nothing():
    series = detail.compare_series(_seg(), "B", None, None, _market(), "All")
    r = detail.range_compare(series, (_ms("2024-01-01"), _ms("2024-03-01")))
    assert r["gap_pp"] < 0 and "points below" in detail.range_sentence("B", r)
    assert detail.range_compare(series, None) is None
    assert detail.range_compare(series, (_ms("2024-01-01"), _ms("2024-01-20"))) is None  # 1 month
    assert detail.range_compare(series, (_ms("2035-01-01"), _ms("2036-01-01"))) is None


def test_a_group_with_no_share_in_the_range_gives_nothing():
    series = detail.compare_series(_seg(), "A", None, None, _market(), "All")
    series["group_pct"] = float("nan")
    assert detail.range_compare(series, (_ms("2024-01-01"), _ms("2024-03-01"))) is None


def test_the_compare_chart_takes_a_dragged_range_only_when_asked():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    series = detail.compare_series(_seg(), "A", None, None, _market(), "All")
    assert "params" not in charts.compare_chart(series, "A").to_dict()
    [brush] = charts.compare_chart(series, "A", brush=True).to_dict()["params"]
    assert brush["name"] == "brush" and brush["select"]["type"] == "interval"
