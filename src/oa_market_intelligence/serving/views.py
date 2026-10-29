"""What the site draws, as tested functions: frames for tables and charts, and the sentences.

The audience is a brand manager, so wording is plain and every forecast is shown with its caveats.
Each function takes the frames from `data` (or the stored result dicts) and fits nothing.
"""

from __future__ import annotations

import pandas as pd

HORIZONS = (1, 3, 6)
TASK_NAMES = {"T1": "National share", "T2": "Segment share", "T3": "Decline alert"}
TASK_KEY = {"T1": "national", "T2": "segment", "T3": "alert"}
METRIC_TEXT = {
    "mae_pp": "average miss (points of share)",
    "log_loss": "penalty score (log-loss)",
    "pr_auc": "ranking score (PR-AUC)",
}
EVENTS = [
    (202203, "Share turns: a break is detected around March 2022"),
    (202403, "Data-capture dip begins (March to July 2024)"),
]
DIP_MONTHS = (202403, 202407)
METHOD_TEXT = {
    "last_month": "same as last month",
    "same_month_last_year": "same month last year",
    "logistic": "logistic regression",
    "gbm": "gradient boosting",
    "rf": "random forest",
    "ridge": "ridge trend line",
    "ets_damped": "trend and season model",
    "ets_seasonal": "trend and season model (seasonal)",
    "small_segment": "segment size only",
    "recent_drop": "recent drop",
    "always_alert": "always alert",
}


def method_text(name: str) -> str:
    return METHOD_TEXT.get(name, str(name).replace("_", " "))


def month_label(month_id: int) -> str:
    return pd.Timestamp(year=int(month_id) // 100, month=int(month_id) % 100, day=1).strftime(
        "%b %Y"
    )


def pct(value: float, digits: int = 2) -> str:
    return f"{100 * value:.{digits}f}%"


# ------------------------------------------------------------------ overview


def headline(monthly: pd.DataFrame, pred: pd.DataFrame) -> dict:
    """The few numbers at the top of the page."""
    latest = monthly.iloc[-1]
    peak = monthly.loc[monthly["visit_share"].idxmax()]
    national = pred[(pred["level"] == "national") & (pred["horizon"] == 1)]
    out = {
        "latest_month": month_label(latest["month_id"]),
        "latest_share": float(latest["visit_share"]),
        "peak_month": month_label(peak["month_id"]),
        "peak_share": float(peak["visit_share"]),
        "change_since_peak": float(latest["visit_share"] / peak["visit_share"] - 1),
        "months_of_data": int(len(monthly)),
        "total_visits": int(monthly[["zilretta", "corticosteroid", "nsaid_otc"]].sum().sum()),
    }
    if len(national):
        row = national.iloc[0]
        out["next_month"] = month_label(row["target_month_id"])
        out["next_month_forecast"] = float(row["point_share"])
        out["next_month_range90"] = (float(row["lo90_share"]), float(row["hi90_share"]))
    return out


def use_and_dont(adoption: dict) -> list[dict]:
    """What to use each forecast for, from the adoption map (DL-79)."""
    seg = adoption["segment"]
    used = [f"{h} month{'s' if h != '1' else ''}" for h, m in seg.items() if m != "last_month"]
    seg_used = " and ".join(used)
    return [
        {
            "what": "National share forecast",
            "use": "Plan on last month's value, with its likely range",
            "dont": "Do not expect a model to beat it: none did at 1, 3 or 6 months",
        },
        {
            "what": "Segment forecast",
            "use": f"A small improvement at {seg_used}, read as optimistic (it runs high)",
            "dont": "Do not plan six months ahead on it, or read it as a prediction of change",
        },
        {
            "what": "Decline alert",
            "use": "A supporting flag at 1 month only",
            "dont": "Never act on an alert alone: about half of the alerts are not real declines",
        },
    ]


# ------------------------------------------------------------------ market trend


def trend_frame(monthly: pd.DataFrame) -> pd.DataFrame:
    out = monthly[["month_id", "month", "visit_share"]].copy()
    out["share_pct"] = 100 * out["visit_share"]
    out["in_dip"] = out["month_id"].between(*DIP_MONTHS)
    return out


