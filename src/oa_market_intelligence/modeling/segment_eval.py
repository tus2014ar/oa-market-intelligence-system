"""Walk-forward evaluation for Task A: a predicted share for every segment-month.

For each test month t the model is fitted on every row from months before t (24 months
minimum) and predicts every row of month t. The test rows it is given have all outcome
columns (those starting with `y_`) removed, so it cannot see the answer, and a guard raises if
a training row is from month t or later.

Scores are binomial log-loss per visit (primary) and the mean absolute error of the share,
visit-weighted and not. Everything is kept as per-month sums so the month bootstrap (resampling
whole test months, because the rows of a month share the same market conditions) is fast and
the pooled value is exactly what scoring all rows at once would give. For the High/Low
secondary test the same machinery reports balanced accuracy and accuracy on the *flip subset*,
the rows whose label changed from last month, where persistence is wrong by definition.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np
import pandas as pd

DEFAULT_MIN_TRAIN_MONTHS = 24
CLIP = 1e-6
LOWER_PERCENTILE = 1.67  # Bonferroni for three trained models (plan, DL-41)
HIGHER_IS_BETTER = {"balanced_accuracy", "accuracy", "flip_accuracy"}
CARRIED = ("market_share_lag1", "seg_high_lag1", "y_label", "y_raw_above", "y_market")


class FutureLeakError(RuntimeError):
    """A training row was from the month being predicted, or later."""


def walk_forward_row_splits(
    rows: pd.DataFrame, *, min_train_months: int = DEFAULT_MIN_TRAIN_MONTHS
) -> Iterator[tuple[int, np.ndarray, np.ndarray]]:
    """Yield `(test_month, train_positions, test_positions)`, one test month at a time."""
    months = sorted(rows["month_id"].unique())
    if min_train_months < 1 or min_train_months >= len(months):
        raise ValueError(
            f"min_train_months={min_train_months} leaves no test months out of {len(months)}"
        )
    month = rows["month_id"].to_numpy()
    for test_month in months[min_train_months:]:
        yield test_month, np.flatnonzero(month < test_month), np.flatnonzero(month == test_month)


def successes(rows: pd.DataFrame) -> np.ndarray:
    """Zilretta visits recovered from the share and the category visits."""
    return np.rint(rows["y_share"].to_numpy() * rows["y_visits"].to_numpy())


def walk_forward_predict_rows(
    rows: pd.DataFrame,
    fit_predict: Callable[[pd.DataFrame, pd.DataFrame], np.ndarray],
    *,
    min_train_months: int = DEFAULT_MIN_TRAIN_MONTHS,
    splits: Callable[..., Iterator] | None = None,
    carry: tuple[str, ...] = CARRIED,
) -> pd.DataFrame:
    """Predict every row of every test month.

    `fit_predict(train, test)` gets the training rows (outcomes included, all from earlier
    months) and the test rows with the outcome columns removed, and returns the predicted share
    for each test row. The result holds the prediction `p`, the counts `z` and `t`, and the
    carried columns needed for scoring."""
    make_splits = splits or walk_forward_row_splits
    frames = []
    for test_month, train_idx, test_idx in make_splits(rows, min_train_months=min_train_months):
        train, test = rows.iloc[train_idx], rows.iloc[test_idx]
        if (train["month_id"] >= test_month).any():
            raise FutureLeakError(f"training rows from month {test_month} or later")
        visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
        p = np.asarray(fit_predict(train, visible), dtype=float)
        if len(p) != len(test):
            raise ValueError("fit_predict must return one prediction per test row")
        out = pd.DataFrame(
            {
                "month_id": test["month_id"].to_numpy(),
                "segment": test["segment"].to_numpy(),
                "p": p,
                "z": successes(test),
                "t": test["y_visits"].to_numpy(),
            }
        )
        for column in carry:
            if column in test.columns:
                out[column] = test[column].to_numpy()
        frames.append(out)
    return pd.concat(frames, ignore_index=True)


def _clip(p) -> np.ndarray:
    return np.clip(np.asarray(p, dtype=float), CLIP, 1 - CLIP)


def log_loss_per_visit(z, t, p) -> float:
    """Binomial negative log-likelihood per visit."""
    z, t, p = np.asarray(z, float), np.asarray(t, float), _clip(p)
    return float(-(z * np.log(p) + (t - z) * np.log(1 - p)).sum() / t.sum())


def mae_per_share(z, t, p, *, weighted: bool = True) -> float:
    z, t = np.asarray(z, float), np.asarray(t, float)
    error = np.abs(z / t - np.asarray(p, float))
    return float((t * error).sum() / t.sum()) if weighted else float(error.mean())


def share_month_stats(preds: pd.DataFrame) -> pd.DataFrame:
    """Per test month: the sums that make up the share scores."""
    z, t, p = preds["z"].to_numpy(float), preds["t"].to_numpy(float), _clip(preds["p"])
    error = np.abs(z / t - preds["p"].to_numpy(float))
    frame = pd.DataFrame(
        {
            "month_id": preds["month_id"].to_numpy(),
            "loss_sum": -(z * np.log(p) + (t - z) * np.log(1 - p)),
            "visits": t,
            "abs_w": t * error,
            "abs": error,
            "n": 1.0,
        }
    )
    return frame.groupby("month_id").sum()


def classify_from_share(preds: pd.DataFrame) -> np.ndarray:
    """High if the predicted share is above last month's market-wide share, else Low."""
    return np.where(preds["p"].to_numpy() > preds["market_share_lag1"].to_numpy(), "High", "Low")


