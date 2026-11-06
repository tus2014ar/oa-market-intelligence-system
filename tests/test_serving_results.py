"""Tests for the Model results tab's logic (serving/results.py and the test-list builder in
serving/style.py): the headline cards, the round 2 tests in plain words and the SHAP rows, by hand
on small inputs and against the real stored results. No Streamlit needed."""

import pandas as pd
import pytest

from oa_market_intelligence.serving import data, results, style, views


def _board():
    rows = []
    for horizon in (1, 3, 6):
        rows.append(
            ("National share", horizon, "same as last month", 0.12, "same as last month", 0.12)
        )
    rows.append(("Segment share", 1, "same as last month", 0.100, "logistic regression", 0.0996))
    rows.append(("Segment share", 3, "same as last month", 0.200, "logistic regression", 0.1996))
    rows.append(("Segment share", 6, "same as last month", 0.300, "same as last month", 0.300))
    return pd.DataFrame(
        rows,
        columns=[
            "task",
            "horizon",
            "simple_rule",
            "simple_rule_score",
            "chosen_model",
            "chosen_model_score",
        ],
    )


def _bias():
    return pd.DataFrame({"horizon": [1, 3, 6], "forecast_above_actual_pct": [5.5, 10.9, 18.2]})


def _alert(reliable=(True, False, False)):
    return pd.DataFrame(
        {
            "horizon": [1, 3, 6],
            "decline_share": [0.23, 0.24, 0.28],
            "registered_simple_rule": [0.15, 0.16, 0.20],
            "corrected_simple_rule": [0.50, 0.49, 0.50],
            "model": [0.55, 0.51, 0.51],
            "reliable": list(reliable),
        }
    )


def _round2():
    summary = [
        dict(test="E1a", horizon=1, passes=True, bias_before=0.055, bias_after=0.010),
        dict(test="E1b", horizon=3, passes=False, bias_before=0.109, bias_after=0.081),
        dict(
            test="E2",
            horizon=1,
            passes=False,
            precision_at_10=0.48,
            base_rate=0.16,
            best_comparator="recent_spike",
        ),
        dict(test="E3", horizon=3, passes=True, mean_spearman=0.49, best_comparator="random"),
    ]
    return {
        "summary": summary,
        "tests_passed": 3,
        "meta": {"tests": 7, "status": "exploratory and provisional"},
        "e2": {"1": {"scenarios": {"main": {"precision_at_10": {"recent_spike": 0.50}}}}},
    }


POSTHOC2 = {"e3_main": {"mean_spearman": {"segment_reversal": 0.38}}}


# ------------------------------------------------------------------ the headline cards


def test_the_segment_gain_is_the_fraction_of_the_rules_score_where_a_model_is_served():
    assert results.segment_gains(_board()) == pytest.approx({1: 0.004, 3: 0.002})


def test_the_four_headline_cards_by_hand():
    cards = results.headline_cards(_board(), _bias(), _alert(), _round2())
    assert [c[0] for c in cards] == ["National share", "Segment share", "Decline alert", "Round 2"]
    assert cards[0][1] == "No model wins" and "3 horizons" in cards[0][2]
    assert cards[1][1] == "+0.4% / +0.2%"
    assert "1 and 3 months" in cards[1][2] and "5.5% and 10.9% high" in cards[1][2]
    assert cards[2][1] == "1 of 3" and "1 month only" in cards[2][2]
    assert cards[3][1] == "3 of 7" and "none adopted" in cards[3][2]


def test_the_national_card_counts_models_that_beat_the_rule_if_any_do():
    board = _board()
    board.loc[0, ["chosen_model", "chosen_model_score"]] = ["ridge", 0.1]
    assert results.headline_cards(board, _bias(), _alert(), _round2())[0][1] == "1 of 3"


def test_a_scoreboard_task_filter_keeps_only_that_task():
    board = _board()
    assert len(results.for_task(board, "All")) == 6
    assert set(results.for_task(board, "Segment share")["task"]) == {"Segment share"}
    assert len(results.for_task(board, "Decline alert")) == 0


# ------------------------------------------------------------------ round 2 in plain words


def test_each_round_2_test_is_put_in_plain_words_with_its_result():
    rows = {(r["test"], r["horizon"]): r for r in results.round2_rows(_round2(), POSTHOC2)}
    e1a, e1b = rows[("E1a", 1)], rows[("E1b", 3)]
    assert (
        e1a["passes"] and e1a["result"] == "5.5% to 1.0% too high" and "limit 2.5%" in e1a["plain"]
    )
    assert not e1b["passes"] and e1b["result"] == "10.9% to 8.1% too high"
    assert "above the 5% limit" in e1b["plain"]  # the 3-month limit
    e2 = rows[("E2", 1)]
    assert e2["result"] == "48% of the top 10 right (chance 16%)"
    assert "3.0 times chance" in e2["plain"] and "gets 50%" in e2["plain"]
    assert "just spiked" in e2["plain"] and not e2["passes"]
    e3 = rows[("E3", 3)]
    assert e3["result"] == "rank correlation +0.49" and "+0.38" in e3["plain"] and e3["passes"]
    assert e1a["name"] == "Bias correction factor" and e3["horizon_label"] == "3 months"