def event_frame(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = [
        (pd.to_datetime(str(m), format="%Y%m"), text)
        for m, text in EVENTS
        if m in set(monthly["month_id"])
    ]
    peak = monthly.loc[monthly["visit_share"].idxmax()]
    rows.append(
        (peak["month"], f"Peak: {pct(peak['visit_share'])} in {month_label(peak['month_id'])}")
    )
    return (
        pd.DataFrame(rows, columns=["month", "label"]).sort_values("month").reset_index(drop=True)
    )


def yearly_table(monthly: pd.DataFrame) -> pd.DataFrame:
    year = monthly["month_id"] // 100
    grouped = monthly.groupby(year).agg(
        zilretta=("zilretta", "sum"),
        corticosteroid=("corticosteroid", "sum"),
        nsaid_otc=("nsaid_otc", "sum"),
        months=("month_id", "count"),
    )
    grouped["share_pct"] = (
        100 * grouped["zilretta"] / grouped[["zilretta", "corticosteroid", "nsaid_otc"]].sum(axis=1)
    )
    out = grouped.reset_index().rename(columns={"month_id": "year"})
    out["partial_year"] = out["months"] < 12
    return out


def trend_note(monthly: pd.DataFrame) -> str:
    h = headline(monthly, pd.DataFrame(columns=["level", "horizon"]))
    return (
        f"Zilretta's share of Zilretta, generic steroid injection and NSAID/OTC visits peaked at "
        f"{pct(h['peak_share'])} in {h['peak_month']} and was {pct(h['latest_share'])} in "
        f"{h['latest_month']}, {abs(100 * h['change_since_peak']):.0f}% below the peak. "
        "March to July 2024 is a data-capture dip that hit all three classes, so read those "
        "months with care."
    )


# ------------------------------------------------------------------ segments


def specialty_frame(seg: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    out = seg.sort_values("share", ascending=False).copy()
    out["share_pct"] = 100 * out["share"]
    out["small_segment"] = out["category_visits"] < 5000
    return out.head(top) if top else out


def specialty_trend(by_year: pd.DataFrame, specialties: list[str]) -> pd.DataFrame:
    sub = by_year[by_year["specialty"].isin(specialties)].copy()
    sub["share_pct"] = 100 * sub["share"]
    return sub.sort_values(["specialty", "year"])


# ------------------------------------------------------------------ forecasts


def national_forecast(pred: pd.DataFrame) -> pd.DataFrame:
    rows = pred[pred["level"] == "national"].sort_values("horizon")
    out = pd.DataFrame(
        {
            "horizon": rows["horizon"].to_numpy(),
            "month": [month_label(m) for m in rows["target_month_id"]],
            "forecast_pct": 100 * rows["point_share"].to_numpy(),
            "low_90_pct": 100 * rows["lo90_share"].to_numpy(),
            "high_90_pct": 100 * rows["hi90_share"].to_numpy(),
            "model": rows["model"].to_numpy(),
        }
    )
    out["months_ahead"] = out["horizon"]
    return out


def known_bias(pred: pd.DataFrame) -> dict[int, float | None]:
    """The bias recorded in testing for each horizon's segment model (None for a baseline)."""
    seg = pred[pred["level"] == "segment"]
    out = {}
    for horizon, part in seg.groupby("horizon"):
        values = part["known_bias"].dropna()
        out[int(horizon)] = float(values.iloc[0]) if len(values) else None
    return out


def bias_sentence(pred: pd.DataFrame) -> str:
    """The tested bias of the segment forecasts, in words."""
    parts = [
        f"{100 * value:.1f}% at {horizon} month{'s' if horizon != 1 else ''}"
        for horizon, value in known_bias(pred).items()
        if value is not None
    ]
    return "Segment forecasts run high: " + " and ".join(parts) + " in testing." if parts else ""


def coverage_sentence(scores: pd.DataFrame) -> str:
    """How often the national forecast's 90% range held in testing."""
    rows = scores[
        (scores["task"] == "T1") & (scores["scenario"] == "main") & (scores["serving"] == 1)
    ].sort_values("horizon")
    parts = []
    for r in rows.itertuples():
        if pd.notna(r.coverage_90):
            where = f"at {r.horizon} month{'s' if r.horizon != 1 else ''}"
            parts.append(
                f"{round(100 * r.coverage_90)}% {'of months ' if not parts else ''}{where}"
            )
    if not parts:
        return ""
    joined = ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
    return f"In testing, the 90% range contained what happened in {joined}."


def specialty_forecast(pred: pd.DataFrame) -> pd.DataFrame:
    """The segment forecasts rolled up to specialties, weighted by each segment's visits at the
    origin, one column per horizon."""
    seg = pred[pred["level"] == "segment"].copy()
    seg["weighted"] = seg["point_share"] * seg["origin_visits"]
    grouped = seg.groupby(["specialty_name", "horizon"]).agg(
        weighted=("weighted", "sum"), visits=("origin_visits", "sum")
    )
    grouped["share_pct"] = 100 * grouped["weighted"] / grouped["visits"]
    wide = grouped["share_pct"].unstack("horizon")
    wide.columns = [f"{h}_month_pct" for h in wide.columns]
    visits = (
        grouped["visits"].xs(1, level="horizon")
        if 1 in grouped.index.get_level_values("horizon")
        else None
    )
    if visits is not None:
        wide["visits_at_origin"] = visits
    return (
        wide.reset_index()
        .rename(columns={"specialty_name": "specialty"})
        .sort_values("visits_at_origin", ascending=False)
        .reset_index(drop=True)
    )


def segment_forecast(pred: pd.DataFrame, specialty: str | None = None) -> pd.DataFrame:
    seg = pred[pred["level"] == "segment"]
    if specialty:
        seg = seg[seg["specialty_name"] == specialty]
    wide = seg.pivot_table(
        index=["specialty_name", "age_band", "gender"], columns="horizon", values="point_share"
    )
    wide.columns = [f"{h}_month_pct" for h in wide.columns]
    wide = (100 * wide).reset_index().rename(columns={"specialty_name": "specialty"})
    visits = seg[seg["horizon"] == 1].set_index(["specialty_name", "age_band", "gender"])[
        "origin_visits"
    ]
    wide["visits_at_origin"] = [
        visits.get((s, a, g))
        for s, a, g in zip(wide["specialty"], wide["age_band"], wide["gender"], strict=True)
    ]
    return wide.sort_values("visits_at_origin", ascending=False).reset_index(drop=True)


def alert_frame(pred: pd.DataFrame) -> pd.DataFrame:
    rows = pred[(pred["level"] == "segment") & (pred["horizon"] == 1) & pred["alert_flag"].notna()]
    out = rows[
        ["specialty_name", "age_band", "gender", "alert_score", "alert_flag", "origin_visits"]
    ]
    out = out.rename(columns={"specialty_name": "specialty"})
    return out.sort_values("alert_score", ascending=False).reset_index(drop=True)


def alert_summary(alerts: pd.DataFrame, scores: pd.DataFrame) -> dict:
    precision = scores[
        (scores["task"] == "T3")
        & (scores["horizon"] == 1)
        & (scores["scenario"] == "main")
        & (scores["serving"] == 1)
    ]["precision"]
    return {
        "scored": int(len(alerts)),
        "flagged": int((alerts["alert_flag"] == 1).sum()),
        "precision": float(precision.iloc[0]) if len(precision) else None,
    }


# ------------------------------------------------------------------ model results


def adopted_model(adoption: dict, task: str, horizon: int) -> str | None:
    return adoption.get(TASK_KEY[task], {}).get(str(horizon))


def _t3_reliable(posthoc: dict | None, horizon: int) -> bool | None:
    if not posthoc:
        return None
    row = posthoc["horizons"].get(str(horizon))
    return None if row is None else bool(row["lower_at_registered_percentile"] > 0)


def verdict(
    task: str, horizon: int, best: pd.Series, serving: pd.Series, posthoc: dict | None
) -> str:
    if task == "T1":
        return "No model beat the simple rule"
    if task == "T2":
        if not bool(serving["promoted"]) or serving["kind"] == "baseline":
            return "No model beat the simple rule reliably"
        gain = serving["gain_over_best_baseline"] / best["score"]
        return f"Small gain over the simple rule: about {pct(gain, 1)} better"
    reliable = _t3_reliable(posthoc, horizon)
    if reliable is None:
        return "Passes the registered test; read with the caveat below"
    return (
        'A reliable gain over a one-line "it just spiked" rule'
        if reliable
        else "Passes the registered test, but not reliably better than a one-line "
        '"it just spiked" rule'
    )


def scoreboard(scores: pd.DataFrame, adoption: dict, posthoc: dict | None = None) -> pd.DataFrame:
    """One row per task and horizon (main run): the simple rule, the model the registered rule
    chose, whether it is adopted for use (DL-79) and what that means in plain words."""
    main = scores[scores["scenario"] == "main"]
    rows = []
    for task in ("T1", "T2", "T3"):
        for horizon in HORIZONS:
            part = main[(main["task"] == task) & (main["horizon"] == horizon)]
            if part.empty:
                continue
            best = part[part["is_best_baseline"] == 1].iloc[0]
            serving = part[part["serving"] == 1].iloc[0]
            adopted = adopted_model(adoption, task, horizon)
            rows.append(
                {
                    "task": TASK_NAMES[task],
                    "horizon": horizon,
                    "measure": METRIC_TEXT[best["metric"]],
                    "simple_rule": method_text(best["method"]),
                    "simple_rule_score": float(best["score"]),
                    "chosen_model": method_text(serving["method"]),
                    "chosen_model_score": float(serving["score"]),
                    "adopted": adopted is not None,
                    "adopted_model": method_text(adopted) if adopted else "",
                    "verdict": verdict(task, horizon, best, serving, posthoc),
                }
            )
    return pd.DataFrame(rows)


def bias_table(scores: pd.DataFrame) -> pd.DataFrame:
    seg = scores[
        (scores["task"] == "T2")
        & (scores["scenario"] == "main")
        & (scores["kind"] == "model")
        & scores["bias_vs_actual"].notna()
    ]
    out = seg[seg["method"] == "logistic"][["horizon", "bias_vs_actual"]].copy()
    out["forecast_above_actual_pct"] = 100 * out["bias_vs_actual"]
    return out.sort_values("horizon").reset_index(drop=True)[
        ["horizon", "forecast_above_actual_pct"]
    ]


def posthoc_table(posthoc: dict) -> pd.DataFrame:
    rows = []
    for horizon, r in sorted(posthoc["horizons"].items(), key=lambda kv: int(kv[0])):
        rows.append(
            {
                "horizon": int(horizon),
                "decline_share": r["decline_share"],
                "registered_simple_rule": r["pr_auc_registered_recent_drop"],
                "corrected_simple_rule": r["pr_auc_flipped_recent_drop"],
                "model": r["pr_auc_logistic"],
                "gain_over_corrected": r["gain_over_flipped"],
                "reliable": bool(r["lower_at_registered_percentile"] > 0),
            }
        )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ data status


def status_block(run: dict) -> dict:
    """Freshness, drift, versions and the stages of the last publish run, as plain text."""
    monitoring = run.get("monitoring") or {}
    fresh = monitoring.get("freshness") or {}
    drift = monitoring.get("drift") or {}
    out = {
        "run_status": run.get("status", "unknown"),
        "finished_at": run.get("finished_at"),
        "months": run.get("n_months"),
        "last_month_id": run.get("last_month_id"),
        "freshness": fresh.get("status"),
        "age_days": fresh.get("age_days"),
        "max_age_days": fresh.get("max_age_days"),
        "drift": drift.get("status"),
        "versions": run.get("versions") or {},
    }
    stale = fresh.get("status") == "stale"
    out["banner"] = (
        f"The latest month of data is {month_label(run['last_month_id'])}, about "
        f"{fresh.get('age_days')} "
        "days ago. No newer extract has arrived, so the forecasts below are for months that "
        "have already "
        "passed and should be read as a method and a baseline, not as a current view."
        if stale and run.get("last_month_id")
        else None
    )
    out["drift_text"] = {
        "nothing_to_compare": (
            "No month has arrived since the previous forecasts, so there is nothing to check yet."
        ),
        "ok": "Earlier forecasts are holding up within the tested range.",
        "drift": "Earlier forecasts have drifted beyond the tested range.",
        "no_previous_run": "There are no earlier forecasts to compare.",
    }.get(drift.get("status"), "Not available")
    return out


def stages_frame(run: dict) -> pd.DataFrame:
    stages = run.get("stages") or []
    return pd.DataFrame(
        [{"stage": s["stage"], "status": s["status"], "seconds": s["seconds"]} for s in stages]
    )


def extracts_frame(run: dict) -> pd.DataFrame:
    files = {}
    for item in run.get("ingest_files") or []:
        files.setdefault(item["file_name"], item["sha256"])
    return pd.DataFrame(
        [{"extract": name, "sha256_start": digest[:12]} for name, digest in files.items()]
    )


# ------------------------------------------------------------------ findings


def findings_frame(items: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(items)[["id", "finding", "evidence", "impact", "source"]]
