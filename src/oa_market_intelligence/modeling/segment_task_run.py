"""Run Task A: baselines and trained models through the walk-forward harness, then judge.

A1 predicts each segment's share and is scored on log-loss per visit; a trained model is
promoted only if the 1.67th percentile of its paired improvement over the best baseline is above
zero (plan, DL-41 and DL-49). A2 turns the same predictions into High or Low and is reported
as supporting evidence only.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from oa_market_intelligence.modeling.segment_baselines import BASELINES, LABEL_BASELINES
from oa_market_intelligence.modeling.segment_eval import (
    best_model,
    classify_from_share,
    improvement_summary,
    label_month_stats,
    month_bootstrap,
    pooled,
    share_month_stats,
    walk_forward_predict_rows,
    walk_forward_row_splits,
)
from oa_market_intelligence.modeling.segment_models import TunedPredictor

SHARE_METRICS = ("log_loss", "mae_weighted", "mae")
LABEL_METRICS = ("balanced_accuracy", "accuracy", "flip_accuracy")


def log_run(path, name: str, params: dict, metrics: dict) -> None:
    """Append a JSON run record; also log to MLflow when it is installed."""
    record = {"name": name, "params": params, "metrics": metrics,
              "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=float) + "\n")
    try:
        import mlflow  # noqa: PLC0415 - optional
    except ImportError:
        return
    with mlflow.start_run(run_name=name):
        mlflow.log_params({k: str(v) for k, v in params.items()})
        mlflow.log_metrics({k: float(v) for k, v in metrics.items()})


def a1_decision(summaries: dict, pooled_loss: dict, best_baseline: str) -> dict:
    """The serving rule: promote every model whose lower bound is above zero; the promoted model
    with the lowest pooled loss serves, otherwise the best baseline serves."""
    promoted = [name for name, summary in summaries.items() if summary["low"] > 0]
    serving = min(promoted, key=lambda name: pooled_loss[name]) if promoted else best_baseline
    reason = (
        f"{serving} beats the best baseline ({best_baseline}) with the lower end of its paired "
        "improvement above zero."
        if promoted
        else f"No trained model clears the rule; the best baseline ({best_baseline}) serves."
    )
    return {"promoted": promoted, "serving": serving, "best_baseline": best_baseline,
            "reason": reason}


def run_a1(
    rows: pd.DataFrame,
    *,
    trained: tuple[str, ...] = ("logistic", "gbm", "rf"),
    min_train_months: int = 24,
    n_boot: int = 2000,
    seed: int = 0,
    overrides: dict | None = None,
    retune_every: int = 6,
    log_path=None,
) -> dict:
    """Walk-forward A1 for every baseline and trained model, with the paired month bootstrap."""
    overrides = overrides or {}
    predictors = dict(BASELINES)
    tuned = {
        name: TunedPredictor(name, overrides=overrides.get(name), retune_every=retune_every)
        for name in trained
    }
    predictors.update(tuned)
    preds = {
        name: walk_forward_predict_rows(rows, predictor, min_train_months=min_train_months)
        for name, predictor in predictors.items()
    }
    stats = {name: share_month_stats(p) for name, p in preds.items()}
    scores = pd.DataFrame(
        {name: {metric: pooled(s, metric) for metric in SHARE_METRICS} for name, s in stats.items()}
    ).T
    best_baseline = best_model({name: stats[name] for name in BASELINES}, "log_loss")
    draws = month_bootstrap(
        {name: stats[name] for name in (best_baseline, *trained)}, "log_loss",
        n_boot=n_boot, seed=seed,
    )
    summaries = {
        name: improvement_summary(
            stats[best_baseline], stats[name], draws[best_baseline], draws[name], "log_loss"
        )
        for name in trained
    }
    decision = a1_decision(summaries, scores["log_loss"].to_dict(), best_baseline)
    if log_path:
        for name in scores.index:
            params = {"model": name}
            if name in tuned:
                params["last_config"] = tuned[name].log[-1]["config"]
            log_run(log_path, f"A1 {name}", params, scores.loc[name].to_dict())
    return {
        "preds": preds, "stats": stats, "scores": scores, "best_baseline": best_baseline,
        "summaries": summaries, "decision": decision, "draws": draws,
        "tuning": {name: predictor.log for name, predictor in tuned.items()},
    }


def a2_frame(preds: pd.DataFrame, two_label_rows: pd.DataFrame) -> pd.DataFrame:
    """A1 predictions turned into High or Low, on the two-label rows (segments with enough
    visits last month), with the actual label and last month's side."""
    actual = two_label_rows[["month_id", "segment", "y_label"]].rename(
        columns={"y_label": "actual"}
    )
    frame = preds.merge(actual, on=["month_id", "segment"], how="inner")
    frame["predicted"] = classify_from_share(frame)
    frame["last_month"] = np.where(frame["seg_high_lag1"] == 1, "High", "Low")
    return frame[["month_id", "segment", "p", "actual", "predicted", "last_month"]]


def label_baseline_frames(two_label_rows: pd.DataFrame, min_train_months: int = 24) -> dict:
    """Walk-forward predictions of the three High or Low baselines on the two-label rows."""
    frames = {name: [] for name in LABEL_BASELINES}
    for _, train_idx, test_idx in walk_forward_row_splits(
        two_label_rows, min_train_months=min_train_months
    ):
        train, test = two_label_rows.iloc[train_idx], two_label_rows.iloc[test_idx]
        for name, rule in LABEL_BASELINES.items():
            frames[name].append(
                pd.DataFrame(
                    {
                        "month_id": test["month_id"].to_numpy(),
                        "segment": test["segment"].to_numpy(),
                        "actual": test["y_label"].to_numpy(),
                        "predicted": rule(train, test),
                        "last_month": np.where(test["seg_high_lag1"] == 1, "High", "Low"),
                    }
                )
            )
    return {name: pd.concat(parts, ignore_index=True) for name, parts in frames.items()}


def run_a2(
    a1: dict,
    two_label_rows: pd.DataFrame,
    *,
    min_train_months: int = 24,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict:
    """High or Low from the A1 predictions, against the three label baselines."""
    frames = label_baseline_frames(two_label_rows, min_train_months)
    derived = {f"A1 {name}": a2_frame(p, two_label_rows) for name, p in a1["preds"].items()}
    all_frames = {**frames, **derived}
    stats = {name: label_month_stats(frame) for name, frame in all_frames.items()}
    scores = pd.DataFrame(
        {name: {m: pooled(s, m) for m in LABEL_METRICS} for name, s in stats.items()}
    ).T
    best_baseline = best_model({name: stats[name] for name in LABEL_BASELINES},
                               "balanced_accuracy")
    summaries = {}
    trained = [f"A1 {name}" for name in a1["summaries"]]
    for metric in ("balanced_accuracy", "flip_accuracy"):
        draws = month_bootstrap(
            {name: stats[name] for name in (best_baseline, *trained)}, metric,
            n_boot=n_boot, seed=seed,
        )
        for name in trained:
            summaries[(name, metric)] = improvement_summary(
                stats[best_baseline], stats[name], draws[best_baseline], draws[name], metric
            )
    return {"scores": scores, "best_baseline": best_baseline, "summaries": summaries,
            "stats": stats}

