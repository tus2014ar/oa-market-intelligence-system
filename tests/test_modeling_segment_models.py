"""Tests for the Task A baselines, models, tuning and run (src/.../modeling/segment_*.py).

Synthetic segments with persistent but different Zilretta rates give models something real to
learn, so "a trained model beats the market-only baseline" is a meaningful check. The tuning
tests pin the rules from the plan: choose by validation log-loss inside the training window,
ties go to the simpler setting, validate on the last 12 months only, retune every 6 test months.
"""

import json

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.segment_task import build_task_a_rows
from oa_market_intelligence.modeling.segment_baselines import (
    BASELINES,
    majority_label,
    persistence_label,
    smoothed,
    specialty_rule_label,
)
from oa_market_intelligence.modeling.segment_eval import (
    pooled,
    share_month_stats,
    walk_forward_predict_rows,
)
from oa_market_intelligence.modeling.segment_models import (
    GBM_CONFIGS,
    LOGISTIC_CONFIGS,
    RF_CONFIGS,
    TunedPredictor,
    expand_counts,
    fit_predict_for,
    tune_config,
)
from oa_market_intelligence.modeling.segment_task_run import (
    a1_decision,
    a2_frame,
    log_run,
    run_a1,
    run_a2,
)

AGES = ["40 TO 59", "65 TO 74", "75 TO 84"]


def _gold(months=36, seed=0):
    """24 segments with fixed, different true rates, so history is informative."""
    rng = np.random.default_rng(seed)
    month_ids = [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(months)]
    rows = []
    for specialty in ("S1", "S2", "S3", "S4"):
        for age in AGES:
            for gender in ("FEMALE", "MALE"):
                rate = rng.uniform(0.01, 0.09)
                for month in month_ids:
                    t = int(rng.integers(80, 400))
                    rows.append((month, specialty, age, gender, int(rng.binomial(t, rate)), t))
    frame = pd.DataFrame(
        rows,
        columns=["month_id", "specialty_name", "age_band", "gender",
                 "branded_injectable_visits", "total_category_visits"],
    )
    frame["segment_visit_share"] = (
        frame["branded_injectable_visits"] / frame["total_category_visits"]
    )
    return frame


def _rows(months=36, seed=0, scheme="interval"):
    return build_task_a_rows(_gold(months, seed), scheme=scheme)


FAST = {"gbm": {"max_iter": 30}, "rf": {"n_estimators": 30}}


def test_the_baselines_match_hand_computed_smoothed_values():
    gold = pd.DataFrame(
        [(m, s, "65 TO 74", "FEMALE", z, 100)
         for m, zs in zip((201901, 201902, 201903, 201904), ((2, 10), (4, 10), (6, 10), (8, 10)))
         for s, z in zip(("S1", "S2"), zs)],
        columns=["month_id", "specialty_name", "age_band", "gender",
                 "branded_injectable_visits", "total_category_visits"],
    )
    rows = build_task_a_rows(gold.assign(segment_visit_share=0.0))
    row = rows[(rows["month_id"] == 201904) & (rows["specialty_name"] == "S1")]
    assert BASELINES["market"](None, row)[0] == pytest.approx(0.08)
    assert BASELINES["last_month"](None, row)[0] == pytest.approx(6.5 / 101)
    assert BASELINES["segment_history"](None, row)[0] == pytest.approx(12.5 / 301)
    assert BASELINES["specialty_history"](None, row)[0] == pytest.approx(12.5 / 301)
    assert smoothed(0, 0) == pytest.approx(0.5)  # no history at all: a half-visit prior


def test_the_high_low_baselines():
    train = pd.DataFrame({"y_label": ["High", "Low", "Low", "Low"]})
    test = pd.DataFrame(
        {"seg_high_lag1": [1.0, 0.0, 1.0], "specialty_prior_share": [0.05, 0.03, 0.04],
         "market_share_lag1": [0.04, 0.04, 0.04]}
    )
    assert majority_label(train, test).tolist() == ["Low", "Low", "Low"]
    assert persistence_label(train, test).tolist() == ["High", "Low", "High"]
    assert specialty_rule_label(train, test).tolist() == ["High", "Low", "Low"]  # tie is Low


