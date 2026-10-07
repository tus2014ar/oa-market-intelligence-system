"""Tests for the segment-row evaluation harness (src/.../modeling/segment_eval.py).

Task A scores a predicted share for every segment-month on walk-forward test months. The
guarantees: training rows always come strictly before the test month, a model is never shown
the test month's outcomes, every metric has a checkable value, and the month bootstrap gives
reproducible, correctly signed paired differences against a baseline.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.modeling.segment_eval import (
    FutureLeakError,
    best_model,
    classify_from_share,
    improvement_summary,
    label_month_stats,
    log_loss_per_visit,
    mae_per_share,
    month_bootstrap,
    pooled,
    share_month_stats,
    walk_forward_predict_rows,
    walk_forward_row_splits,
)


def _rows(months=30, per_month=4, seed=0):
    rng = np.random.default_rng(seed)
    month_ids = [(2019 + (7 + i) // 12) * 100 + (7 + i) % 12 + 1 for i in range(months)]
    records = []
    for month in month_ids:
        for k in range(per_month):
            t = int(rng.integers(50, 300))
            z = int(rng.binomial(t, 0.03 + 0.01 * k))
            records.append(
                dict(month_id=month, segment=f"seg{k}", feature=float(k), market_share_lag1=0.04,
                     seg_high_lag1=float(k % 2), y_share=z / t, y_visits=t,
                     y_market=0.04, y_label="High" if z / t > 0.04 else "Low",
                     y_raw_above=int(z / t > 0.04))
            )
    return pd.DataFrame(records)


def test_splits_train_on_earlier_months_and_test_one_month_at_a_time():
    rows = _rows()
    splits = list(walk_forward_row_splits(rows, min_train_months=24))
    assert [s[0] for s in splits] == sorted(rows["month_id"].unique())[24:]
    for test_month, train_idx, test_idx in splits:
        assert (rows.iloc[train_idx]["month_id"] < test_month).all()
        assert (rows.iloc[test_idx]["month_id"] == test_month).all()
        assert len(set(rows.iloc[train_idx]["month_id"])) >= 24


def test_too_few_months_is_an_error():
    with pytest.raises(ValueError, match="min_train_months"):
        list(walk_forward_row_splits(_rows(months=10), min_train_months=24))


def _mean_share(train, test):
    return np.full(len(test), float(train["y_share"].mean()))


def test_predictions_cover_every_test_row_with_the_counts_needed_for_scoring():
    rows = _rows()
    preds = walk_forward_predict_rows(rows, _mean_share, min_train_months=24)
    test_rows = rows[rows["month_id"].isin(sorted(rows["month_id"].unique())[24:])]
    assert len(preds) == len(test_rows)
    needed = {"month_id", "segment", "p", "z", "t", "market_share_lag1", "seg_high_lag1"}
    assert needed <= set(preds)
    assert (preds["t"].to_numpy() == test_rows["y_visits"].to_numpy()).all()
    assert (preds["z"] == np.rint(test_rows["y_share"] * test_rows["y_visits"]).to_numpy()).all()


def test_a_model_never_sees_the_test_months_outcomes():
    rows = _rows()

    def peeks(train, test):
        return test["y_share"].to_numpy()  # the answer, if it were visible

    with pytest.raises(KeyError):
        walk_forward_predict_rows(rows, peeks, min_train_months=24)


def test_the_guard_raises_if_a_split_puts_the_test_month_in_training():
    rows = _rows()
    last = rows["month_id"].max()

    def leaky_splits(frame, **_):
        yield last, np.arange(len(frame)), np.flatnonzero(frame["month_id"] == last)

    with pytest.raises(FutureLeakError):
        walk_forward_predict_rows(rows, _mean_share, splits=leaky_splits)


def test_a_model_is_refit_only_on_data_before_each_test_month():
    rows = _rows()
    seen = []

    def spy(train, test):
        seen.append((train["month_id"].max(), test["month_id"].iloc[0]))
        return np.full(len(test), 0.03)

    walk_forward_predict_rows(rows, spy, min_train_months=24)
    assert seen and all(train_max < test_month for train_max, test_month in seen)


def test_log_loss_per_visit_matches_a_hand_computed_value():
    z, t, p = np.array([1, 0]), np.array([2, 2]), np.array([0.5, 0.25])
    expected = -(1 * np.log(0.5) + 1 * np.log(0.5) + 2 * np.log(0.75)) / 4
    assert log_loss_per_visit(z, t, p) == pytest.approx(expected)
    assert log_loss_per_visit(z, t, p) == pytest.approx(0.490415, abs=1e-5)


def test_predictions_of_exactly_zero_or_one_are_clipped_not_infinite():
    loss = log_loss_per_visit(np.array([3]), np.array([10]), np.array([0.0]))
    # 3 of 10 visits at a probability clipped to 1e-6: -(3 ln 1e-6 + 7 ln(1 - 1e-6)) / 10
    assert loss == pytest.approx(-3 * np.log(1e-6) / 10, rel=1e-5)
    assert loss == pytest.approx(4.1447, abs=1e-3)  # expensive but finite


def test_mae_is_visit_weighted_or_not_as_asked():
    z, t, p = np.array([1, 0]), np.array([2, 6]), np.array([0.5, 0.25])
    assert mae_per_share(z, t, p, weighted=True) == pytest.approx((2 * 0 + 6 * 0.25) / 8)
    assert mae_per_share(z, t, p, weighted=False) == pytest.approx(0.125)


def test_month_sums_pool_to_the_same_value_as_scoring_all_rows_at_once():
    rng = np.random.default_rng(1)
    preds = pd.DataFrame(
        {"month_id": np.repeat([1, 2, 3], 5), "t": rng.integers(20, 200, 15)}
    )
    preds["z"] = rng.binomial(preds["t"], 0.05)
    preds["p"] = rng.uniform(0.01, 0.1, 15)
    stats = share_month_stats(preds)
    assert pooled(stats, "log_loss") == pytest.approx(
        log_loss_per_visit(preds["z"], preds["t"], preds["p"])
    )
    assert pooled(stats, "mae_weighted") == pytest.approx(
        mae_per_share(preds["z"], preds["t"], preds["p"], weighted=True)
    )
    assert pooled(stats, "mae") == pytest.approx(
        mae_per_share(preds["z"], preds["t"], preds["p"], weighted=False)
    )


def _stats(p_for, rows=None, seed=0):
    rows = rows if rows is not None else _rows(months=30, seed=seed)
    preds = pd.DataFrame(
        {"month_id": rows["month_id"], "t": rows["y_visits"],
         "z": np.rint(rows["y_share"] * rows["y_visits"])}
    )
    preds["p"] = p_for(rows)
    return share_month_stats(preds)


def test_the_best_model_is_the_one_with_the_lowest_pooled_loss():
    rows = _rows()
    good = _stats(lambda r: (r["y_share"] * 0.5 + 0.015).clip(0.001, 0.2), rows)
    bad = _stats(lambda r: np.full(len(r), 0.2), rows)
    assert best_model({"good": good, "bad": bad}, "log_loss") == "good"


def test_the_month_bootstrap_is_reproducible_and_paired():
    rows = _rows()
    base = _stats(lambda r: np.full(len(r), 0.04), rows)
    same = _stats(lambda r: np.full(len(r), 0.04), rows)
    a = month_bootstrap({"base": base, "same": same}, "log_loss", n_boot=200, seed=3)
    b = month_bootstrap({"base": base, "same": same}, "log_loss", n_boot=200, seed=3)
    assert (a["base"] == b["base"]).all()
    assert (a["base"] == a["same"]).all()  # identical models give identical draws
    summary = improvement_summary(base, same, a["base"], a["same"], "log_loss")
    assert summary["estimate"] == pytest.approx(0.0, abs=1e-12)
    assert summary["low"] == pytest.approx(0.0, abs=1e-12)


def test_a_uniformly_better_model_has_an_improvement_interval_above_zero():
    rows = _rows(seed=2)
    base = _stats(lambda r: np.full(len(r), 0.04), rows)
    better = _stats(lambda r: (0.03 + 0.01 * r["feature"]).to_numpy(), rows)  # the true rates
    draws = month_bootstrap({"base": base, "better": better}, "log_loss", n_boot=500, seed=0)
    summary = improvement_summary(base, better, draws["base"], draws["better"], "log_loss")
    assert summary["estimate"] > 0
    assert summary["low"] > 0 and summary["high"] >= summary["low"]
    assert summary["share_positive"] > 0.99


def test_a_worse_model_has_an_interval_below_zero():
    rows = _rows(seed=2)
    base = _stats(lambda r: (0.03 + 0.01 * r["feature"]).to_numpy(), rows)
    worse = _stats(lambda r: np.full(len(r), 0.2), rows)
    draws = month_bootstrap({"base": base, "worse": worse}, "log_loss", n_boot=300, seed=0)
    summary = improvement_summary(base, worse, draws["base"], draws["worse"], "log_loss")
    assert summary["high"] < 0


def test_classify_from_share_predicts_high_when_above_last_months_market_share():
    preds = pd.DataFrame({"p": [0.05, 0.03, 0.04], "market_share_lag1": [0.04, 0.04, 0.04]})
    assert classify_from_share(preds).tolist() == ["High", "Low", "Low"]  # a tie is not above


def test_label_stats_match_a_hand_computed_balanced_accuracy_and_flip_score():
    preds = pd.DataFrame(
        {
            "month_id": [1, 1, 1, 1],
            "actual": ["High", "High", "Low", "Low"],
            "predicted": ["High", "Low", "Low", "High"],
            "last_month": ["High", "High", "High", "Low"],
        }
    )
    stats = label_month_stats(preds)
    assert pooled(stats, "balanced_accuracy") == pytest.approx(0.5)  # recalls 1/2 and 1/2
    assert stats["flip_n"].sum() == 1  # only the third row changed label from last month
    assert pooled(stats, "flip_accuracy") == pytest.approx(1.0)  # and the model got it right
    assert pooled(stats, "accuracy") == pytest.approx(0.5)