def test_the_summary_lists_what_passed_and_the_status():
    summary = results.round2_summary(_round2())
    assert summary["passed"] == 3 and summary["total"] == 7
    assert summary["which"] == ["E1a (1 mo)", "E3 (3 mo)"]  # from this small summary
    assert "provisional" in summary["status"]


def test_the_provisional_note_says_nothing_is_used_by_the_site():
    assert "provisional" in results.PROVISIONAL
    assert "Nothing from round 2 is used by the site yet" in results.PROVISIONAL
    assert "after seeing round 1" in results.PROVISIONAL


# ------------------------------------------------------------------ what the model relies on


def _shap():
    block = {
        "typical_segment_pct": 1.85,
        "segments": 113,
        "matches_stored_forecasts": True,
        "contributions_add_up": True,
        "group_share_of_movement_pct": {"Time of year": 0.1, "Own share": 40.4, "Context": 19.0},
        "example": {
            "segment": {
                "specialty_name": "PHYSICIAN ASSISTANT",
                "age_band": "65 TO 74",
                "gender": "FEMALE",
            },
            "visits_last_month": 6104.0,
            "last_month_pct": 2.05,
            "forecast_pct": 1.96,
        },
    }
    return {"origin_month_id": 202507, "horizons": {"1": block, "3": dict(block)}}


def test_the_shap_rows_are_sorted_largest_first_with_a_readable_example():
    info = results.shap_rows(_shap(), 1)
    assert info["groups"] == [("Own share", 40.4), ("Context", 19.0), ("Time of year", 0.1)]
    assert results.shap_rows(_shap(), 1, top=2)["groups"][-1] == ("Context", 19.0)
    assert info["example"]["name"] == "Physician Assistant, ages 65 to 74, female"
    assert info["example"]["visits"] == 6104.0 and info["typical_pct"] == 1.85
    assert info["segments"] == 113 and info["checks_pass"] and info["origin_month_id"] == 202507


def test_the_shap_checks_fail_if_the_stored_ones_do():
    shap = _shap()
    shap["horizons"]["3"]["contributions_add_up"] = False
    assert (
        results.shap_rows(shap, 1)["checks_pass"] and not results.shap_rows(shap, 3)["checks_pass"]
    )


def test_the_alert_cards_have_the_three_scores_and_whether_the_gain_is_reliable():
    cards = results.alert_cards(_alert())
    assert cards[0] == ("1 month", 0.15, 0.50, 0.55, True)
    assert cards[1][0] == "3 months" and cards[1][4] is False


# ------------------------------------------------------------------ the list of tests in HTML


def test_a_pass_is_green_a_non_pass_grey_and_text_is_escaped():
    html = style.test_rows(
        [
            ("Name <b>", "E1a · 1 month", "Passes (provisional)", True, "r & s", "plain <i>"),
            ("Other", "E2 · 3 months", "No pass", False, "x", "y"),
        ]
    )
    assert 'oa-pill2 pass">Passes (provisional)' in html and 'oa-pill2 no">No pass' in html
    assert "<b>" not in html and "<i>" not in html and "&lt;b&gt;" in html and "&amp;" in html
    assert html.count('class="oa-test"') == 2 and style.test_rows([]) == ""


# ------------------------------------------------------------------ against the stored results


@pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")
def test_the_real_stored_results_give_the_headline_cards_the_site_shows():
    engine = data.open_engine()
    try:
        scores = data.model_scores(engine)
    finally:
        engine.dispose()
    posthoc, round2 = data.posthoc(), data.round2()
    board = views.scoreboard(scores, data.serving_map(), posthoc)
    cards = results.headline_cards(
        board, views.bias_table(scores), views.posthoc_table(posthoc), round2
    )
    assert cards[0][1] == "No model wins" and cards[1][1] == "+0.4% / +0.2%"
    assert "5.5% and 10.9% high" in cards[1][2]
    assert cards[2][1] == "1 of 3" and cards[3][1] == "3 of 7"


def test_the_real_round_2_results_make_seven_rows_and_three_passes():
    round2, posthoc2 = data.round2(), data.round2_posthoc()
    rows = results.round2_rows(round2, posthoc2)
    assert [(r["test"], r["horizon"]) for r in rows] == [
        ("E1a", 1),
        ("E1a", 3),
        ("E1b", 1),
        ("E1b", 3),
        ("E2", 1),
        ("E2", 3),
        ("E3", 3),
    ]
    assert [r["passes"] for r in rows] == [True, True, False, False, False, False, True]
    assert (
        rows[0]["result"] == "5.5% to 1.0% too high"
        and rows[1]["result"] == "10.9% to 3.0% too high"
    )
    assert rows[6]["result"] == "rank correlation +0.49" and "+0.38" in rows[6]["plain"]
    assert results.round2_summary(round2)["which"] == ["E1a (1 mo)", "E1a (3 mo)", "E3 (3 mo)"]


def test_the_real_shap_summary_gives_nine_groups_that_add_up_to_a_hundred():
    shap = data.shap_summary()
    for horizon in (1, 3):
        info = results.shap_rows(shap, horizon)
        assert len(info["groups"]) == 9 and info["checks_pass"]
        assert sum(p for _, p in info["groups"]) == pytest.approx(100, abs=0.5)
        assert info["groups"][0][0] == "The segment's own recent share"
