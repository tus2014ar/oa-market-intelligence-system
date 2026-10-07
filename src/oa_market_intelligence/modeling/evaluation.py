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


def score_predictions(y_true, y_pred, *, undefined: float = 0.0) -> dict[str, float]:
    """Accuracy, balanced accuracy, macro-F1 and per-class precision / recall / F1.

    Balanced accuracy averages recall over the classes that actually occur in `y_true`;
    macro-F1 averages over all three classes, counting an empty class as 0. A per-class
    score that is undefined (recall with no true cases, precision with no predictions, F1
    with neither) is reported as `undefined`: 0 by default, or NaN when the caller wants to
    tell "undefined" from "scored zero"."""
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
        scores[f"precision_{key}"] = precision if predicted else undefined
        scores[f"recall_{key}"] = recall if actual else undefined
        scores[f"f1_{key}"] = f1 if (actual or predicted) else undefined
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


def _resample_positions(n: int, block_length: int, rng: np.random.Generator) -> np.ndarray:
    """Positions of one bootstrap sample of `n` months. `block_length` 1 resamples months
    independently; longer blocks resample runs of consecutive months, which respects any
    month-to-month dependence."""
    if block_length < 1 or block_length > n:
        raise ValueError(f"block_length={block_length} must be between 1 and {n}")
    if block_length == 1:
        return rng.integers(0, n, size=n)
    n_blocks = -(-n // block_length)
    starts = rng.integers(0, n - block_length + 1, size=n_blocks)
    return (starts[:, None] + np.arange(block_length)).ravel()[:n]


def _interval(values: np.ndarray, level: float) -> tuple[float, float, int]:
    valid = values[~np.isnan(values)]
    if len(valid) == 0:
        return float("nan"), float("nan"), 0
    tail = (1 - level) / 2 * 100
    lo, hi = np.percentile(valid, [tail, 100 - tail])
    return float(lo), float(hi), len(valid)


def bootstrap_scores(
    y_true,
    y_pred,
    *,
    n_boot: int = 2000,
    seed: int = 0,
    level: float = 0.95,
    block_length: int = 1,
) -> pd.DataFrame:
    """Percentile bootstrap intervals for every score in `score_predictions`.

    Months are resampled with replacement (in blocks of `block_length` consecutive
    months), the scores recomputed on each resample, and the middle `level` of them kept.
    Per-class scores that are undefined on a resample (for example recall for Down when the
    resample happens to contain no Down month) are left out rather than counted as 0;
    `n_valid` says how many resamples each interval is based on. A point estimate that is
    undefined for the real predictions is NaN."""
    truth = np.asarray(y_true, dtype=object)
    pred = np.asarray(y_pred, dtype=object)
    rng = np.random.default_rng(seed)

    point = score_predictions(truth, pred, undefined=np.nan)
    metrics = [m for m in point if m != "n"]
    draws = np.empty((n_boot, len(metrics)))
    for b in range(n_boot):
        idx = _resample_positions(len(truth), block_length, rng)
        sample = score_predictions(truth[idx], pred[idx], undefined=np.nan)
        draws[b] = [sample[m] for m in metrics]

    rows = {}
    for j, metric in enumerate(metrics):
        lo, hi, n_valid = _interval(draws[:, j], level)
        rows[metric] = {"point": point[metric], "lo": lo, "hi": hi, "n_valid": n_valid}
    return pd.DataFrame(rows).T


def bootstrap_difference(
    y_true,
    pred_a,
    pred_b,
    *,
    metric: str = "recall_down",
    n_boot: int = 2000,
    seed: int = 0,
    level: float = 0.95,
    block_length: int = 1,
) -> dict[str, float]:
    """Bootstrap interval for `metric(A) - metric(B)`, resampling the same months for both
    models so the comparison is paired. `share_positive` is the share of valid resamples
    in which A beat B. Resamples where either score is undefined are left out."""
    truth = np.asarray(y_true, dtype=object)
    a = np.asarray(pred_a, dtype=object)
    b = np.asarray(pred_b, dtype=object)
    rng = np.random.default_rng(seed)

    point = (
        score_predictions(truth, a, undefined=np.nan)[metric]
        - score_predictions(truth, b, undefined=np.nan)[metric]
    )
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        idx = _resample_positions(len(truth), block_length, rng)
        diffs[i] = (
            score_predictions(truth[idx], a[idx], undefined=np.nan)[metric]
            - score_predictions(truth[idx], b[idx], undefined=np.nan)[metric]
        )
    lo, hi, n_valid = _interval(diffs, level)
    valid = diffs[~np.isnan(diffs)]
    share_positive = float((valid > 0).mean()) if len(valid) else float("nan")
    return {
        "point": float(point),
        "lo": lo,
        "hi": hi,
        "share_positive": share_positive,
        "n_valid": n_valid,
    }


def simulate_scores(
    frame: pd.DataFrame,
    make_model_for_seed: Callable[[int], Callable[[], object]],
    seeds,
    *,
    target: str = TARGET,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> pd.DataFrame:
    """Repeat a walk-forward run once per seed and score each run: one row per seed. For a
    random model this is the distribution of scores chance produces, the yardstick for
    whether anything beats luck. `make_model_for_seed(seed)` returns a model factory."""
    seeds = list(seeds)
    rows = []
    for seed in seeds:
        run = walk_forward_predict(
            frame, make_model_for_seed(seed), target=target, min_train=min_train
        )
        rows.append(score_predictions(run["y_true"], run["y_pred"]))
    return pd.DataFrame(rows, index=pd.Index(seeds, name="seed"))
