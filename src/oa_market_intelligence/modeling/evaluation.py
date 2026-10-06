"""Walk-forward evaluation for the Up / Down / Flat classifier.

Protocol (PROPOSAL.md §18.3-§18.4): an expanding training window with a 24-month
minimum, advancing one month at a time and retraining before every prediction, then
scoring the predictions and comparing models with McNemar's exact test.

The harness predicts exactly one month after each training window. A prediction for
month t therefore uses the labels and features of months before t plus month t's own
features, which are known in advance; month t's label is never visible to it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

import numpy as np
import pandas as pd
from scipy.stats import binomtest

TARGET = "direction_label"
CLASSES = ["Up", "Flat", "Down"]
DEFAULT_MIN_TRAIN = 24  # two full seasonal cycles (PROPOSAL.md §18.3)


def walk_forward_splits(
    n_rows: int, *, min_train: int = DEFAULT_MIN_TRAIN
) -> Iterator[tuple[np.ndarray, int]]:
    """Yield `(train_positions, test_position)`: the first `min_train` rows, then one more
    row per step. Training rows always come strictly before the test row."""
    if min_train < 1 or min_train >= n_rows:
        raise ValueError(f"min_train={min_train} leaves no test months out of {n_rows} rows")
    for test in range(min_train, n_rows):
        yield np.arange(test), test


def walk_forward_predict(
    frame: pd.DataFrame,
    make_model: Callable[[], object],
    *,
    target: str = TARGET,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> pd.DataFrame:
    """Predict every month after the first `min_train`, retraining a fresh model each time.

    `frame` is a time-ordered, model-ready table (the target plus numeric features,
    indexed by month). `make_model` returns an unfitted object with `fit(X, y)` and
    `predict(X)`. Returns `y_true` and `y_pred` per test month."""
    if not frame.index.is_monotonic_increasing:
        raise ValueError("frame must be time-ordered (index increasing)")
    features = frame.drop(columns=target)
    labels = frame[target]

    rows = []
    for train, test in walk_forward_splits(len(frame), min_train=min_train):
        model = make_model()
        model.fit(features.iloc[train], labels.iloc[train])
        prediction = model.predict(features.iloc[[test]])[0]
        rows.append((frame.index[test], labels.iloc[test], prediction))
    index_name = frame.index.name or "month_id"
    return pd.DataFrame(rows, columns=[index_name, "y_true", "y_pred"]).set_index(index_name)


def compare_models(
    frame: pd.DataFrame,
    factories: Mapping[str, Callable[[], object]],
    *,
    target: str = TARGET,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> pd.DataFrame:
    """Run each model over the same months. Columns: `y_true`, then one per model."""
    result = None
    for name, factory in factories.items():
        run = walk_forward_predict(frame, factory, target=target, min_train=min_train)
        if result is None:
            result = run[["y_true"]].copy()
        result[name] = run["y_pred"]
    return result


def score_predictions(y_true, y_pred) -> dict[str, float]:
    """Accuracy, balanced accuracy, macro-F1 and per-class precision / recall / F1.

    Balanced accuracy averages recall over the classes that actually occur in `y_true`;
    macro-F1 averages over all three classes. A class with no true cases or no
    predictions scores 0 rather than raising."""
    truth = np.asarray(y_true, dtype=object)
    pred = np.asarray(y_pred, dtype=object)
    scores: dict[str, float] = {"n": len(truth), "accuracy": float((truth == pred).mean())}

    recalls, f1s = [], []
    for cls in CLASSES:
        tp = float(((truth == cls) & (pred == cls)).sum())
        actual = float((truth == cls).sum())
        predicted = float((pred == cls).sum())
        precision = tp / predicted if predicted else 0.0
        recall = tp / actual if actual else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        key = cls.lower()
        scores[f"precision_{key}"] = precision
        scores[f"recall_{key}"] = recall
        scores[f"f1_{key}"] = f1
        f1s.append(f1)
        if actual:
            recalls.append(recall)

    scores["balanced_accuracy"] = float(np.mean(recalls)) if recalls else 0.0
    scores["macro_f1"] = float(np.mean(f1s))
    return scores


def score_table(predictions: pd.DataFrame) -> pd.DataFrame:
    """One row of scores per model column in a `compare_models` result."""
    models = [c for c in predictions.columns if c != "y_true"]
    return pd.DataFrame(
        {m: score_predictions(predictions["y_true"], predictions[m]) for m in models}
    ).T


def confusion(y_true, y_pred) -> pd.DataFrame:
    """Counts with true classes down the rows and predicted classes across, in a fixed order."""
    truth = pd.Categorical(np.asarray(y_true, dtype=object), categories=CLASSES)
    pred = pd.Categorical(np.asarray(y_pred, dtype=object), categories=CLASSES)
    table = pd.crosstab(truth, pred, dropna=False)
    table.index.name, table.columns.name = "true", "predicted"
    return table.reindex(index=CLASSES, columns=CLASSES, fill_value=0)


def mcnemar_exact(y_true, pred_a, pred_b, *, positive_class: str | None = None) -> dict:
    """McNemar's exact test on paired correctness (PROPOSAL.md §18.4).

    By default a month is "correct" when the 3-class prediction equals the truth. With
    `positive_class` (e.g. "Down", the priority class) it is correct when the model
    rightly says that class or rightly says it is not that class. The test looks only at
    the months where exactly one model is right: under no difference those split 50/50,
    and the exact two-sided binomial gives the p-value."""
    truth = np.asarray(y_true, dtype=object)
    a = np.asarray(pred_a, dtype=object)
    b = np.asarray(pred_b, dtype=object)
    if positive_class is None:
        a_ok, b_ok = a == truth, b == truth
    else:
        actual = truth == positive_class
        a_ok, b_ok = (a == positive_class) == actual, (b == positive_class) == actual

    a_only = int((a_ok & ~b_ok).sum())
    b_only = int((~a_ok & b_ok).sum())
    discordant = a_only + b_only
    p_value = 1.0
    if discordant:
        p_value = float(binomtest(min(a_only, b_only), discordant, 0.5).pvalue)
    return {
        "a_only_correct": a_only,
        "b_only_correct": b_only,
        "n_discordant": discordant,
        "p_value": p_value,
    }
