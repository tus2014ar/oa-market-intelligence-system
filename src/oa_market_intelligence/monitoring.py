"""Monitoring of a publish run (DL-84): is the data fresh, and are earlier forecasts holding up?

The `monitoring` stage of `oa-publish` runs after the scoring stage and before the swap. It reads
the new (staged) database and the previously published one, reports, and never stops the run: a
stale extract or a drifting forecast is something to look at, not a reason to withhold the data.

**Freshness.** The latest month of data should be recent: IQVIA publishes about 40 days after a
month ends and the pipeline runs monthly, so the latest month is `stale` once it is more than
`MAX_AGE_DAYS` (100: the lag, one monthly cycle and a month of slack) old.

**Drift.** The previous run's predictions (`pred_batch` in the previously published database) are
compared with what happened, for every prediction whose target month is now in the data:
- segments: the visit-weighted predicted over observed share minus one (the bias) at each horizon,
  against the bias recorded when the model was tested (`known_bias`); flagged if they differ by
  more than `BIAS_TOLERANCE` (5 percentage points);
- national: the absolute error in points of share against the model's tested error
  (`model_scores`); flagged if it is more than `ERROR_MULTIPLE` (2) times that.
These are monitoring thresholds, not statistical tests. With no new month since the previous run
(which is the case while there is one static extract) there is nothing to compare, and the report
says so.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

MAX_AGE_DAYS = 100
BIAS_TOLERANCE = 0.05
ERROR_MULTIPLE = 2.0
SEGMENT_KEYS = ["specialty_name", "age_band", "gender"]


def month_end(month_id: int) -> date:
    year, month = divmod(int(month_id), 100)
    return date(year, month, calendar.monthrange(year, month)[1])


def freshness(
    last_month_id: int, today: date | None = None, max_age_days: int = MAX_AGE_DAYS
) -> dict:
    """How old the latest month of data is, and whether that is too old."""
    today = today or date.today()
    end = month_end(last_month_id)
    age = (today - end).days
    stale = age > max_age_days
    return {
        "last_month_id": int(last_month_id),
        "last_month_end": end.isoformat(),
        "checked_on": today.isoformat(),
        "age_days": age,
        "max_age_days": max_age_days,
        "status": "stale" if stale else "fresh",
        "note": ("no newer extract has arrived since this month" if stale else "within the limit"),
    }


def _segment_drift(pred: pd.DataFrame, seg: pd.DataFrame, last: int) -> dict:
    out = {}
    rows = pred[(pred["level"] == "segment") & (pred["target_month_id"] <= last)]
    actual = seg.rename(columns={"month_id": "target_month_id"})[
        ["target_month_id", *SEGMENT_KEYS, "branded_injectable_visits", "total_category_visits"]
    ]
    for horizon, part in rows.groupby("horizon"):
        joined = part.merge(actual, on=["target_month_id", *SEGMENT_KEYS], how="inner")
        observed = joined["branded_injectable_visits"].sum()
        if joined.empty or observed <= 0:
            continue
        predicted = (joined["point_share"] * joined["total_category_visits"]).sum()
        realised = float(predicted / observed - 1)
        tested = joined["known_bias"].dropna()
        tested_bias = float(tested.mean()) if len(tested) else None
        drifted = tested_bias is not None and abs(realised - tested_bias) > BIAS_TOLERANCE
        out[str(int(horizon))] = {
            "n_rows": int(len(joined)), "model": str(part["model"].iloc[0]),
            "realised_bias": realised, "tested_bias": tested_bias, "drift": bool(drifted),
        }
    return out


def _national_drift(
    pred: pd.DataFrame, monthly: pd.DataFrame, scores: pd.DataFrame, last: int
) -> dict:
    out = {}
    share = monthly.set_index("month_id")["visit_share"]
    rows = pred[(pred["level"] == "national") & (pred["target_month_id"] <= last)]
    for r in rows.itertuples():
        if int(r.target_month_id) not in share.index:
            continue
        error = abs(float(r.point_share) - float(share.loc[int(r.target_month_id)])) * 100
        tested = scores[(scores["task"] == "T1") & (scores["scenario"] == "main")
                        & (scores["horizon"] == int(r.horizon)) & (scores["method"] == r.model)]
        tested_error = float(tested["score"].iloc[0]) if len(tested) else None
        drifted = tested_error is not None and error > ERROR_MULTIPLE * tested_error
        out[str(int(r.horizon))] = {
            "target_month_id": int(r.target_month_id), "model": r.model, "error_points": error,
            "tested_error_points": tested_error, "drift": bool(drifted),
        }
    return out


def drift_report(
    previous: pd.DataFrame | None,
    seg: pd.DataFrame,
    monthly: pd.DataFrame,
    scores: pd.DataFrame,
) -> dict:
    """Earlier predictions against what has happened since. `previous` is the previously published
    `pred_batch` (or None)."""
    if previous is None or previous.empty:
        return {"status": "no_previous_run", "note": "there are no earlier predictions to compare"}
    last = int(monthly["month_id"].max())
    run_month = int(previous["run_month_id"].iloc[0])
    base = {"previous_run_month_id": run_month, "latest_month_id": last}
    if last <= run_month:
        return {**base, "status": "nothing_to_compare",
                "note": "no month has arrived since the previous predictions were made"}
    segments = _segment_drift(previous, seg, last)
    national = _national_drift(previous, monthly, scores, last)
    flagged = [v["drift"] for v in (*segments.values(), *national.values())]
    status = "nothing_to_compare" if not flagged else ("drift" if any(flagged) else "ok")
    return {**base, "status": status, "segment": segments, "national": national,
            "tolerances": {"bias": BIAS_TOLERANCE, "error_multiple": ERROR_MULTIPLE}}


def _previous_predictions(previous_db: Path | None) -> pd.DataFrame | None:
    if previous_db is None or not Path(previous_db).exists():
        return None
    connection = sqlite3.connect(f"file:{Path(previous_db).as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        if "pred_batch" not in tables:
            return None
        return pd.read_sql("SELECT * FROM pred_batch", connection)
    finally:
        connection.close()


def run_monitoring(engine: Engine, previous_db: Path | None, today: date | None = None) -> dict:
    """The `monitoring` stage: freshness of the staged data and drift of the previous predictions.
    Never raises: a failure is reported as `status: error`. `status` is `ok`, or `attention` when
    the data are stale or a forecast has drifted."""
    from oa_market_intelligence.features.segment import load_segment_gold

    try:
        with engine.connect() as conn:
            monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
            scores = pd.read_sql("SELECT * FROM model_scores", conn)
        fresh = freshness(int(monthly["month_id"].max()), today)
        drift = drift_report(
            _previous_predictions(previous_db), load_segment_gold(engine), monthly, scores
        )
        attention = fresh["status"] == "stale" or drift["status"] == "drift"
        return {"status": "attention" if attention else "ok", "freshness": fresh, "drift": drift}
    except Exception as error:  # noqa: BLE001 - monitoring must not stop a publish
        return {"status": "error", "error": f"{type(error).__name__}: {error}"}