def label_month_stats(preds: pd.DataFrame) -> pd.DataFrame:
    """Per test month: hit counts for High, Low, all rows and the flip subset. `preds` needs
    `actual`, `predicted` and `last_month` (the persistence label)."""
    actual, predicted = preds["actual"].to_numpy(), preds["predicted"].to_numpy()
    flip = actual != preds["last_month"].to_numpy()
    hit = actual == predicted
    frame = pd.DataFrame(
        {
            "month_id": preds["month_id"].to_numpy(),
            "high_hit": (hit & (actual == "High")).astype(float),
            "high_n": (actual == "High").astype(float),
            "low_hit": (hit & (actual == "Low")).astype(float),
            "low_n": (actual == "Low").astype(float),
            "hit": hit.astype(float),
            "n": 1.0,
            "flip_hit": (hit & flip).astype(float),
            "flip_n": flip.astype(float),
        }
    )
    return frame.groupby("month_id").sum()


def _ratio(num, den):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.asarray(den) > 0, np.asarray(num) / np.asarray(den), np.nan)


METRICS: dict[str, Callable] = {
    "log_loss": lambda s: _ratio(s["loss_sum"], s["visits"]),
    "mae_weighted": lambda s: _ratio(s["abs_w"], s["visits"]),
    "mae": lambda s: _ratio(s["abs"], s["n"]),
    "balanced_accuracy": lambda s: 0.5
    * (_ratio(s["high_hit"], s["high_n"]) + _ratio(s["low_hit"], s["low_n"])),
    "accuracy": lambda s: _ratio(s["hit"], s["n"]),
    "flip_accuracy": lambda s: _ratio(s["flip_hit"], s["flip_n"]),
}


def pooled(stats: pd.DataFrame, metric: str) -> float:
    """The metric over all test months together."""
    sums = {column: stats[column].sum() for column in stats.columns}
    return float(METRICS[metric](sums))


def best_model(stats_by_model: dict[str, pd.DataFrame], metric: str) -> str:
    """The model with the best pooled value (lowest for losses, highest for accuracies)."""
    sign = -1 if metric in HIGHER_IS_BETTER else 1
    return min(stats_by_model, key=lambda name: sign * pooled(stats_by_model[name], metric))


def month_bootstrap(
    stats_by_model: dict[str, pd.DataFrame], metric: str, *, n_boot: int = 2000, seed: int = 0
) -> dict[str, np.ndarray]:
    """The metric for every model on the same resampled sets of test months (a paired
    bootstrap): each draw picks months with replacement and pools their sums."""
    names = list(stats_by_model)
    index = stats_by_model[names[0]].index
    for name in names:
        if not stats_by_model[name].index.equals(index):
            raise ValueError("every model must be scored on the same test months")
    draws = np.random.default_rng(seed).integers(0, len(index), (n_boot, len(index)))
    out = {}
    for name in names:
        stats = stats_by_model[name]
        sums = {column: stats[column].to_numpy()[draws].sum(axis=1) for column in stats.columns}
        out[name] = np.asarray(METRICS[metric](sums), dtype=float)
    return out


def improvement_summary(
    stats_baseline: pd.DataFrame,
    stats_model: pd.DataFrame,
    draws_baseline: np.ndarray,
    draws_model: np.ndarray,
    metric: str,
    *,
    lower_percentile: float = LOWER_PERCENTILE,
) -> dict:
    """How much better the model is than the baseline, oriented so positive means better:
    the pooled difference and the paired bootstrap interval for it. `low` is the
    `lower_percentile` point, the one the serving rule looks at."""
    higher = metric in HIGHER_IS_BETTER
    if higher:
        improvement = draws_model - draws_baseline
        estimate = pooled(stats_model, metric) - pooled(stats_baseline, metric)
    else:
        improvement = draws_baseline - draws_model
        estimate = pooled(stats_baseline, metric) - pooled(stats_model, metric)
    valid = improvement[~np.isnan(improvement)]
    low, high = np.percentile(valid, [lower_percentile, 100 - lower_percentile])
    return {
        "estimate": float(estimate),
        "low": float(low),
        "high": float(high),
        "share_positive": float((valid > 0).mean()),
        "n_valid": int(len(valid)),
    }
