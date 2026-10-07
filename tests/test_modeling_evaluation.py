"""Tests for the walk-forward evaluation harness (src/.../modeling/evaluation.py).

Protocol from PROPOSAL.md §18.3-§18.4: an expanding window with a 24-month minimum
training set, one month predicted at a time, McNemar's test on paired correctness. The
guarantee that matters most is the same one as the features: a prediction for month t
may not depend on anything from month t or later (except month t's own features, which
are known in advance). `test_prediction_for_month_t_ignores_later_months` enforces it
for the harness itself, using a real scikit-learn model as well as the baselines.
"""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier

from oa_market_intelligence.modeling.baselines import (
    MajorityClassBaseline,
    PersistenceBaseline,
    SeasonalBaseline,
    StratifiedRandomBaseline,
)
from oa_market_intelligence.modeling.evaluation import (
    CLASSES,
    bootstrap_difference,
    bootstrap_scores,
    compare_models,
    confusion,
    mcnemar_exact,
    score_predictions,
    score_table,
    simulate_scores,
    walk_forward_predict,
    walk_forward_splits,
)


def _month_ids(n, year=2019, month=8):
    ids = []
    for _ in range(n):
        ids.append(year * 100 + month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return ids


def _frame(n=40, seed=5):
    rng = np.random.RandomState(seed)
    return pd.DataFrame(
        {
            "direction_label": rng.choice(["Up", "Flat", "Down"], n, p=[0.2, 0.6, 0.2]),
            "f1": rng.normal(size=n),
            "f2": rng.normal(size=n),
        },
        index=pd.Index(_month_ids(n), name="month_id"),
    )


def test_splits_expand_one_month_at_a_time():
    splits = list(walk_forward_splits(30, min_train=24))
    assert len(splits) == 6
    assert [len(train) for train, _ in splits] == [24, 25, 26, 27, 28, 29]
    assert [test for _, test in splits] == [24, 25, 26, 27, 28, 29]
    for train, test in splits:
        assert train.max() < test
        assert train.tolist() == list(range(len(train)))


@pytest.mark.parametrize("min_train, n", [(0, 10), (10, 10), (11, 10)])
def test_splits_reject_a_window_with_no_room(min_train, n):
    with pytest.raises(ValueError):
        list(walk_forward_splits(n, min_train=min_train))


def test_predict_requires_time_ordered_rows():
    frame = _frame().iloc[::-1]
    with pytest.raises(ValueError, match="time-ordered"):
        walk_forward_predict(frame, MajorityClassBaseline, min_train=24)


@pytest.mark.parametrize(
    "make_model",
    [
        MajorityClassBaseline,
        PersistenceBaseline,
        lambda: LogisticRegression(max_iter=500),
        lambda: KNeighborsClassifier(n_neighbors=1),  # memorizes any row it is allowed to see
        lambda: StratifiedRandomBaseline(random_state=3),
        SeasonalBaseline,
    ],
    ids=["majority", "persistence", "logistic", "nearest-neighbour", "random", "seasonal"],
)
@pytest.mark.parametrize("t", [28, 33])
def test_prediction_for_month_t_ignores_later_months(make_model, t):
    frame = _frame()
    base = walk_forward_predict(frame, make_model, min_train=24)

    altered = frame.copy()
    original = altered["direction_label"].to_numpy()
    flipped = np.where(original == "Down", "Up", "Down")  # a different class on every row
    altered.loc[altered.index[t:], "direction_label"] = flipped[t:]  # the answer and later labels
    altered.loc[altered.index[t + 1 :], ["f1", "f2"]] = 99.0  # features of later months
    changed = walk_forward_predict(altered, make_model, min_train=24)

    month = frame.index[t]
    assert base.loc[month, "y_pred"] == changed.loc[month, "y_pred"]


def test_predict_returns_one_row_per_test_month():
    result = walk_forward_predict(_frame(), MajorityClassBaseline, min_train=24)
    assert len(result) == 16
    assert result.index.name == "month_id"
    assert list(result.columns) == ["y_true", "y_pred"]
    assert result.index[0] == _month_ids(40)[24]


def test_scores_for_a_known_confusion_matrix():
    y_true = ["Up", "Flat", "Flat", "Down", "Down", "Flat"]
    y_pred = ["Up", "Flat", "Down", "Down", "Flat", "Flat"]
    scores = score_predictions(y_true, y_pred)

    assert scores["accuracy"] == pytest.approx(4 / 6)
    assert scores["recall_down"] == pytest.approx(1 / 2)
    assert scores["precision_down"] == pytest.approx(1 / 2)
    assert scores["f1_down"] == pytest.approx(1 / 2)
    assert scores["recall_up"] == pytest.approx(1.0)
    assert scores["recall_flat"] == pytest.approx(2 / 3)
    assert scores["macro_f1"] == pytest.approx((1 + 2 / 3 + 1 / 2) / 3)
    assert scores["n"] == 6


def test_balanced_accuracy_ignores_classes_absent_from_the_truth():
    y_true = ["Flat", "Flat", "Down", "Down"]
    y_pred = ["Flat", "Up", "Down", "Down"]  # predicts a class that never occurs
    scores = score_predictions(y_true, y_pred)
    assert scores["balanced_accuracy"] == pytest.approx((1 / 2 + 1.0) / 2)


def test_recall_for_a_class_with_no_true_cases_is_zero_not_an_error():
    scores = score_predictions(["Flat", "Flat"], ["Flat", "Flat"])
    assert scores["recall_down"] == 0.0
    assert scores["precision_down"] == 0.0


def test_confusion_matrix_has_fixed_class_order():
    matrix = confusion(["Up", "Flat", "Down"], ["Flat", "Flat", "Down"])
    assert list(matrix.index) == CLASSES
    assert list(matrix.columns) == CLASSES
    assert matrix.loc["Up", "Flat"] == 1
    assert matrix.loc["Down", "Down"] == 1
    assert matrix.to_numpy().sum() == 3


def test_mcnemar_exact_matches_the_binomial_p_value():
    y = ["Flat"] * 8
    a = ["Flat"] * 8  # right every time
    b = ["Up"] * 8  # wrong every time
    result = mcnemar_exact(y, a, b)
    assert (result["a_only_correct"], result["b_only_correct"]) == (8, 0)
    assert result["p_value"] == pytest.approx(2 * 0.5**8)


def test_mcnemar_with_no_disagreements_is_not_significant():
    y = ["Flat", "Down", "Up"]
    result = mcnemar_exact(y, y, y)
    assert result["n_discordant"] == 0
    assert result["p_value"] == 1.0


def test_mcnemar_positive_class_scores_down_versus_rest():
    y = ["Down", "Down", "Flat", "Flat"]
    a = ["Down", "Down", "Flat", "Flat"]
    b = ["Flat", "Flat", "Up", "Up"]
    overall = mcnemar_exact(y, a, b)
    down = mcnemar_exact(y, a, b, positive_class="Down")

    assert overall["a_only_correct"] == 4  # a is exactly right, b is wrong on every month
    # Down-vs-rest: b is right on the two Flat months (it correctly avoids saying Down).
    assert down["a_only_correct"] == 2
    assert down["b_only_correct"] == 0
    assert down["n_discordant"] == 2


def test_compare_models_aligns_every_model_on_the_same_months():
    frame = _frame()
    predictions = compare_models(
        frame,
        {"always-majority": MajorityClassBaseline, "persistence": PersistenceBaseline},
        min_train=24,
    )
    assert list(predictions.columns) == ["y_true", "always-majority", "persistence"]
    assert len(predictions) == 16

    table = score_table(predictions)
    assert list(table.index) == ["always-majority", "persistence"]
    assert {"accuracy", "balanced_accuracy", "macro_f1", "recall_down", "precision_down"} <= set(
        table.columns
    )
    assert (table["n"] == 16).all()


# ---------- undefined scores ----------


def test_undefined_per_class_scores_can_be_marked_nan_instead_of_zero():
    scores = score_predictions(["Flat", "Flat"], ["Flat", "Flat"], undefined=np.nan)
    assert np.isnan(scores["recall_down"])  # no true Down months
    assert np.isnan(scores["precision_down"])  # no Down predictions
    assert np.isnan(scores["f1_down"])
    assert scores["recall_flat"] == 1.0
    assert scores["macro_f1"] == pytest.approx(1 / 3)  # still averaged with 0 for empty classes


# ---------- bootstrap_scores ----------


def _noisy_predictions(n, accuracy=0.7, seed=0):
    rng = np.random.RandomState(seed)
    truth = rng.choice(CLASSES, n, p=[0.2, 0.6, 0.2])
    wrong = rng.rand(n) > accuracy
    pred = np.where(wrong, rng.choice(CLASSES, n), truth)
    return truth, pred


def test_bootstrap_is_reproducible_for_a_seed():
    truth, pred = _noisy_predictions(60)
    a = bootstrap_scores(truth, pred, n_boot=300, seed=4)
    b = bootstrap_scores(truth, pred, n_boot=300, seed=4)
    c = bootstrap_scores(truth, pred, n_boot=300, seed=5)
    pd.testing.assert_frame_equal(a, b)
    assert not a["lo"].equals(c["lo"])


def test_perfect_predictions_have_a_degenerate_interval():
    truth = ["Up", "Flat", "Flat", "Down", "Flat", "Down"] * 5
    table = bootstrap_scores(truth, truth, n_boot=200, seed=0)
    assert table.loc["accuracy", ["point", "lo", "hi"]].tolist() == [1.0, 1.0, 1.0]


def test_interval_brackets_the_point_estimate_and_narrows_with_more_months():
    small = bootstrap_scores(*_noisy_predictions(40), n_boot=400, seed=1).loc["accuracy"]
    large = bootstrap_scores(*_noisy_predictions(400), n_boot=400, seed=1).loc["accuracy"]
    for row in (small, large):
        assert row["lo"] <= row["point"] <= row["hi"]
    assert (large["hi"] - large["lo"]) < (small["hi"] - small["lo"])


def test_resamples_where_a_class_is_absent_are_excluded_not_scored_zero():
    truth = ["Down"] + ["Flat"] * 19  # a single Down month
    pred = ["Down"] + ["Flat"] * 19  # caught perfectly
    row = bootstrap_scores(truth, pred, n_boot=500, seed=0).loc["recall_down"]
    assert row["n_valid"] < 500  # some resamples drew no Down month at all
    assert row["lo"] == 1.0 and row["hi"] == 1.0  # zeros would have dragged lo to 0


@pytest.mark.parametrize("block_length", [0, 41])
def test_bootstrap_rejects_an_impossible_block_length(block_length):
    truth, pred = _noisy_predictions(40)
    with pytest.raises(ValueError, match="block_length"):
        bootstrap_scores(truth, pred, n_boot=10, block_length=block_length)


def test_block_bootstrap_resamples_in_runs_of_consecutive_months():
    truth, pred = _noisy_predictions(60)
    iid = bootstrap_scores(truth, pred, n_boot=400, seed=2, block_length=1).loc["accuracy"]
    blocks = bootstrap_scores(truth, pred, n_boot=400, seed=2, block_length=6).loc["accuracy"]
    assert blocks["lo"] <= blocks["point"] <= blocks["hi"]
    assert blocks["point"] == iid["point"]


# ---------- bootstrap_difference ----------


def test_difference_interval_for_a_model_that_is_always_better():
    truth = ["Flat", "Down", "Up"] * 10
    good, bad = truth, ["Up", "Flat", "Down"] * 10
    result = bootstrap_difference(truth, good, bad, metric="accuracy", n_boot=200, seed=0)
    assert (result["point"], result["lo"], result["hi"]) == (1.0, 1.0, 1.0)
    assert result["share_positive"] == 1.0


def test_difference_interval_for_identical_models_is_zero():
    truth, pred = _noisy_predictions(50)
    result = bootstrap_difference(truth, pred, pred, metric="recall_down", n_boot=200, seed=0)
    assert (result["point"], result["lo"], result["hi"]) == (0.0, 0.0, 0.0)
    assert result["share_positive"] == 0.0


# ---------- simulate_scores ----------


def test_simulate_scores_returns_one_row_per_seed_and_is_reproducible():
    frame = _frame(n=40)

    def make(seed):
        return lambda: StratifiedRandomBaseline(random_state=seed)

    first = simulate_scores(frame, make, seeds=range(8), min_train=24)
    again = simulate_scores(frame, make, seeds=range(8), min_train=24)
    assert len(first) == 8
    assert {"accuracy", "recall_down", "macro_f1", "n"} <= set(first.columns)
    pd.testing.assert_frame_equal(first, again)
    assert first["accuracy"].nunique() > 1  # different seeds really do differ
