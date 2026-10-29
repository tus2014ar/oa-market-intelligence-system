"""Tests for what the site draws (src/.../serving/views.py): every frame and every sentence has an
answer that can be checked by hand on small frames."""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.serving import views

ADOPTION = {
    "national": {"1": "last_month", "3": "last_month", "6": "last_month"},
    "segment": {"1": "logistic", "3": "logistic", "6": "last_month"},
    "alert": {"1": "logistic"},
}


def _monthly():
    ids = [202112, 202201, 202202, 202203]
    z = [338, 300, 250, 200]
    return pd.DataFrame(
        {
            "month_id": ids,
            "month": pd.to_datetime([str(i) for i in ids], format="%Y%m"),
            "zilretta": z,
            "corticosteroid": [4000] * 4,
            "nsaid_otc": [5662] * 4,
            "visit_share": [v / 10000 for v in z],
        }
    )


def _pred():
    seg = [
        ("A", "40 TO 59", "MALE", 1, 0.040, 100.0, 0.055, 1, 0.8),
        ("B", "40 TO 59", "MALE", 1, 0.010, 300.0, 0.055, 0, 0.1),
        ("A", "40 TO 59", "MALE", 3, 0.050, 100.0, 0.109, np.nan, np.nan),
        ("B", "40 TO 59", "MALE", 3, 0.020, 300.0, 0.109, np.nan, np.nan),
    ]
    rows = [
        dict(
            level="segment",
            horizon=h,
            specialty_name=s,
            age_band=a,
            gender=g,
            model="logistic",
            point_share=p,
            origin_visits=v,
            known_bias=b,
            alert_flag=f,
            alert_score=sc,
            target_month_id=202205,
            lo90_share=np.nan,
            hi90_share=np.nan,
        )
        for s, a, g, h, p, v, b, f, sc in seg
    ]
    rows.append(
        dict(
            level="national",
            horizon=1,
            specialty_name=None,
            age_band=None,
            gender=None,
            model="last_month",
            point_share=0.02,
            origin_visits=np.nan,
            known_bias=np.nan,
            alert_flag=np.nan,
            alert_score=np.nan,
            target_month_id=202204,
            lo90_share=0.015,
            hi90_share=0.025,
        )
    )
    return pd.DataFrame(rows)


def test_month_label_and_percent():
    assert views.month_label(202507) == "Jul 2025"
    assert views.pct(0.01883) == "1.88%" and views.pct(0.5, 0) == "50%"


def test_the_headline_numbers_by_hand():
    h = views.headline(_monthly(), _pred())
    assert h["latest_month"] == "Mar 2022" and h["latest_share"] == pytest.approx(0.02)
    assert h["peak_month"] == "Dec 2021" and h["peak_share"] == pytest.approx(0.0338)
    assert h["change_since_peak"] == pytest.approx(0.02 / 0.0338 - 1)
    assert h["next_month"] == "Apr 2022" and h["next_month_range90"] == (0.015, 0.025)
    assert h["months_of_data"] == 4 and h["total_visits"] == 1088 + 4 * (4000 + 5662)


def test_use_and_dont_names_the_horizons_that_are_used():
    rows = views.use_and_dont(ADOPTION)
    assert [r["what"] for r in rows] == [
        "National share forecast",
        "Segment forecast",
        "Decline alert",
    ]
    assert "1 month and 3 months" in rows[1]["use"] and "6 month" not in rows[1]["use"]
    assert "1 month only" in rows[2]["use"]


def test_the_trend_frame_flags_the_dip_and_the_events_include_the_peak():
    m = _monthly()
    m.loc[1, "month_id"] = 202403
    frame = views.trend_frame(m)
    assert list(frame["in_dip"]) == [False, True, False, False]
    assert frame["share_pct"].iloc[0] == pytest.approx(3.38)
    events = views.event_frame(_monthly())
    assert any("Peak: 3.38% in Dec 2021" in label for label in events["label"])
    assert any("March 2022" in label for label in events["label"])  # the turn is in the data
    assert not any("dip" in label for label in events["label"])  # 202403 is not in these months