def test_expanding_counts_gives_success_and_failure_rows_with_weights_that_average_one():
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    z, t = np.array([2.0, 0.0, 5.0]), np.array([10.0, 4.0, 5.0])
    x2, y2, w2 = expand_counts(frame, z, t)
    # row 2 has no successes and row 3 no failures, so those two zero-weight rows drop out
    assert len(x2) == 4 and int((y2 == 1).sum()) == 2 and int((y2 == 0).sum()) == 2
    # weights are proportional to the counts (2, 8, 4, 5) and scaled so they total the row count
    assert w2.sum() == pytest.approx(len(frame))
    ratio = np.sort(w2) / np.array([2, 4, 5, 8])
    assert ratio == pytest.approx(np.full(4, ratio[0]))


def test_the_grids_are_the_ones_fixed_in_the_plan():
    assert [c["C"] for c in LOGISTIC_CONFIGS] == [0.01, 0.1, 1.0]
    assert {(c["max_depth"], c["learning_rate"]) for c in GBM_CONFIGS} == {
        (3, 0.05), (3, 0.1), (5, 0.05), (5, 0.1)
    }
    assert len(RF_CONFIGS) == 6
    assert {c["max_depth"] for c in RF_CONFIGS} == {4, 8, 12}
    assert {c["min_samples_leaf"] for c in RF_CONFIGS} == {5, 20}
    assert GBM_CONFIGS[0]["max_depth"] == 3  # simplest first, for tie-breaking


def _constant_factory(values):
    """A factory whose config i predicts the constant values[i] for every row."""
    return lambda config: (lambda train, test: np.full(len(test), values[config["i"]]))


def test_tuning_picks_the_lower_validation_loss_and_ties_go_to_the_first_config():
    rows = _rows()
    configs = [{"i": 0}, {"i": 1}, {"i": 2}]
    chosen, losses = tune_config(rows, configs, _constant_factory([0.5, 0.04, 0.04]))
    assert chosen == 1  # 0.04 is near the true share; 0.5 is far off; the tie keeps the first
    assert losses[0] > losses[1] and losses[1] == pytest.approx(losses[2])


def test_tuning_fits_before_the_validation_block_and_validates_on_the_last_12_months():
    rows = _rows()
    train = rows[rows["month_id"] < sorted(rows["month_id"].unique())[26]]
    record = []

    def factory(config):
        def fit_predict(fit_rows, validation_rows):
            record.append((fit_rows["month_id"].max(), validation_rows["month_id"].min(),
                           validation_rows["month_id"].nunique()))
            return np.full(len(validation_rows), 0.04)
        return fit_predict

    tune_config(train, [{"i": 0}], factory, val_months=12)
    months = sorted(train["month_id"].unique())
    fit_max, val_min, val_n = record[0]
    assert val_n == 12 and val_min == months[-12]
    assert fit_max < val_min  # the model is fitted only on months before the validation block
    assert fit_max < train["month_id"].max()  # validation months lie inside the training window


