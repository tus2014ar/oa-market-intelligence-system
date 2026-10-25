"""Tests for T3, the decline alert (src/.../modeling/alerts.py): the label and eligibility by hand,
precision, recall, F1 and PR-AUC by hand, the past-only alert threshold, the rewrite-the-future
check, promotion on planted scorers and a small end-to-end run that must reproduce itself."""

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from modelling_fixtures import make_inputs
from oa_market_intelligence.modeling import alerts, segment_models
from oa_market_intelligence.modeling.alerts import (
    BaselineAlert,
    TunedAlertModel,
    add_alert_label,
    alert_counts,
    alert_threshold,
    always_alert_score,
    average_precision,
    judge_alerts,
    method_report,
    recent_drop_score,
    run_alerts_horizon,
    small_segment_score,
    walk_forward_alerts,
)
from oa_market_intelligence.modeling.horizons import horizon_splits, month_index, pair_rows

Y = np.array([1, 0, 1, 0, 0, 1])
SCORE = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4])


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "gbm", [{"max_depth": 3, "learning_rate": 0.1}])
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "rf", [{"max_depth": 4, "min_samples_leaf": 20}])
    monkeypatch.setitem(segment_models.RF_BASE, "n_estimators", 15)


# ------------------------------------------------------------------ label and eligibility


def _label_rows(origin_share, outcome_share, visits):
    return pd.DataFrame(
        {
            "seg_share_lag1": origin_share,
            "y_share": outcome_share,
            "seg_log_visits_lag1": np.log1p(visits),
        }
    )


def test_label_threshold_by_hand():
    rows = _label_rows(
        [0.10, 0.10, 0.10, 0.10, 0.20],
        [0.07, 0.0701, 0.05, 0.12, 0.14],  # exactly -30%, just inside, -50%, a rise, exactly -30%
        [200, 200, 200, 200, 200],
    )
    labelled = add_alert_label(rows)
    assert list(labelled["y_alert"]) == [1, 0, 1, 0, 1]


def test_zero_origin_share_and_the_visit_floor_decide_who_is_scored():
    rows = _label_rows(
        [0.0, 0.10, 0.10],
        [0.0, 0.01, 0.01],
        [500, 99, 100],  # zero share cannot fall; 99 visits is under the floor; 100 is in
    )
    labelled = add_alert_label(rows)
    assert len(labelled) == 1
    assert labelled["y_alert"].iloc[0] == 1
    assert labelled["seg_log_visits_lag1"].iloc[0] == pytest.approx(np.log1p(100))


def test_the_fall_rule_can_be_changed_for_the_sensitivity():
    rows = _label_rows([0.10] * 3, [0.09, 0.075, 0.04], [300] * 3)
    assert list(add_alert_label(rows, 0.30)["y_alert"]) == [0, 0, 1]
    assert list(add_alert_label(rows, 0.20)["y_alert"]) == [0, 1, 1]
    assert list(add_alert_label(rows, 0.50)["y_alert"]) == [0, 0, 1]


# ------------------------------------------------------------------ the metrics


def test_pr_auc_by_hand():
    # (precision, recall) at each cut: (1, 1/3), (1/2, 1/3), (2/3, 2/3), (1/2, 2/3), (2/5, 2/3),
    # (1/2, 1)
    # average precision = 1/3 * 1 + 1/3 * 2/3 + 1/3 * 1/2 = 13/18
    assert average_precision(Y, SCORE) == pytest.approx(13 / 18)


def test_pr_auc_matches_scikit_learn_including_ties():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 300)
    score = np.round(rng.uniform(0, 1, 300), 1)  # many tied scores
    assert average_precision(y, score) == pytest.approx(average_precision_score(y, score))


def test_a_constant_score_has_the_no_skill_value():
    y = np.array([1, 0, 0, 0])
    assert average_precision(y, np.ones(4)) == pytest.approx(0.25)
    assert np.isnan(average_precision(np.zeros(4), np.ones(4)))


def test_precision_recall_f1_by_hand():
    out = alert_counts(Y, SCORE, 0.65)  # alerts on the first three rows
    assert out["n_alerts"] == 3 and out["n_positive"] == 3
    assert out["precision"] == pytest.approx(2 / 3)
    assert out["recall"] == pytest.approx(2 / 3)
    assert out["f1"] == pytest.approx(2 / 3)
    # one threshold per row works the same way
    per_row = alert_counts(Y, SCORE, np.full(6, 0.65))
    assert per_row == out
    nothing = alert_counts(Y, SCORE, np.inf)
    assert nothing["n_alerts"] == 0 and np.isnan(nothing["precision"]) and nothing["recall"] == 0


def test_always_alert_has_precision_equal_to_the_decline_share_and_recall_one():
    preds = pd.DataFrame(
        {"month_id": [1] * 4, "score": always_alert_score(np.zeros(4)),
         "label": [1, 0, 0, 0], "threshold": float("-inf")}
    )
    out = method_report(preds)
    assert out["precision"] == pytest.approx(0.25) and out["recall"] == 1.0
    assert out["pr_auc"] == pytest.approx(0.25)


