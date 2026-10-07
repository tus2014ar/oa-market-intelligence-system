"""The Phase 4 results the website and the Claude tools display, computed in the publish step.

Every number here comes from the same tested functions the notebooks use. The results are
computed once per publish, made strictly JSON-safe, and stored in the same database file as the
tables (a `serving_results` key-value table), so a version of the site can never show new tables
with old results and nothing is fitted when a page loads.

`PRECISIONS["full"]` uses the draws and tree counts of the notebooks; `PRECISIONS["fast"]` uses
small ones for tests and quick checks, and is recorded in every stored result.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from oa_market_intelligence.analysis.adoption import (
    bootstrap_adjusted_shares,
    combine_intervals,
    deviance_shares,
    group_specialties,
    load_adoption_data,
    split_half_stability,
)
from oa_market_intelligence.analysis.decomposition import (
    bootstrap_decomposition,
    decompose,
    decompose_chain,
    year_windows,
)
from oa_market_intelligence.analysis.sensitivity import run_sensitivity, share_series
from oa_market_intelligence.analysis.trend import (
    CATEGORIES,
    bootstrap_break_locations,
    category_shares,
    events_overlapping,
    select_breaks,
)
from oa_market_intelligence.features.monthly import (
    compute_monthly_features,
    load_monthly_gold,
    model_ready_monthly,
)
from oa_market_intelligence.features.segment import load_segment_gold
from oa_market_intelligence.features.segment_task import build_task_a_rows
from oa_market_intelligence.modeling.baselines import (
    MajorityClassBaseline,
    PersistenceBaseline,
    SeasonalBaseline,
    StratifiedRandomBaseline,
)
from oa_market_intelligence.modeling.direction_models import make_gbm, make_random_forest
from oa_market_intelligence.modeling.evaluation import (
    bootstrap_difference,
    compare_models,
    in_sample_scores,
    mcnemar_exact,
    score_table,
    simulate_scores,
)
from oa_market_intelligence.modeling.forecast import (
    RidgeForecaster,
    ets_forecaster,
    load_share_series,
    naive_forecaster,
    run_task_b,
    seasonal_naive_forecaster,
)
from oa_market_intelligence.modeling.models import make_logistic_regression
from oa_market_intelligence.modeling.monitoring import (
    RULES,
    detection_experiment,
    empirical_false_alarm,
    hit_series,
    interval_misses,
    random_guess_line,
    rolling_accuracy,
    stable_threshold,
    window_alarm,
)
from oa_market_intelligence.modeling.power import mcnemar_power, smallest_detectable_gain
from oa_market_intelligence.modeling.segment_eval import (
    log_loss_per_visit,
    mae_per_share,
)
from oa_market_intelligence.modeling.segment_judge import (
    a2_extras,
    bucket_improvement,
    calibration_slope,
    calibration_table,
    monthly_wins,
    permutation_importance,
    serve_with_tie_break,
)
from oa_market_intelligence.modeling.segment_models import fitter_for
from oa_market_intelligence.modeling.segment_task_run import a2_frame, run_a1, run_a2
from oa_market_intelligence.serving.model_panel import serving_decision

PRECISIONS = {
    "full": {
        "n_null": 1000, "n_boot_trend": 1000, "n_boot_decomp": 1000, "n_boot_adopt": 1000,
        "n_boot_stability": 200, "n_boot_sens": 300, "n_boot_sens_stability": 100,
        "a_n_boot": 2000, "b_n_boot": 2000, "chance_runs": 1000, "power_sims": 5000,
        "detection_seeds": 20, "monitor_boot": 2000, "block_sensitivity": True,
        "tree_overrides": {}, "direction_trees": {"rf": 300, "gbm": 100},
    },
    "fast": {
        "n_null": 49, "n_boot_trend": 20, "n_boot_decomp": 20, "n_boot_adopt": 10,
        "n_boot_stability": 10, "n_boot_sens": 8, "n_boot_sens_stability": 8,
        "a_n_boot": 40, "b_n_boot": 40, "chance_runs": 100, "power_sims": 500,
        "detection_seeds": 2, "monitor_boot": 100, "block_sensitivity": False,
        "tree_overrides": {"gbm": {"max_iter": 30}, "rf": {"n_estimators": 30}},
        "direction_trees": {"rf": 20, "gbm": 20},
    },
}
WINDOW = 6
FORECAST_HISTORY = 48


# ---------------------------------------------------------------- helpers and storage


def clean(value):
    """Make a value strictly JSON-safe: plain types, NaN and infinity to None."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return clean(value.to_dict(orient="records"))
    if isinstance(value, pd.Series):
        return clean(value.to_dict())
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m")
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else float(value)
    return value


