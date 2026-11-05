"""Tests for the Forecasts tab's logic (serving/forecasts.py) and its two charts: the national
forecasts, how often the ranges held, the segment view next to last month's share, the specialty
roll-up, the alert list and the wording, by hand on small frames and against the real warehouse."""

import math

import pandas as pd
import pytest

from modelling_fixtures import month_ids
from oa_market_intelligence.serving import data, forecasts

RUN = 202412


def _pred():
    """Run month Dec 2024. National forecasts at 1, 3 and 6 months. Segment forecasts at 1 and 3
    months for three segments: A|40 TO 59|FEMALE (100 visits, flagged by the alert), A|65 TO 74|MALE
    (300 visits) and B|40 TO 59|FEMALE (50 visits, small)."""
    rows = []
    for horizon, target in ((1, 202501), (3, 202503), (6, 202506)):
        rows.append(
            dict(
                run_month_id=RUN,
                target_month_id=target,
                horizon=horizon,
                level="national",
                point_share=0.02,
                lo80_share=0.018 - 0.001 * horizon,
                hi80_share=0.022 + 0.001 * horizon,
                lo90_share=0.017 - 0.001 * horizon,
                hi90_share=0.023 + 0.001 * horizon,
                model="last_month",
            )
        )
    segments = [
        ("A", "40 TO 59", "FEMALE", (0.03, 0.031), 100, 0.9, 1.0),
        ("A", "65 TO 74", "MALE", (0.01, 0.011), 300, 0.2, 0.0),
        ("B", "40 TO 59", "FEMALE", (0.02, 0.021), 50, None, None),
    ]
    for specialty, age, gender, points, visits, score, flag in segments:
        for horizon, point, bias in ((1, points[0], 0.05), (3, points[1], 0.10)):
            rows.append(
                dict(
                    run_month_id=RUN,
                    target_month_id=202500 + horizon,
                    horizon=horizon,
                    level="segment",
                    specialty_name=specialty,
                    age_band=age,
                    gender=gender,
                    model="logistic",
                    point_share=point,
                    origin_visits=visits,
                    alert_score=score if horizon == 1 else None,
                    alert_flag=flag if horizon == 1 else None,
                    known_bias=bias,
                )
            )
    return pd.DataFrame(rows)


def _seg():
    """Dec 2024: each segment's share is 2% (2 of 100, 6 of 300, 1 of 50). Nov 2024 is different, to
    show that only the run month is used."""
    rows = []
    for month, scale in ((202411, 9), (RUN, 1)):
        for specialty, age, gender, z, c in (
            ("A", "40 TO 59", "FEMALE", 2, 100),
            ("A", "65 TO 74", "MALE", 6, 300),
            ("B", "40 TO 59", "FEMALE", 1, 50),
        ):
            rows.append((month, specialty, age, gender, z * scale, c))
    frame = pd.DataFrame(
        rows, columns=["month_id", "specialty", "age_band", "gender", "zilretta", "category"]
    )
    frame.insert(1, "month", pd.to_datetime(frame["month_id"].astype(str), format="%Y%m"))
    return frame


def _scores():
    rows = [
        ("T1", "main", 1, 1, 0.96),
        ("T1", "main", 1, 3, 0.91),
        ("T1", "main", 1, 6, None),  # no coverage figure
        ("T1", "main", 0, 1, 0.50),  # not the served method
        ("T1", "other", 1, 1, 0.10),  # not the main scenario
        ("T2", "main", 1, 1, 0.70),  # not the national task
    ]
    return pd.DataFrame(rows, columns=["task", "scenario", "serving", "horizon", "coverage_90"])


ADOPTION = {
    "national": {"1": "last_month", "3": "last_month", "6": "last_month"},
    "segment": {"1": "logistic", "3": "logistic", "6": "last_month"},
}


# ------------------------------------------------------------------ the national forecast


def test_the_national_forecast_has_the_point_and_both_ranges_in_percent():
    nat = forecasts.national(_pred())
    assert list(nat["horizon"]) == [1, 3, 6] and list(nat["month"]) == [
        "Jan 2025",
        "Mar 2025",
        "Jun 2025",
    ]
    assert nat["forecast_pct"].tolist() == pytest.approx([2.0, 2.0, 2.0])
    assert nat["lo90_pct"].tolist() == pytest.approx([1.6, 1.4, 1.1])
    assert nat["hi80_pct"].tolist() == pytest.approx([2.3, 2.5, 2.8])
    assert set(nat["method"]) == {"same as last month"}
    assert nat["date"].iloc[0] == pd.Timestamp("2025-01-01")


