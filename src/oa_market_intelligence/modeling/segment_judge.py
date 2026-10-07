"""Tools for judging Task A beyond the headline scores (Step 9).

Calibration, wins by month, improvement by segment size, the High or Low trade-off between real
flips caught and stable segments wrongly flipped, permutation importance by feature group,
the tie-break among promoted models, the exclusion re-runs, and the pass/fail rules fixed in
the plan before any of this was run.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import spearmanr
from sklearn.metrics import f1_score, roc_auc_score

from oa_market_intelligence.features.segment_task import build_task_a_rows
from oa_market_intelligence.modeling.segment_eval import (
    CLIP,
    HIGHER_IS_BETTER,
    METRICS,
    log_loss_per_visit,
    mae_per_share,
    pooled,
    successes,
)

FEATURE_GROUPS = {
    "own recent share": ["seg_share_lag1", "seg_share_roll3", "seg_margin_lag1", "seg_high_lag1"],
    "own history": ["seg_prior_share", "seg_prior_z", "seg_prior_t"],
    "specialty history": ["specialty_prior_share", "spec_prior_z", "spec_prior_t",
                          "specialty_grouped"],
    "last month volume": ["seg_log_visits_lag1"],
    "market": ["market_share_lag1", "market_change_lag1"],
    "demographics": ["age_ordinal", "gender_FEMALE", "gender_MALE"],
    "calendar": ["month_sin", "month_cos"],
}
SIZE_EDGES = [0, 50, 100, 300, np.inf]
SIZE_LABELS = ["<50", "50-100", "100-300", ">300"]
EXCLUSIONS = {
    "PEDIATRICS": ("specialty", "PEDIATRICS"),
    "COVID months": ("months", (202003, 202005)),
    "2024 dip months": ("months", (202403, 202407)),
}
SLOPE_RANGE = (0.8, 1.2)


def monthly_wins(stats_baseline: pd.DataFrame, stats_model: pd.DataFrame, metric: str):
    """(months where the model is strictly better than the baseline, number of months)."""
    columns = {c: stats_baseline[c].to_numpy() for c in stats_baseline.columns}
    model_columns = {c: stats_model[c].to_numpy() for c in stats_model.columns}
    base, model = METRICS[metric](columns), METRICS[metric](model_columns)
    better = model > base if metric in HIGHER_IS_BETTER else model < base
    return int(better.sum()), int(len(base))


def calibration_table(preds: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    """Predicted against observed share in groups of equal visit volume, ordered by prediction."""
    ordered = preds.sort_values("p").reset_index(drop=True)
    cumulative = ordered["t"].cumsum() - ordered["t"] / 2
    group = np.minimum((cumulative / ordered["t"].sum() * bins).astype(int), bins - 1)
    rows = []
    for _, part in ordered.groupby(group):
        visits = part["t"].sum()
        rows.append(
            {"pred": float((part["p"] * part["t"]).sum() / visits),
             "obs": float(part["z"].sum() / visits), "visits": float(visits), "n": len(part)}
        )
    return pd.DataFrame(rows)


def calibration_slope(preds: pd.DataFrame) -> dict:
    """Slope and intercept of a visit-weighted logistic regression of the outcome on the logit
    of the prediction. A calibrated model has slope 1 and intercept 0; a slope under 1 means the
    predictions are more spread out than the truth (overconfident)."""
    p = np.clip(preds["p"].to_numpy(float), CLIP, 1 - CLIP)
    z, t = preds["z"].to_numpy(float), preds["t"].to_numpy(float)
    exog = sm.add_constant(np.log(p / (1 - p)))
    fit = sm.GLM(np.column_stack([z, t - z]), exog, family=sm.families.Binomial()).fit()
    return {"intercept": float(fit.params[0]), "slope": float(fit.params[1])}


def bucket_improvement(baseline: pd.DataFrame, model: pd.DataFrame) -> pd.DataFrame:
    """Log-loss and unweighted share error by the segment's visits last month, for a baseline
    and a model scored on the same rows, with the improvement (positive means the model wins)."""
    visits_last = np.expm1(baseline["seg_log_visits_lag1"].to_numpy(float))
    bucket = pd.cut(visits_last, SIZE_EDGES, right=False, labels=SIZE_LABELS)
    rows = []
    for label in SIZE_LABELS:
        mask = np.asarray(bucket == label)
        z, t = baseline["z"].to_numpy()[mask], baseline["t"].to_numpy()[mask]
        base_loss = log_loss_per_visit(z, t, baseline["p"].to_numpy()[mask])
        model_loss = log_loss_per_visit(z, t, model["p"].to_numpy()[mask])
        base_mae = mae_per_share(z, t, baseline["p"].to_numpy()[mask], weighted=False)
        model_mae = mae_per_share(z, t, model["p"].to_numpy()[mask], weighted=False)
        rows.append({"bucket": label, "n": int(mask.sum()), "baseline": base_loss,
                     "model": model_loss, "improvement": base_loss - model_loss,
                     "mae_baseline": base_mae, "mae_model": model_mae})
    return pd.DataFrame(rows)


def serve_with_tie_break(
    promoted: list[str],
    stats: dict[str, pd.DataFrame],
    draws: dict[str, np.ndarray],
    *,
    order: tuple[str, ...] = ("logistic", "gbm", "rf"),
    lower_percentile: float = 1.67,
) -> dict:
    """Among promoted models serve the simplest unless a more complex one is clearly better.

    A is the promoted model with the lowest pooled log-loss. Going from the simplest model up,
    the first model B that A is not clearly better than serves (A is clearly better than B when
    the `lower_percentile` point of loss(B) - loss(A) over the bootstrap draws is above zero)."""
    if not promoted:
        return {"serving": None, "reason": "no promoted model", "comparisons": {}}
    best = min(promoted, key=lambda name: pooled(stats[name], "log_loss"))
    comparisons = {}
    for name in sorted(promoted, key=order.index):
        if name == best:
            return {"serving": best, "reason": f"{best} has the lowest loss and no simpler "
                    "promoted model is as good", "comparisons": comparisons}
        gap = draws[name] - draws[best]
        low = float(np.percentile(gap[~np.isnan(gap)], lower_percentile))
        comparisons[name] = low
        if low <= 0:
            return {"serving": name, "reason": f"{best} is not clearly better than the simpler "
                    f"{name}", "comparisons": comparisons}
    return {"serving": best, "reason": "lowest loss", "comparisons": comparisons}


def a2_extras(frame: pd.DataFrame) -> dict:
    """High or Low beyond balanced accuracy: ROC AUC and F1 for High, how many real flips the
    prediction catches, how many stable segments it wrongly flips, and the net accuracy gain
    over persistence (the number that counts both)."""
    actual_high = (frame["actual"] == "High").to_numpy()
    score = frame["p"].to_numpy(float) - frame["market_share_lag1"].to_numpy(float)
    flips = (frame["actual"] != frame["last_month"]).to_numpy()
    predicted_flip = (frame["predicted"] != frame["last_month"]).to_numpy()
    correct = (frame["predicted"] == frame["actual"]).to_numpy()
    persistence_correct = (frame["last_month"] == frame["actual"]).to_numpy()
    return {
        "auc_high": float(roc_auc_score(actual_high, score)),
        "f1_high": float(f1_score(actual_high, (frame["predicted"] == "High").to_numpy())),
        "flips_caught": float(correct[flips].mean()) if flips.any() else float("nan"),
        "stable_wrongly_flipped": float(predicted_flip[~flips].mean())
        if (~flips).any() else float("nan"),
        "accuracy": float(correct.mean()),
        "persistence_accuracy": float(persistence_correct.mean()),
        "net_accuracy_gain": float(correct.mean() - persistence_correct.mean()),
    }


def rank_agreement(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation over the index the two series share."""
    common = a.index.intersection(b.index)
    return float(spearmanr(a[common], b[common])[0])