def test_yearly_table_pools_the_counts_and_marks_part_years():
    yearly = views.yearly_table(_monthly())
    assert list(yearly["year"]) == [2021, 2022]
    assert yearly["share_pct"].iloc[1] == pytest.approx(
        100 * 750 / (750 + 3 * 9662)
    )  # Zilretta 300+250+200 of all three classes over the three months
    assert list(yearly["partial_year"]) == [True, True]


def test_national_forecast_and_known_bias():
    nat = views.national_forecast(_pred())
    assert nat["forecast_pct"].iloc[0] == pytest.approx(2.0) and nat["month"].iloc[0] == "Apr 2022"
    assert nat["low_90_pct"].iloc[0] == pytest.approx(1.5)
    assert views.known_bias(_pred()) == {1: pytest.approx(0.055), 3: pytest.approx(0.109)}
    assert "5.5% at 1 month and 10.9% at 3 months" in views.bias_sentence(_pred())


def test_the_specialty_forecast_weights_segments_by_visits_at_the_origin():
    sf = views.specialty_forecast(_pred()).set_index("specialty")
    assert sf.loc["A", "1_month_pct"] == pytest.approx(4.0) and sf.loc[
        "B", "3_month_pct"
    ] == pytest.approx(2.0)
    # two segments of one specialty would weight 0.04 and 0.02 by 100 and 300 visits
    two = _pred()
    extra = two[
        (two["level"] == "segment") & (two["specialty_name"] == "A") & (two["horizon"] == 1)
    ].copy()
    extra["age_band"], extra["point_share"], extra["origin_visits"] = "60 TO 64", 0.02, 300.0
    sf2 = views.specialty_forecast(pd.concat([two, extra])).set_index("specialty")
    assert sf2.loc["A", "1_month_pct"] == pytest.approx(100 * (0.04 * 100 + 0.02 * 300) / 400)
    assert list(views.specialty_forecast(_pred())["specialty"]) == ["B", "A"]  # largest first


def test_the_alert_list_summary_and_the_precision_from_the_scores():
    alerts = views.alert_frame(_pred())
    assert list(alerts["specialty"]) == ["A", "B"] and list(alerts["alert_flag"]) == [1, 0]
    scores = pd.DataFrame([dict(task="T3", horizon=1, scenario="main", serving=1, precision=0.49)])
    summary = views.alert_summary(alerts, scores)
    assert summary == {"scored": 2, "flagged": 1, "precision": pytest.approx(0.49)}


def _scores():
    def row(task, horizon, method, score, kind, **kw):
        base = dict(
            task=task,
            horizon=horizon,
            scenario="main",
            method=method,
            kind=kind,
            metric={"T1": "mae_pp", "T2": "log_loss", "T3": "pr_auc"}[task],
            score=score,
            is_best_baseline=int(kind == "baseline"),
            serving=0,
            promoted=0,
            gain_over_best_baseline=np.nan,
            bias_vs_actual=np.nan,
        )
        base.update(kw)
        return base

    return pd.DataFrame(
        [
            row("T1", 1, "last_month", 0.125, "baseline", serving=1),
            row("T2", 1, "last_month", 0.1132, "baseline"),
            row(
                "T2",
                1,
                "logistic",
                0.1127,
                "model",
                serving=1,
                promoted=1,
                gain_over_best_baseline=0.0005,
                bias_vs_actual=0.055,
            ),
            row("T2", 6, "last_month", 0.1103, "baseline", serving=1),
            row("T3", 1, "small_segment", 0.38, "baseline"),
            row(
                "T3",
                1,
                "logistic",
                0.55,
                "model",
                serving=1,
                promoted=1,
                gain_over_best_baseline=0.17,
            ),
            row("T3", 3, "small_segment", 0.37, "baseline"),
            row(
                "T3",
                3,
                "logistic",
                0.51,
                "model",
                serving=1,
                promoted=1,
                gain_over_best_baseline=0.14,
            ),
        ]
    )