def test_how_often_the_range_held_uses_only_the_served_main_rows_with_a_figure():
    assert forecasts.coverage(_scores()) == pytest.approx({1: 0.96, 3: 0.91})


def test_the_chart_frames_are_the_last_36_months_the_forecasts_and_a_line_to_each():
    months = month_ids(40, start=(2021, 1))
    monthly = pd.DataFrame({"month_id": months, "visit_share": [0.02] * 39 + [0.025]})
    monthly["month"] = pd.to_datetime(monthly["month_id"].astype(str), format="%Y%m")
    frames = forecasts.fan(monthly, _pred())
    assert len(frames["history"]) == 36 and frames["history"]["share_pct"].iloc[
        -1
    ] == pytest.approx(2.5)
    assert len(frames["forecast"]) == 3 and len(frames["links"]) == 6
    first = frames["links"].iloc[:2]
    assert first["share_pct"].tolist() == pytest.approx([2.5, 2.0])  # latest month, then forecast
    assert first["month"].iloc[0] == frames["history"]["month"].iloc[-1]
    assert first["month"].iloc[1] == pd.Timestamp("2025-01-01")


# ------------------------------------------------------------------ the segment forecasts


def test_each_segment_is_set_beside_last_months_share_by_hand():
    view = forecasts.segment_view(_pred(), _seg(), 1)
    assert list(view["age_band"]) == ["65 TO 74", "40 TO 59", "40 TO 59"]  # most visits first
    assert view["last_pct"].tolist() == pytest.approx([2.0, 2.0, 2.0])  # December only
    assert view["forecast_pct"].tolist() == pytest.approx([1.0, 3.0, 2.0])
    assert view["change_pp"].tolist() == pytest.approx([-1.0, 1.0, 0.0])
    assert view["small"].tolist() == [False, False, True]  # the 50-visit segment
    assert view["age_label"].iloc[0] == "65 to 74" and view["gender_label"].iloc[0] == "Male"


def test_the_horizon_picks_the_forecast_and_the_alert_is_one_month_only():
    three = forecasts.segment_view(_pred(), _seg(), 3)
    assert three["forecast_pct"].tolist() == pytest.approx([1.1, 3.1, 2.1])
    assert three["alert_flag"].isna().all() and forecasts.alerts(three).empty
    assert set(three["model"]) == {"logistic"}


def test_a_segment_with_no_visits_in_the_run_month_has_no_last_share():
    seg = _seg()
    seg = seg[~((seg["month_id"] == RUN) & (seg["specialty"] == "B"))]
    view = forecasts.segment_view(_pred(), seg, 1)
    row = view[view["specialty"] == "B"].iloc[0]
    assert math.isnan(row["last_pct"]) and math.isnan(row["change_pp"])


def test_the_specialty_roll_up_weights_by_visits_by_hand():
    spec = forecasts.specialty_view(forecasts.segment_view(_pred(), _seg(), 1), min_visits=0)
    a, b = spec.iloc[0], spec.iloc[1]
    assert (a["specialty"], b["specialty"]) == ("A", "B")  # 400 visits against 50
    assert a["forecast_pct"] == pytest.approx((3.0 * 100 + 1.0 * 300) / 400)
    assert (
        a["last_pct"] == pytest.approx(100 * 8 / 400) and a["visits"] == 400 and a["segments"] == 2
    )
    assert b["forecast_pct"] == pytest.approx(2.0) and b["last_pct"] == pytest.approx(2.0)


def test_a_specialty_with_too_few_visits_is_left_out_of_the_chart_and_counted():
    view = forecasts.segment_view(_pred(), _seg(), 1)
    spec = forecasts.specialty_view(view)  # the default floor is 300 visits
    assert list(spec["specialty"]) == ["A"] and spec.attrs["left_out"] == 1  # B has 50
    assert list(forecasts.specialty_view(view, min_visits=50)["specialty"]) == ["A", "B"]
    assert forecasts.specialty_view(view, min_visits=1000).empty