def next_month_id(month_id: int) -> int:
    year, month = divmod(int(month_id), 100)
    return (year + 1) * 100 + 1 if month == 12 else year * 100 + month + 1


def store_result(engine: Engine, key: str, payload: dict, *, precision: str) -> None:
    """Save one result under `key`, replacing any earlier one."""
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = {**clean(payload), "precision": precision, "built_at": built_at}
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS serving_results "
                "(key TEXT PRIMARY KEY, built_at TEXT NOT NULL, precision TEXT NOT NULL, "
                "payload TEXT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT OR REPLACE INTO serving_results (key, built_at, precision, payload) "
                "VALUES (:key, :built_at, :precision, :payload)"
            ),
            {"key": key, "built_at": built_at, "precision": precision,
             "payload": json.dumps(body, allow_nan=False)},
        )


def load_result(engine: Engine, key: str) -> dict | None:
    """The stored result for `key`, or None if this database has none."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT payload FROM serving_results WHERE key = :key"), {"key": key}
            ).fetchone()
    except Exception:  # noqa: BLE001 - a database without the table simply has no results
        return None
    return json.loads(row[0]) if row else None


def monitoring_status(direction_flag: bool, forecast_alarm: bool) -> dict:
    """'review' if either monitor has a flag, with a plain-language banner."""
    parts = []
    if direction_flag:
        parts.append(
            "The served direction classifier's rolling six-month accuracy is at or below its "
            "review threshold."
        )
    if forecast_alarm:
        parts.append(
            "At least 3 of the last 6 months fell outside the forecast's 90% interval."
        )
    return {"status": "review" if parts else "ok", "banner": " ".join(parts) or None}


# ---------------------------------------------------------------- Q1 and Q3 findings


def _top_group_rows(frame: pd.DataFrame, n: int = 8) -> list[dict]:
    ordered = frame.assign(_abs=frame["total"].abs()).sort_values("_abs", ascending=False)
    return ordered.head(n).drop(columns="_abs").to_dict(orient="records")


def compute_findings_from_data(
    adoption: pd.DataFrame, settings: dict, categories: pd.DataFrame | None = None
) -> dict:
    """Trend, decomposition, specialty adoption and robustness verdicts (Q1 and Q3).

    `adoption` has one row per month, specialty, age band and gender with Zilretta and category
    visit counts; `categories` (optional) is `category_shares` output, for the other two
    categories' breaks."""
    months = sorted(int(m) for m in adoption["month_id"].unique())
    series = share_series(adoption, months)["share"].to_numpy() * 100

    fit = select_breaks(series, n_null=settings["n_null"], seed=0)
    locations = bootstrap_break_locations(
        series, fit.n_breaks, n_boot=settings["n_boot_trend"], seed=0
    )
    intervals = [[months[lo], months[hi]] for lo, hi in locations]
    edges = [0, *fit.breaks, len(series)]
    segments = [
        {"from": months[a], "to": months[b - 1], "mean_pct": float(series[a:b].mean()),
         "slope_pp_per_year": float(np.polyfit(np.arange(a, b), series[a:b], 1)[0] * 12)}
        for a, b in zip(edges, edges[1:])
    ]
    trend = {
        "fitted": fit.fitted, "n_breaks": fit.n_breaks,
        "break_months": [months[b] for b in fit.breaks], "intervals": intervals,
        "p_values": list(fit.p_values), "segments": segments,
        "events_overlap": [events_overlapping(tuple(i)) for i in intervals],
        "block_sensitivity": [], "other_categories": {},
    }
    if settings["block_sensitivity"]:
        for block in (3, 12):
            other = select_breaks(series, n_null=settings["n_null"], block_length=block, seed=0)
            trend["block_sensitivity"].append(
                {"block": block, "n_breaks": other.n_breaks,
                 "break_months": [months[b] for b in other.breaks],
                 "p_values": list(other.p_values)}
            )
    if categories is not None:
        for name in CATEGORIES:
            if name == "branded_injectable":
                continue
            other = select_breaks(
                categories[name].to_numpy() * 100, n_null=settings["n_null"], seed=0
            )
            trend["other_categories"][name] = {
                "n_breaks": other.n_breaks, "p_values": list(other.p_values),
                "break_months": [months[b] for b in other.breaks],
            }

    counts = (
        adoption.groupby(["month_id", "specialty"])[["zilretta", "category"]].sum().reset_index()
        .rename(columns={"specialty": "group"})
    )
    windows = year_windows(months)
    chain = decompose_chain(counts, windows)
    pairs = {"first_to_last": (0, len(windows) - 1)}
    if len(windows) >= 4:
        pairs["third_to_last"] = (2, len(windows) - 1)
    bootstrap = {
        label: bootstrap_decomposition(
            counts, windows[i], windows[j], n_boot=settings["n_boot_decomp"], seed=0
        )
        for label, (i, j) in pairs.items()
    }
    focus = pairs.get("third_to_last", pairs["first_to_last"])
    specialties = decompose(
        counts[counts["month_id"].isin(windows[focus[0]])],
        counts[counts["month_id"].isin(windows[focus[1]])],
    )["by_group"]
    granularity = {}
    for label, keys in (("age_gender", ["age_band", "gender"]),
                        ("segment", ["specialty", "age_band", "gender"])):
        grouped = adoption.assign(group=adoption[keys].astype(str).agg("|".join, axis=1))
        sub = grouped.groupby(["month_id", "group"])[["zilretta", "category"]].sum().reset_index()
        last = decompose_chain(sub, windows).iloc[-1]
        granularity[label] = {"mix": last["mix"], "rate": last["rate"], "total": last["total"]}
    last_specialty = chain.iloc[-1]
    granularity["specialty"] = {"mix": last_specialty["mix"], "rate": last_specialty["rate"],
                                "total": last_specialty["total"]}
    decomposition = {
        "windows": [[w[0], w[-1]] for w in windows], "chain": chain.to_dict(orient="records"),
        "bootstrap": bootstrap, "focus": f"{focus[0] + 1} to {focus[1] + 1}",
        "top_specialties": _top_group_rows(specialties), "granularity": granularity,
    }

    grouped = group_specialties(adoption)
    deviance = deviance_shares(grouped)
    n_adopt = settings["n_boot_adopt"]
    by_month = bootstrap_adjusted_shares(grouped, by="month", n_boot=n_adopt, seed=0)
    by_segment = bootstrap_adjusted_shares(grouped, by="segment", n_boot=n_adopt, seed=0)
    combined = combine_intervals(by_month["table"], by_segment["table"])
    overall = float(grouped["zilretta"].sum() / grouped["category"].sum())
    volume = grouped.groupby("specialty")["category"].sum() / grouped["category"].sum() * 100
    table = []
    for specialty, row in combined.iterrows():
        position = (
            "clearly above" if row["low"] > overall
            else "clearly below" if row["high"] < overall else "not clearly different"
        )
        table.append(
            {"specialty": specialty, "adjusted_share": row["estimate"], "low": row["low"],
             "high": row["high"], "visit_share_pct": volume[specialty], "position": position}
        )
    stability = split_half_stability(grouped, n_boot=settings["n_boot_stability"], seed=0)
    halves = [months[: len(months) // 2], months[len(months) // 2:]]
    overall_halves = [
        float(grouped[grouped["month_id"].isin(h)]["zilretta"].sum()
              / grouped[grouped["month_id"].isin(h)]["category"].sum())
        for h in halves
    ]
    stability_rows = [
        {"specialty": name, "first_half": row["first_half"], "second_half": row["second_half"],
         "flipped": bool((row["first_half"] > overall_halves[0])
                         != (row["second_half"] > overall_halves[1]))}
        for name, row in stability["table"].iterrows()
    ]
    adoption_out = {
        "overall_share": overall, "table": table,
        "deviance": {
            "specialty": deviance["specialty"]["share"], "age_band": deviance["age_band"]["share"],
            "gender": deviance["gender"]["share"], "dispersion": deviance["dispersion"],
        },
        "interval_ratio_median": float(
            ((by_segment["table"]["high"] - by_segment["table"]["low"])
             / (by_month["table"]["high"] - by_month["table"]["low"])).median()
        ),
        "stability": {
            "spearman": stability["spearman"], "low": stability["spearman_low"],
            "high": stability["spearman_high"], "pearson_logit": stability["pearson_logit"],
            "same_side_fraction": stability["same_side_fraction"], "table": stability_rows,
            "overall_first_half": overall_halves[0], "overall_second_half": overall_halves[1],
        },
    }

    sensitivity = run_sensitivity(
        adoption, n_null=settings["n_null"], n_boot=settings["n_boot_sens"],
        n_boot_stability=settings["n_boot_sens_stability"], seed=0,
    )
    runs = {
        name: {
            "n_months": run["n_months"], "break_months": run["trend"]["break_months"],
            "p_first": run["trend"]["p_first"],
            "rate_over_mix": abs(run["decomposition"]["first_to_last"]["rate"])
            / max(abs(run["decomposition"]["first_to_last"]["mix"]), 1e-12),
            "specialty_share": run["adoption"]["specialty_share"],
            "spearman": run["adoption"]["spearman"],
        }
        for name, run in sensitivity["runs"].items()
    }
    return clean(
        {"months": months, "series_pct": series, "trend": trend, "decomposition": decomposition,
         "adoption": adoption_out,
         "robustness": {"verdicts": sensitivity["verdicts"], "runs": runs}}
    )


# ---------------------------------------------------------------- segment model (Task A)


def _scores_to_dict(frame: pd.DataFrame) -> dict:
    return {str(i): {c: frame.loc[i, c] for c in frame.columns} for i in frame.index}


def compute_segment_model_from_data(
    seg: pd.DataFrame,
    settings: dict,
    *,
    trained: tuple[str, ...] = ("logistic", "gbm", "rf"),
    min_train: int = 24,
) -> dict:
    """Task A: scores, the serving decision, calibration, improvement by size, High or Low."""
    rows = build_task_a_rows(seg)
    two = build_task_a_rows(seg, scheme="two_label")
    a1 = run_a1(
        rows, trained=trained, min_train_months=min_train, n_boot=settings["a_n_boot"], seed=0,
        overrides=settings["tree_overrides"],
    )
    a2 = run_a2(a1, two, min_train_months=min_train, n_boot=settings["a_n_boot"], seed=0)
    tie = serve_with_tie_break(a1["decision"]["promoted"], a1["stats"], a1["draws"])
    serving = tie["serving"] or a1["best_baseline"]
    best = a1["best_baseline"]
    preds = a1["preds"]

    wins = {}
    for name in trained:
        wins[name] = {
            metric: list(monthly_wins(a1["stats"][best], a1["stats"][name], metric))
            for metric in ("log_loss", "mae_weighted", "mae")
        }
    ct = calibration_table(preds[serving])
    slope = calibration_slope(preds[serving])
    calibration = {
        "table": ct, "slope": slope["slope"], "intercept": slope["intercept"],
        "mean_pred": float(np.average(ct["pred"], weights=ct["visits"])),
        "mean_obs": float(np.average(ct["obs"], weights=ct["visits"])),
    }
    by_size = bucket_improvement(preds[best], preds[serving])

    pm, pb = preds[serving], preds[best]
    by_specialty = []
    for specialty, index in pm.groupby("specialty_name").groups.items():
        m, b = pm.loc[index], pb.loc[index]
        by_specialty.append(
            {"specialty": specialty, "visits": float(m["t"].sum()),
             "obs_share": float(m["z"].sum() / m["t"].sum()),
             "pred_share": float((m["p"] * m["t"]).sum() / m["t"].sum()),
             "mae_model": mae_per_share(m["z"], m["t"], m["p"]),
             "mae_baseline": mae_per_share(b["z"], b["t"], b["p"]),
             "log_loss_gain": log_loss_per_visit(b["z"], b["t"], b["p"])
             - log_loss_per_visit(m["z"], m["t"], m["p"])}
        )
    importance = {}
    for name in [n for n in trained if n in ("logistic", "gbm")]:
        parts = []
        for entry in [e for e in a1["tuning"][name] if e["retuned"]]:
            test_month = entry["test_month"]
            train, test = rows[rows["month_id"] < test_month], rows[rows["month_id"] == test_month]
            visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
            fit = fitter_for(name, entry["config"], overrides=settings["tree_overrides"].get(name))
            scored = permutation_importance(fit, train, test, visible, n_repeats=5, seed=0)
            parts.append(scored["importance"])
        importance[name] = pd.concat(parts, axis=1).mean(axis=1).to_dict()

    a2_scores = _scores_to_dict(a2["scores"])
    a2_extras_by_model = {
        name: a2_extras(a2_frame(preds[name], two)) for name in ("last_month", *trained)
    }
    a2_summaries = [
        {"model": model, "metric": metric, **summary}
        for (model, metric), summary in a2["summaries"].items()
    ]
    scores = a1["scores"]
    return clean(
        {
            "n_test_months": int(preds[best]["month_id"].nunique()),
            "n_test_rows": int(len(preds[best])),
            "n_prediction_rows": int(len(rows)), "trained": list(trained),
            "scores": _scores_to_dict(scores), "best_baseline": best,
            "summaries": a1["summaries"],
            "decision": {**a1["decision"], "serving": serving, "tie_break": tie["reason"]},
            "wins": wins, "calibration": calibration, "by_size": by_size,
            "by_specialty": by_specialty, "importance": importance,
            "a2": {"scores": a2_scores, "best_baseline": a2["best_baseline"],
                   "summaries": a2_summaries, "extras": a2_extras_by_model},
        }
    )


# ---------------------------------------------------------------- forecast (Task B)


def _forecaster_for(name: str):
    return {
        "last_month": naive_forecaster, "same_month_last_year": seasonal_naive_forecaster,
        "ets_damped": ets_forecaster(seasonal=False), "ets_seasonal": ets_forecaster(seasonal=True),
        "ridge": RidgeForecaster(),
    }[name]


def compute_forecast_from_series(series: pd.Series, settings: dict) -> dict:
    """Task B: scores, the decision, the next month's forecast with intervals, recent history."""
    result = run_task_b(series, n_boot=settings["b_n_boot"], block_length=6, seed=0)
    serving = result["decision"]["serving"]
    next_id = next_month_id(int(series.index[-1]))
    try:
        point = _forecaster_for(serving)(series, next_id)
    except Exception:  # noqa: BLE001 - fall back to the baseline forecast, flagged
        point = {**naive_forecaster(series, next_id), "fallback": True}
    next_month = {"month_id": next_id, "model": serving,
                  **{k: point[k] for k in ("point", "lo80", "hi80", "lo90", "hi90", "fallback")}}
    frame = result["forecasts"][serving]
    columns = ["month_id", "actual", "point", "lo80", "hi80", "lo90", "hi90"]
    history = frame[columns].tail(FORECAST_HISTORY)
    sensitivity = []
    if settings["block_sensitivity"]:
        for block in (3, 12):
            other = run_task_b(series, n_boot=settings["b_n_boot"], block_length=block, seed=0)
            sensitivity.append({"block": block, "promoted": other["decision"]["promoted"],
                                "serving": other["decision"]["serving"]})
    return clean(
        {
            "n_test_months": int(len(frame)), "scores": _scores_to_dict(result["scores"]),
            "best_baseline": result["best_baseline"], "summaries": result["summaries"],
            "decision": result["decision"], "fallbacks": result["fallbacks"],
            "next_month": next_month, "history": history, "block_sensitivity": sensitivity,
        }
    )


# ---------------------------------------------------------------- direction (Q2)


def compute_direction(ready: pd.DataFrame, settings: dict) -> dict:
    """Q2: the direction panel with random forest and gradient boosting, and the power table."""
    trees = settings["direction_trees"]
    models = {
        "always-majority": MajorityClassBaseline, "persistence": PersistenceBaseline,
        "seasonal": SeasonalBaseline, "logistic": make_logistic_regression,
        "rf": lambda: make_random_forest(trees["rf"]), "gbm": lambda: make_gbm(trees["gbm"]),
    }
    preds = compare_models(ready, models, min_train=24)
    scores = score_table(preds)
    chance = simulate_scores(
        ready, lambda s: (lambda: StratifiedRandomBaseline(s)), range(settings["chance_runs"]),
        min_train=24,
    )
    p95 = float(chance["balanced_accuracy"].quantile(0.95))
    decision = serving_decision(scores[["balanced_accuracy"]], chance_p95=p95)
    versus = {}
    for name in ("logistic", "rf", "gbm", "seasonal"):
        boot = bootstrap_difference(
            preds["y_true"], preds[name], preds["persistence"], metric="balanced_accuracy",
            n_boot=settings["b_n_boot"], seed=0, level=0.95, block_length=3,
        )
        mc = mcnemar_exact(preds["y_true"], preds[name], preds["persistence"])
        versus[name] = {"difference": boot["point"], "low": boot["lo"], "high": boot["hi"],
                        "only_model_right": mc["a_only_correct"],
                        "only_persistence_right": mc["b_only_correct"], "p_value": mc["p_value"]}
    in_sample = {
        name: float(in_sample_scores(ready, make, min_train=24)["train_balanced_accuracy"].mean())
        for name, make in (("logistic", make_logistic_regression), ("rf", models["rf"]),
                           ("gbm", models["gbm"]))
    }
    hp = hit_series(preds["y_true"], preds["persistence"])
    hs = hit_series(preds["y_true"], preds["seasonal"])
    accuracy, discordance = float(hp.mean()), float((hp != hs).mean())
    power = mcnemar_power(
        n=len(preds), accuracy=accuracy, discordance=discordance,
        deltas=np.round(np.arange(0.02, 0.31, 0.02), 2), n_sim=settings["power_sims"], seed=0,
    )
    predictions = preds.reset_index().rename(columns={preds.index.name or "index": "month_id"})
    return clean(
        {
            "n_test": int(len(preds)), "label_counts": preds["y_true"].value_counts().to_dict(),
            "scores": _scores_to_dict(scores[["accuracy", "balanced_accuracy", "macro_f1",
                                              "recall_down", "recall_up"]]),
            "chance": {"mean": float(chance["balanced_accuracy"].mean()), "p95": p95,
                       "p05": float(chance["balanced_accuracy"].quantile(0.05))},
            "decision": decision, "versus_persistence": versus, "in_sample": in_sample,
            "power": {"persistence_accuracy": accuracy, "discordance": discordance,
                      "table": power, "smallest_detectable_gain": smallest_detectable_gain(power)},
            "predictions": predictions,
        }
    )


# ---------------------------------------------------------------- monitoring (Q4)


def compute_monitoring_from(
    backtest: pd.DataFrame,
    served_direction: str,
    forecast_frame: pd.DataFrame,
    settings: dict,
    series: pd.Series | None = None,
) -> dict:
    """Q4: the direction hit series with its review threshold, and the forecast-interval alarm.

    `backtest` has `y_true` and a column per classifier (indexed by test month); `forecast_frame`
    is the served forecast's walk-forward frame with its 80% and 90% interval columns."""
    hits = hit_series(backtest["y_true"], backtest[served_direction])
    threshold = stable_threshold(
        hits, window=WINDOW, block_length=3, n_boot=settings["monitor_boot"], seed=0
    )
    rolling = rolling_accuracy(hits, WINDOW)
    guess = random_guess_line(
        backtest["y_true"].to_numpy(), window=WINDOW, n_runs=settings["chance_runs"], seed=0
    )
    # a flag means "at or below the review threshold AND worse than the classifier's own
    # average"; a perfect record has its threshold at the ceiling and must not be flagged
    flag = bool(rolling[-1] <= threshold["threshold"] and rolling[-1] < threshold["match_rate"])
    direction = {
        "model": served_direction, "match_rate": threshold["match_rate"],
        "rate_low": threshold["rate_low"], "rate_high": threshold["rate_high"],
        "threshold": threshold["threshold"], "random_line": guess["line"],
        "latest_rolling": float(rolling[-1]), "flag": flag,
        "windows_at_or_below": int((rolling <= threshold["threshold"]).sum()),
        "n_windows": int(len(rolling)),
        "rolling": [{"month_id": int(m), "accuracy": float(a)}
                    for m, a in zip(backtest.index[WINDOW - 1:], rolling)],
    }

    rule = RULES["R90"]
    misses = interval_misses(forecast_frame, rule["level"]).to_numpy()
    recent = misses[-rule["n"]:]
    alarms = window_alarm(misses, k=rule["k"], n=rule["n"])
    forecast = {
        "rule": "R90", "level": rule["level"], "k": rule["k"], "n": rule["n"],
        "misses_in_window": int(recent.sum()), "alarm": bool(recent.sum() >= rule["k"]),
        "recent": [{"month_id": int(m), "outside": bool(o)}
                   for m, o in zip(forecast_frame["month_id"].tail(rule["n"]), recent)],
        "backtest_misses": int(misses.sum()), "backtest_months": int(len(misses)),
        "backtest_alarm_windows": int(alarms.sum()), "n_windows": int(len(alarms)),
        "false_alarm_rate": empirical_false_alarm(
            misses, k=rule["k"], n=rule["n"], block_length=3,
            n_boot=settings["monitor_boot"], seed=0,
        ),
    }
    detection = None
    if series is not None and settings["detection_seeds"] > 0:
        table = detection_experiment(series, n_seeds=settings["detection_seeds"], seed=0)
        detection = table.to_dict(orient="records")
    status = monitoring_status(flag, forecast["alarm"])
    return clean({"direction": direction, "forecast": forecast, "detection": detection, **status})


# ---------------------------------------------------------------- everything


def compute_all(engine: Engine, precision: str = "full") -> dict[str, dict]:
    """Every stored result for the current database, at the chosen precision."""
    settings = PRECISIONS[precision]
    series = load_share_series(engine)
    findings = compute_findings_from_data(
        load_adoption_data(engine), settings, category_shares(engine)
    )
    segment = compute_segment_model_from_data(load_segment_gold(engine), settings)
    forecast = compute_forecast_from_series(series, settings)
    ready = model_ready_monthly(compute_monthly_features(load_monthly_gold(engine)))
    direction = compute_direction(ready, settings)
    backtest = pd.DataFrame(direction["predictions"]).set_index("month_id")
    monitoring = compute_monitoring_from(
        backtest, direction["decision"]["serving"], pd.DataFrame(forecast["history"]), settings,
        series=series,
    )
    results = {"findings": findings, "segment_model": segment, "forecast": forecast,
               "direction": direction, "monitoring": monitoring}
    for payload in results.values():
        payload["precision"] = precision
    return results