def test_retuning_happens_every_six_test_months_only():
    rows = _rows(months=44)
    tunings = []

    def factory(config):
        def fit_predict(train, test):
            return np.full(len(test), 0.04)
        return fit_predict

    predictor = TunedPredictor("logistic", configs=[{"i": 0}, {"i": 1}], factory=factory,
                               retune_every=6)
    original = tune_config

    import oa_market_intelligence.modeling.segment_models as module

    def counting(*args, **kwargs):
        tunings.append(1)
        return original(*args, **kwargs)

    module.tune_config = counting
    try:
        walk_forward_predict_rows(rows, predictor, min_train_months=24)
    finally:
        module.tune_config = original
    n_folds = len(predictor.log)
    assert n_folds >= 12
    assert len(tunings) == -(-n_folds // 6)  # ceil(n_folds / 6)
    assert [entry["retuned"] for entry in predictor.log][:7] == [True] + [False] * 5 + [True]


@pytest.mark.parametrize("model", ["logistic", "gbm", "rf"])
def test_a_trained_model_beats_the_market_only_baseline_when_segments_differ(model):
    rows = _rows(months=34)
    config = {"logistic": LOGISTIC_CONFIGS, "gbm": GBM_CONFIGS, "rf": RF_CONFIGS}[model][-1]
    fit_predict = fit_predict_for(model, config, overrides=FAST.get(model, {}))
    preds = walk_forward_predict_rows(rows, fit_predict, min_train_months=24)
    market = walk_forward_predict_rows(rows, BASELINES["market"], min_train_months=24)
    assert ((preds["p"] > 0) & (preds["p"] < 1)).all()
    model_loss = pooled(share_month_stats(preds), "log_loss")
    market_loss = pooled(share_month_stats(market), "log_loss")
    assert model_loss < market_loss


def test_running_the_same_model_twice_gives_identical_predictions():
    rows = _rows(months=30)
    first = walk_forward_predict_rows(
        rows, fit_predict_for("gbm", GBM_CONFIGS[0], overrides=FAST["gbm"]), min_train_months=24
    )
    again = walk_forward_predict_rows(
        rows, fit_predict_for("gbm", GBM_CONFIGS[0], overrides=FAST["gbm"]), min_train_months=24
    )
    assert (first["p"].to_numpy() == again["p"].to_numpy()).all()


def test_the_serving_rule_promotes_only_a_model_whose_lower_bound_is_above_zero():
    summaries = {
        "logistic": {"low": 0.0004, "estimate": 0.002},
        "gbm": {"low": 0.0010, "estimate": 0.003},
        "rf": {"low": -0.0002, "estimate": 0.001},
    }
    losses = {"logistic": 0.110, "gbm": 0.108, "rf": 0.109, "last_month": 0.113}
    decision = a1_decision(summaries, losses, best_baseline="last_month")
    assert decision["promoted"] == ["logistic", "gbm"]
    assert decision["serving"] == "gbm"  # the promoted model with the lowest loss
    none = a1_decision({"rf": {"low": -0.0002, "estimate": 0.001}}, losses, "last_month")
    assert none["promoted"] == [] and none["serving"] == "last_month"


def test_the_a1_run_returns_scores_summaries_and_a_decision():
    rows = _rows(months=32)
    result = run_a1(rows, trained=("logistic",), n_boot=50, seed=0, overrides=FAST)
    assert set(result["scores"].index) >= {"market", "last_month", "segment_history",
                                           "specialty_history", "logistic"}
    assert {"log_loss", "mae_weighted", "mae"} <= set(result["scores"].columns)
    assert result["best_baseline"] in BASELINES
    assert "logistic" in result["summaries"]
    assert result["decision"]["serving"] in {"logistic", result["best_baseline"]}


def test_the_a2_frame_derives_labels_from_the_a1_prediction_on_the_two_label_rows():
    rows = _rows(months=30)
    two_label = _rows(months=30, scheme="two_label")
    preds = walk_forward_predict_rows(rows, BASELINES["last_month"], min_train_months=24)
    frame = a2_frame(preds, two_label)
    assert set(frame["actual"]) <= {"High", "Low"} and set(frame["predicted"]) <= {"High", "Low"}
    assert set(zip(frame["month_id"], frame["segment"])) <= set(
        zip(two_label["month_id"], two_label["segment"])
    )
    assert len(frame) > 0


def test_a_run_record_is_written_as_json(tmp_path):
    path = tmp_path / "runs.jsonl"
    log_run(path, "logistic", {"C": 0.1}, {"log_loss": 0.11})
    record = json.loads(path.read_text().strip())
    assert record["name"] == "logistic" and record["params"] == {"C": 0.1}
    assert record["metrics"] == {"log_loss": 0.11} and "time" in record


def test_the_a2_run_scores_the_label_baselines_and_the_derived_models():
    rows = _rows(months=32)
    two_label = _rows(months=32, scheme="two_label")
    a1 = run_a1(rows, trained=("logistic",), n_boot=40, seed=0, overrides=FAST)
    a2 = run_a2(a1, two_label, n_boot=40, seed=0)
    assert {"majority", "persistence", "specialty_rule", "A1 logistic", "A1 last_month"} <= set(
        a2["scores"].index
    )
    assert {"balanced_accuracy", "accuracy", "flip_accuracy"} <= set(a2["scores"].columns)
    assert a2["best_baseline"] in {"majority", "persistence", "specialty_rule"}
    assert ("A1 logistic", "flip_accuracy") in a2["summaries"]
    # persistence is wrong on every flip by construction
    assert a2["scores"].loc["persistence", "flip_accuracy"] == pytest.approx(0.0)