def test_the_threshold_maximises_f1_and_ties_go_to_the_higher_cut():
    # F1 by cut: .9 0.5, .8 0.4, .7 2/3, .6 4/7, .5 0.5, .4 2/3 -> a tie between .7 and .4
    assert alert_threshold(SCORE, Y) == pytest.approx(0.7)
    assert alert_threshold(SCORE, np.zeros(6)) == np.inf  # no positives: no alerts


def test_baseline_scores_by_hand():
    rows = pd.DataFrame(
        {"seg_share_lag1": [0.05, 0.10], "seg_share_roll3": [0.10, 0.10],
         "seg_log_visits_lag1": [np.log1p(100), np.log1p(1000)]}
    )
    assert list(recent_drop_score(rows)) == pytest.approx([2.0, 1.0])  # below its 3-month mean
    assert small_segment_score(rows)[0] > small_segment_score(rows)[1]  # fewer visits, higher
    assert (always_alert_score(rows) == 1).all()


# ------------------------------------------------------------------ the walk and the leakage checks


def _paired(horizon, n_months=44):
    rows, outcomes, _ = make_inputs(n_months)
    return add_alert_label(pair_rows(rows, outcomes, horizon))


def test_the_predictor_never_sees_the_outcomes_and_trains_before_the_origin():
    horizon = 3
    paired = _paired(horizon)
    seen = []

    def spy(train, test):
        assert not any(c.startswith("y_") for c in test.columns)
        origin = month_index(test["origin_id"].iloc[0])
        assert month_index(train["month_id"].to_numpy()).max() <= origin
        seen.append(len(train))
        return np.zeros(len(test)), 0.5

    frame = walk_forward_alerts(paired, spy, horizon)
    assert len(seen) == frame["month_id"].nunique() == 44 - 2 - 24 - 2
    assert set(frame.columns) >= {"month_id", "score", "label", "threshold"}


def _rewrite_after(paired, origin):
    rng = np.random.default_rng(7)
    out = paired.copy()
    later = month_index(out["month_id"].to_numpy()) > origin
    out.loc[later, "y_alert"] = rng.integers(0, 2, later.sum())
    out.loc[later, "y_share"] = rng.uniform(0, 1, later.sum())
    later_origin = month_index(out["origin_id"].to_numpy()) > origin
    for column in ("seg_share_lag1", "seg_share_roll3", "market_share_lag1", "seg_prior_z",
                   "seg_prior_t", "spec_prior_z", "spec_prior_t"):
        out.loc[later_origin, column] = out.loc[later_origin, column] * 5 + 2
    return out


@pytest.mark.parametrize("horizon", [1, 3, 6])
@pytest.mark.parametrize("name", ["recent_drop", "logistic"])
def test_scores_and_threshold_do_not_move_when_the_future_is_rewritten(horizon, name):
    paired = _paired(horizon)
    test_month, train_idx, test_idx = list(horizon_splits(horizon)(paired))[6]
    origin = int(month_index(test_month)) - horizon
    tampered = _rewrite_after(paired, origin)

    def predict(frame):
        train, test = frame.iloc[train_idx], frame.iloc[test_idx]
        visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
        return alerts.alert_predictor(name, horizon, ())(train, visible)

    scores_a, thr_a = predict(paired)
    scores_b, thr_b = predict(tampered)
    assert np.allclose(scores_a, scores_b, atol=1e-12)
    assert thr_a == pytest.approx(thr_b)


def test_the_threshold_is_fitted_only_on_rows_whose_outcome_precedes_the_inner_origin():
    horizon = 3
    paired = _paired(horizon)
    recorded = []

    class Spy(TunedAlertModel):
        def _factory(self, config):
            def fit(train):
                recorded.append(month_index(train["month_id"].to_numpy()).max())
                return lambda test: np.full(len(test), 0.5)

            return fit

    test_month, train_idx, test_idx = list(horizon_splits(horizon)(paired))[10]
    train, test = paired.iloc[train_idx], paired.iloc[test_idx]
    visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
    model = Spy("logistic", horizon)
    model(train, visible)
    inner_months = sorted(train["month_id"].unique())[-alerts.INNER_ORIGINS:]
    limits = {int(month_index(m)) - horizon for m in inner_months}
    # the fits are: tuning, the final fit, then one per inner origin; each inner fit stops at its
    # own inner origin (the outcome month minus the horizon), never later
    inner_fits = recorded[-len(model._inner):]
    assert len(inner_fits) == len(model._inner) > 0
    assert sorted(inner_fits) == sorted(limits_used for limits_used in inner_fits)
    assert all(max_idx in limits for max_idx in inner_fits)
    last_outcome = month_index(train["month_id"].max())
    assert max(inner_fits) < last_outcome  # strictly before the last training outcome
    assert model.log[-1]["n_inner_origins"] == len(model._inner)