def test_the_alert_list_has_only_the_flagged_segments_highest_score_first():
    flagged = forecasts.alerts(forecasts.segment_view(_pred(), _seg(), 1))
    assert len(flagged) == 1
    assert (
        flagged["alert_score"].iloc[0] == pytest.approx(0.9)
        and flagged["age_band"].iloc[0] == "40 TO 59"
    )


# ------------------------------------------------------------------ how far to trust it


def test_the_trust_panel_reads_the_adoption_map_and_the_scores():
    one = forecasts.trust(ADOPTION, _scores(), 1)
    assert one == {
        "national_method": "same as last month",
        "segment_method": "logistic regression",
        "range_held": pytest.approx(0.96),
        "segment_is_model": True,
    }
    six = forecasts.trust(ADOPTION, _scores(), 6)
    assert six["segment_method"] == "same as last month" and not six["segment_is_model"]
    assert six["range_held"] is None


def test_the_bias_is_stated_in_words_at_each_horizon():
    pred = _pred()
    assert forecasts.bias_text(pred, 1) == (
        "At 1 month the segment forecast ran 5.0% too high in testing: read it as optimistic."
    )
    assert "10.0% too high" in forecasts.bias_text(pred, 3)
    six = forecasts.bias_text(pred, 6)
    assert "simple rule" in six and "too high" not in six


def test_horizons_are_labelled_in_singular_and_plural():
    assert [forecasts.horizon_label(h) for h in (1, 3, 6)] == ["1 month", "3 months", "6 months"]


def test_the_download_has_the_labels_the_shares_and_the_small_flag():
    view = forecasts.segment_view(_pred(), _seg(), 1)
    lines = forecasts.csv(view).decode().strip().splitlines()
    assert lines[0] == (
        "specialty,age_band,gender,last_month_share_pct,forecast_share_pct,"
        "visits_in_latest_month,small_segment"
    )
    assert lines[1] == "A,65 to 74,Male,2.0,1.0,300,False" and len(lines) == 4


# ------------------------------------------------------------------ against the real warehouse


@pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")
def test_the_real_forecasts_equal_the_stored_predictions_and_last_month_is_the_overviews():
    engine = data.open_engine()
    try:
        pred, seg = data.predictions(engine), data.segment_monthly(engine)
        monthly, scores = data.market_monthly(engine), data.model_scores(engine)
    finally:
        engine.dispose()
    nat = forecasts.national(pred)
    assert nat["forecast_pct"].iloc[0] == pytest.approx(100 * monthly["visit_share"].iloc[-1])
    assert list(nat["horizon"]) == [1, 3, 6]
    assert set(forecasts.coverage(scores)) == {1, 3, 6}
    for horizon in (1, 3, 6):
        view = forecasts.segment_view(pred, seg, horizon)
        assert len(view) == 113 and view["last_pct"].notna().all()
        stored = pred[(pred["level"] == "segment") & (pred["horizon"] == horizon)]
        assert view["forecast_pct"].sum() == pytest.approx(100 * stored["point_share"].sum())
        spec = forecasts.specialty_view(view, min_visits=0)
        assert spec["segments"].sum() == 113
        floor = forecasts.specialty_view(view)
        assert len(floor) + floor.attrs["left_out"] == len(spec) and (floor["visits"] >= 300).all()
    assert len(forecasts.alerts(forecasts.segment_view(pred, seg, 1))) == 26


# ------------------------------------------------------------------ the charts


def test_the_national_chart_layers_history_dotted_lines_ranges_and_points():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    months = month_ids(40, start=(2021, 1))
    monthly = pd.DataFrame({"month_id": months, "visit_share": [0.02] * 40})
    monthly["month"] = pd.to_datetime(monthly["month_id"].astype(str), format="%Y%m")
    spec = charts.national_fan_chart(forecasts.fan(monthly, _pred())).to_dict()
    marks = [layer["mark"]["type"] for layer in spec["layer"]]
    assert marks == ["line", "circle", "line", "rule", "rule", "point"]


def test_the_specialty_chart_has_a_bar_for_the_forecast_and_a_tick_for_last_month():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    spec = forecasts.specialty_view(forecasts.segment_view(_pred(), _seg(), 1), min_visits=0)
    chart = charts.specialty_forecast_chart(spec, 1).to_dict()
    assert [layer["mark"]["type"] for layer in chart["layer"]] == ["bar", "tick"]