def test_the_scoreboard_marks_adoption_and_says_what_it_means_in_plain_words():
    posthoc = {
        "horizons": {
            "1": {"lower_at_registered_percentile": 0.013},
            "3": {"lower_at_registered_percentile": -0.013},
        }
    }
    board = views.scoreboard(_scores(), ADOPTION, posthoc).set_index(["task", "horizon"])
    assert board.loc[("National share", 1), "verdict"] == "No model beat the simple rule"
    t2 = board.loc[("Segment share", 1)]
    assert (
        "about 0.4% better" in t2["verdict"]
        and t2["adopted"]
        and t2["adopted_model"] == "logistic regression"
    )
    assert board.loc[("Segment share", 6), "verdict"].startswith(
        "No model beat the simple rule reliably"
    )
    assert board.loc[("Segment share", 6), "chosen_model"] == "same as last month"
    assert board.loc[("Decline alert", 1), "verdict"].startswith("A reliable gain")
    assert bool(board.loc[("Decline alert", 1), "adopted"]) is True
    assert "not reliably better" in board.loc[("Decline alert", 3), "verdict"]
    assert (
        bool(board.loc[("Decline alert", 3), "adopted"]) is False
    )  # narrower than the registered rule


def test_the_bias_and_post_hoc_tables():
    bias = views.bias_table(_scores())
    assert list(bias["horizon"]) == [1] and bias["forecast_above_actual_pct"].iloc[
        0
    ] == pytest.approx(5.5)
    posthoc = {
        "horizons": {
            "1": dict(
                decline_share=0.23,
                pr_auc_registered_recent_drop=0.15,
                pr_auc_flipped_recent_drop=0.50,
                pr_auc_logistic=0.55,
                gain_over_flipped=0.05,
                lower_at_registered_percentile=0.013,
            )
        }
    }
    table = views.posthoc_table(posthoc)
    assert table["reliable"].iloc[0] and table["corrected_simple_rule"].iloc[0] == 0.50


def test_the_status_block_and_its_banner():
    run = {
        "status": "ok",
        "n_months": 72,
        "last_month_id": 202507,
        "finished_at": "x",
        "versions": {"data": "abc"},
        "monitoring": {
            "freshness": {"status": "stale", "age_days": 454, "max_age_days": 100},
            "drift": {"status": "nothing_to_compare"},
        },
        "stages": [{"stage": "swap", "status": "ok", "seconds": 0.0}],
        "ingest_files": [
            {"file_name": "a.xlsx", "sha256": "1" * 64},
            {"file_name": "a.xlsx", "sha256": "1" * 64},
        ],
    }
    status = views.status_block(run)
    assert "Jul 2025" in status["banner"] and "454" in status["banner"]
    assert "nothing to check yet" in status["drift_text"]
    assert list(views.stages_frame(run)["stage"]) == ["swap"]
    assert list(views.extracts_frame(run)["sha256_start"]) == [
        "1" * 12
    ]  # a file listed twice counts once
    fresh = views.status_block(
        {
            **run,
            "monitoring": {
                "freshness": {"status": "fresh", "age_days": 5, "max_age_days": 100},
                "drift": {},
            },
        }
    )
    assert fresh["banner"] is None
    assert views.status_block({})["run_status"] == "unknown"


def test_the_coverage_sentence_and_the_findings_frame():
    scores = pd.DataFrame(
        [
            dict(task="T1", scenario="main", serving=1, horizon=h, coverage_90=c)
            for h, c in ((1, 0.9583), (3, 0.9130), (6, 0.7674))
        ]
    )
    assert views.coverage_sentence(scores) == (
        "In testing, the 90% range contained what happened in 96% of months at 1 month, "
        "91% at 3 months and 77% at 6 months."
    )
    items = [{"id": 1, "finding": "f", "evidence": "e", "impact": "i", "source": "s", "extra": 1}]
    assert list(views.findings_frame(items).columns) == [
        "id",
        "finding",
        "evidence",
        "impact",
        "source",
    ]
