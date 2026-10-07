"""Tests for the Step 9 judging tools (src/.../modeling/segment_judge.py).

Every measure has a small case with a worked answer. The case worth remembering is the flip
example: a model can catch every real flip and still gain nothing overall, because it also
flips stable segments, which is why the High/Low result is judged on net accuracy and not on the
flip score alone.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.segment_task import build_task_a_rows
from oa_market_intelligence.modeling.segment_eval import month_bootstrap
from oa_market_intelligence.modeling.segment_judge import (
    FEATURE_GROUPS,
    a2_extras,
    bucket_improvement,
    calibration_slope,
    calibration_table,
    exclude_and_build,
    judge_rules,
    monthly_wins,
    permutation_importance,
    rank_agreement,
    serve_with_tie_break,
)
from oa_market_intelligence.modeling.segment_models import LOGISTIC_CONFIGS, fitter_for


def _loss_stats(loss_sums, visits=100.0):
    months = list(range(1, len(loss_sums) + 1))
    return pd.DataFrame(
        {"loss_sum": loss_sums, "visits": [visits] * len(months), "abs_w": 0.0, "abs": 0.0,
         "n": 1.0},
        index=pd.Index(months, name="month_id"),
    )


def test_monthly_wins_counts_months_where_the_model_has_the_lower_loss():
    baseline = _loss_stats([10.0, 10.0, 10.0, 10.0])
    model = _loss_stats([9.0, 10.0, 11.0, 9.5])  # better, tied, worse, better
    wins, n = monthly_wins(baseline, model, "log_loss")
    assert (wins, n) == (2, 4)  # a tie is not a win


def _preds(p, z_rate=None, n=4000, seed=0, visits=(50, 400)):
    rng = np.random.default_rng(seed)
    t = rng.integers(*visits, n)
    truth = np.asarray(p if z_rate is None else z_rate)
    return pd.DataFrame(
        {"p": p, "t": t, "z": rng.binomial(t, truth), "month_id": 1}
    )


def test_calibration_slope_is_near_one_for_calibrated_predictions_and_low_for_overconfident():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.01, 0.10, 6000)
    good = _preds(p, n=6000, seed=2)
    assert calibration_slope(good)["slope"] == pytest.approx(1.0, abs=0.12)
    # predictions whose spread on the logit scale is twice the truth's: slope near one half
    logit = np.log(p / (1 - p))
    overconfident = _preds(1 / (1 + np.exp(-2 * logit + np.log(3))), z_rate=p, n=6000, seed=3)
    assert calibration_slope(overconfident)["slope"] == pytest.approx(0.5, abs=0.12)


def test_the_calibration_table_has_visit_weighted_deciles_that_track_a_calibrated_model():
    rng = np.random.default_rng(4)
    preds = _preds(rng.uniform(0.01, 0.10, 8000), n=8000, seed=5)
    table = calibration_table(preds, bins=10)
    assert len(table) == 10
    assert (table["pred"].diff().dropna() > 0).all()  # ordered by predicted share
    assert (table["pred"] - table["obs"]).abs().max() < 0.01
    assert table["visits"].sum() == preds["t"].sum()


def test_improvement_by_segment_size_uses_last_months_visits_in_the_plans_buckets():
    n = 2000
    rng = np.random.default_rng(6)
    visits_last = np.repeat([30, 80, 200, 600], n // 4)
    t = rng.integers(60, 300, n)
    truth = np.full(n, 0.05)
    base = pd.DataFrame(
        {"month_id": 1, "t": t, "z": rng.binomial(t, truth),
         "seg_log_visits_lag1": np.log1p(visits_last), "p": np.where(visits_last < 100, 0.2, 0.05)}
    )
    model = base.assign(p=0.05)  # the model is right everywhere; the baseline is wrong on small
    table = bucket_improvement(base, model)
    assert list(table["bucket"]) == ["<50", "50-100", "100-300", ">300"]
    assert table.loc[table["bucket"] == "<50", "improvement"].iloc[0] > 0
    assert table.loc[table["bucket"] == ">300", "improvement"].iloc[0] == pytest.approx(0, abs=1e-3)


def _bootstrap_inputs(logistic_gap, months=30, seed=0):
    """Month loss sums for logistic, gbm and rf where gbm is `logistic_gap` per visit better."""
    rng = np.random.default_rng(seed)
    base = 11.0 + rng.normal(0, 0.05, months)
    stats = {
        "logistic": _loss_stats(base),
        "gbm": _loss_stats(base - logistic_gap * 100),
        "rf": _loss_stats(base - logistic_gap * 100 + rng.normal(0, 0.01, months)),
    }
    draws = month_bootstrap(stats, "log_loss", n_boot=300, seed=0)
    return stats, draws


def test_when_models_are_tied_the_simplest_promoted_one_serves():
    stats, draws = _bootstrap_inputs(logistic_gap=0.0)
    out = serve_with_tie_break(["logistic", "gbm", "rf"], stats, draws)
    assert out["serving"] == "logistic"


def test_a_clearly_better_complex_model_beats_the_simpler_one():
    stats, draws = _bootstrap_inputs(logistic_gap=0.02)
    out = serve_with_tie_break(["logistic", "gbm", "rf"], stats, draws)
    assert out["serving"] in {"gbm", "rf"}
    only_gbm = serve_with_tie_break(["gbm"], stats, draws)
    assert only_gbm["serving"] == "gbm"  # a single promoted model serves by default
    assert serve_with_tie_break([], stats, draws)["serving"] is None


def _frame():
    return pd.DataFrame(
        {
            "month_id": 1,
            "actual": ["High", "High", "Low", "Low", "High", "Low"],
            "predicted": ["High", "Low", "Low", "High", "High", "Low"],
            "last_month": ["High", "High", "High", "Low", "Low", "Low"],
            "p": [0.07, 0.03, 0.01, 0.05, 0.09, 0.02],
            "market_share_lag1": [0.05, 0.04, 0.04, 0.04, 0.06, 0.04],
        }
    )


def test_a2_extras_match_a_worked_example_including_the_cost_of_wrong_flips():
    out = a2_extras(_frame())
    # score = p - market = .02, -.01, -.03, .01, .03, -.02; High rows score .02, -.01, .03
    assert out["auc_high"] == pytest.approx(8 / 9)
    assert out["f1_high"] == pytest.approx(2 / 3)  # 2 true, 1 false, 1 missed High
    assert out["flips_caught"] == pytest.approx(1.0)  # both real flips were called
    assert out["stable_wrongly_flipped"] == pytest.approx(0.5)  # 2 of 4 stable segments flipped
    assert out["accuracy"] == pytest.approx(4 / 6)
    assert out["persistence_accuracy"] == pytest.approx(4 / 6)
    assert out["net_accuracy_gain"] == pytest.approx(0.0)  # catching flips cost as much as it won


def test_rank_agreement_is_a_spearman_on_the_shared_index():
    a = pd.Series({"x": 1.0, "y": 2.0, "z": 3.0, "only_a": 9.0})
    b = pd.Series({"x": 10.0, "y": 20.0, "z": 30.0, "only_b": 0.0})
    assert rank_agreement(a, b) == pytest.approx(1.0)
    assert rank_agreement(a, 1 / b) == pytest.approx(-1.0)


def _gold(months=30, seed=0):
    rng = np.random.default_rng(seed)
    month_ids = [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(months)]
    rows = []
    for specialty in ("S1", "S2", "PEDIATRICS"):
        for age in ("40 TO 59", "65 TO 74"):
            rate = rng.uniform(0.01, 0.09)
            for month in month_ids:
                t = int(rng.integers(80, 400))
                rows.append((month, specialty, age, "FEMALE", int(rng.binomial(t, rate)), t))
    frame = pd.DataFrame(
        rows, columns=["month_id", "specialty_name", "age_band", "gender",
                       "branded_injectable_visits", "total_category_visits"]
    )
    frame["segment_visit_share"] = (
        frame["branded_injectable_visits"] / frame["total_category_visits"]
    )
    return frame


def test_excluding_pediatrics_rebuilds_the_dataset_without_it_and_recomputes_the_market():
    gold = _gold()
    base = build_task_a_rows(gold)
    without = exclude_and_build(gold, "PEDIATRICS")
    assert "PEDIATRICS" not in set(without["specialty_name"])
    assert (base["specialty_name"] == "PEDIATRICS").any()
    month = without["month_id"].iloc[-1]
    assert without.loc[without["month_id"] == month, "y_market"].iloc[0] != pytest.approx(
        base.loc[base["month_id"] == month, "y_market"].iloc[0]
    )


def test_excluding_months_drops_those_rows_but_not_the_lags_of_later_rows():
    gold = _gold()
    base = build_task_a_rows(gold)
    without = exclude_and_build(gold, "COVID months")
    assert not without["month_id"].between(202003, 202005).any()
    assert base["month_id"].between(202003, 202005).any()
    june = base[base["month_id"] == 202006].set_index("segment")["seg_share_lag1"]
    june_without = without[without["month_id"] == 202006].set_index("segment")["seg_share_lag1"]
    assert june.to_numpy() == pytest.approx(june_without.reindex(june.index).to_numpy())


def test_permutation_importance_finds_the_groups_the_model_relies_on():
    rows = build_task_a_rows(_gold(months=34))
    split = sorted(rows["month_id"].unique())[24]
    train, test = rows[rows["month_id"] < split], rows[rows["month_id"] >= split]
    visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
    fit = fitter_for("logistic", LOGISTIC_CONFIGS[-1])
    importance = permutation_importance(fit, train, test, visible, n_repeats=3, seed=0)
    assert set(importance.index) == set(FEATURE_GROUPS)
    # segments differ in a persistent way, so their own history matters far more than the calendar
    assert importance.loc["own history", "importance"] > importance.loc["calendar", "importance"]
    assert importance.loc["own history", "importance"] > 0


def test_the_judging_rules_apply_the_plans_thresholds():
    ok = judge_rules(
        summary={"low": 0.0004}, mae_model=(0.0049, 0.0112), mae_last_month=(0.0055, 0.0150),
        ba_summary={"low": 0.012}, slope=1.05,
    )
    assert ok == {"J1": True, "J2": True, "J3": True, "J4": True}
    bad = judge_rules(
        summary={"low": -0.0001}, mae_model=(0.0060, 0.0112), mae_last_month=(0.0055, 0.0150),
        ba_summary={"low": -0.01}, slope=1.4,
    )
    assert bad == {"J1": False, "J2": False, "J3": False, "J4": False}