def test_inner_origins_with_too_little_training_data_are_skipped():
    horizon = 6
    paired = _paired(horizon)
    test_month, train_idx, test_idx = list(horizon_splits(horizon)(paired))[0]
    train, test = paired.iloc[train_idx], paired.iloc[test_idx]
    visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
    model = TunedAlertModel("logistic", horizon)
    model(train, visible)
    # counted independently: an inner origin is used only if the rows with an outcome at or before
    # it span at least MIN_INNER_TRAIN_MONTHS months
    outcome = month_index(train["month_id"].to_numpy())
    expected = 0
    for month in sorted(train["month_id"].unique())[-alerts.INNER_ORIGINS:]:
        origin = month_index(month) - horizon
        if len(np.unique(outcome[outcome <= origin])) >= alerts.MIN_INNER_TRAIN_MONTHS:
            expected += 1
    assert expected < alerts.INNER_ORIGINS  # the window is short at the first origin with h = 6
    assert model.log[-1]["n_inner_origins"] == expected


def test_baseline_threshold_uses_only_training_rows():
    paired = _paired(1)
    test_month, train_idx, test_idx = list(horizon_splits(1)(paired))[3]
    train, test = paired.iloc[train_idx], paired.iloc[test_idx]
    visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
    scores, thr = BaselineAlert("recent_drop")(train, visible)
    assert np.isfinite(thr)
    always, thr_always = BaselineAlert("always_alert")(train, visible)
    assert thr_always == -np.inf and (always >= thr_always).all()


# ------------------------------------------------------------------ promotion


def _preds(score, label, months):
    return pd.DataFrame({"month_id": months, "score": score, "label": label,
                         "threshold": 0.5, "specialty_name": "X"})


def test_a_planted_better_scorer_is_promoted_and_a_noise_scorer_is_not():
    rng = np.random.default_rng(5)
    months = np.repeat(np.arange(202101, 202141), 60)
    n = len(months)
    label = (rng.uniform(0, 1, n) < 0.25).astype(int)
    preds = {
        "recent_drop": _preds(rng.uniform(0, 1, n), label, months),
        "small_segment": _preds(rng.uniform(0, 1, n), label, months),
        "always_alert": _preds(np.ones(n), label, months),
        "logistic": _preds(label * 0.6 + rng.uniform(0, 0.8, n), label, months),  # planted better
        "gbm": _preds(rng.uniform(0, 1, n), label, months),  # noise
        "rf": _preds(rng.uniform(0, 1, n), label, months),  # noise
    }
    out = judge_alerts(preds, 1, n_boot=300)
    assert out["models"]["logistic"]["promoted"] and out["models"]["logistic"]["estimate"] > 0.2
    assert not out["models"]["gbm"]["promoted"] and not out["models"]["rf"]["promoted"]
    assert out["serving"] == "logistic"
    assert out["lower_percentile"] == pytest.approx(100 * 0.05 / 3)
    assert out["decline_share"] == pytest.approx(label.mean())
    assert out["methods"]["always_alert"]["pr_auc"] == pytest.approx(label.mean())
    assert judge_alerts(preds, 3, n_boot=50)["lower_percentile"] == pytest.approx(100 * 0.05 / 6)


def test_when_nothing_beats_the_baselines_a_baseline_serves():
    rng = np.random.default_rng(6)
    months = np.repeat(np.arange(202101, 202141), 60)
    n = len(months)
    label = (rng.uniform(0, 1, n) < 0.25).astype(int)
    preds = {name: _preds(rng.uniform(0, 1, n), label, months)
             for name in alerts.ALERT_BASELINES + alerts.ALERT_MODELS}
    preds["always_alert"] = _preds(np.ones(n), label, months)
    out = judge_alerts(preds, 1, n_boot=300)
    assert out["promoted"] == [] and out["serving_is_baseline"]


# ------------------------------------------------------------------ end to end


def test_run_alerts_horizon_end_to_end_is_reproducible():
    rows, outcomes, _ = make_inputs(n_months=34)
    kwargs = dict(scenarios=("main", "fall_20"), n_boot=40, seed=0)
    a = run_alerts_horizon(rows, outcomes, 3, **kwargs)
    b = run_alerts_horizon(rows, outcomes, 3, **kwargs)
    main = a["scenarios"]["main"]
    assert main["n_test_months"] == 34 - 2 - 24 - 2
    assert 0 < main["decline_share"] < 1
    assert set(main["methods"]) == set(alerts.ALERT_BASELINES + alerts.ALERT_MODELS)
    assert main["methods"]["always_alert"]["recall"] == 1.0
    assert a["scenarios"]["fall_20"]["decline_share"] > main["decline_share"]
    assert str(a["scenarios"]) == str(b["scenarios"])