def exclude_and_build(seg: pd.DataFrame, name: str, *, scheme: str = "interval") -> pd.DataFrame:
    """The Task A rows with one of the known data problems excluded. A specialty is removed
    before the dataset is built, so the market share is recomputed without it; months are removed
    from the finished rows (training and scoring), so lag features of later months stay as they
    are."""
    if name == "baseline":
        return build_task_a_rows(seg, scheme=scheme)
    kind, value = EXCLUSIONS[name]
    if kind == "specialty":
        return build_task_a_rows(seg[seg["specialty_name"] != value], scheme=scheme)
    rows = build_task_a_rows(seg, scheme=scheme)
    low, high = value
    return rows[~rows["month_id"].between(low, high)].reset_index(drop=True)


def permutation_importance(
    fit: Callable,
    train: pd.DataFrame,
    test: pd.DataFrame,
    visible: pd.DataFrame,
    *,
    n_repeats: int = 5,
    seed: int = 0,
) -> pd.DataFrame:
    """How much the log-loss per visit rises when one group of inputs is shuffled across the
    test rows, for a model fitted once on `train`. `visible` is `test` without outcomes."""
    predict = fit(train)
    z, t = successes(test), test["y_visits"].to_numpy(float)
    base = log_loss_per_visit(z, t, predict(visible))
    rng = np.random.default_rng(seed)
    rows = []
    for group, columns in FEATURE_GROUPS.items():
        present = [c for c in columns if c in visible.columns]
        increases = []
        for _ in range(n_repeats):
            order = rng.permutation(len(visible))
            shuffled = visible.copy()
            shuffled[present] = visible[present].iloc[order].to_numpy()
            increases.append(log_loss_per_visit(z, t, predict(shuffled)) - base)
        rows.append({"group": group, "importance": float(np.mean(increases)),
                     "spread": float(np.std(increases))})
    return pd.DataFrame(rows).set_index("group")


def judge_rules(
    *,
    summary: dict,
    mae_model: tuple[float, float],
    mae_last_month: tuple[float, float],
    ba_summary: dict,
    slope: float,
) -> dict[str, bool]:
    """The four rules from the plan, for one run. `mae_*` are (visit-weighted, unweighted)."""
    return {
        "J1": summary["low"] > 0,
        "J2": mae_model[0] < mae_last_month[0] and mae_model[1] < mae_last_month[1],
        "J3": ba_summary["low"] > 0,
        "J4": SLOPE_RANGE[0] <= slope <= SLOPE_RANGE[1],
    }
