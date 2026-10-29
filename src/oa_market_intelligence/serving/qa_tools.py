"""The read-only tools Claude may call when it answers a question on the site (DL-86).

Claude never sees SQL, a database connection or a patient-level row. It can ask for six named
summaries; each one is built from the same tested functions the site's tabs use (`views`), over a
`Snapshot` of the aggregate frames the page has already loaded. Every tool returns a small,
JSON-safe dict, and anything that goes wrong comes back as {"error": ...} for Claude to read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.serving import data, views

MAX_ROWS = 40
MAX_TOP = 25
LIMITS = [
    "The data are IQVIA NMTA visit counts, aggregated: there are no patient-level rows.",
    "Payer, regional and prescription-volume data are not part of this analysis.",
    "Company sales rose while IQVIA visits fell, so the size of the visit decline is not "
    "confirmed as a market fact.",
    "March to July 2024 is a data-capture dip that hit all three drug classes.",
]
SEGMENT_GROUPS = ("specialty", "age_band", "gender")
FORECAST_VIEWS = ("national", "specialty", "alerts")


@dataclass
class Snapshot:
    """Everything the tools read: the aggregate frames and the stored results."""

    monthly: pd.DataFrame
    pred: pd.DataFrame
    scores: pd.DataFrame
    by_specialty: pd.DataFrame
    by_age: pd.DataFrame
    by_gender: pd.DataFrame
    run: dict = field(default_factory=dict)
    adoption: dict = field(default_factory=dict)
    posthoc: dict = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)


def load_snapshot(engine: Engine, run_log=data.RUN_LOG) -> Snapshot:
    """The snapshot the site builds, for scripts and tests that have only an engine."""
    return Snapshot(
        monthly=data.market_monthly(engine),
        pred=data.predictions(engine),
        scores=data.model_scores(engine),
        by_specialty=data.segments(engine, "specialty"),
        by_age=data.segments(engine, "age_band"),
        by_gender=data.segments(engine, "gender"),
        run=data.last_run(run_log),
        adoption=data.serving_map(),
        posthoc=data.posthoc(),
        findings=data.findings(),
    )


TOOL_DEFINITIONS = [
    {
        "name": "get_data_status",
        "description": (
            "The headline numbers (latest share, peak, change since the peak, the next-month "
            "forecast with its range), how fresh the data are, and the limits of the analysis."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_market_trend",
        "description": (
            "Zilretta's share of visits (Zilretta over Zilretta, generic corticosteroid and "
            "NSAID/OTC visits together), by month or by year. Months are numbers like 202112."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_month_id": {"type": "integer", "description": "First month, e.g. 202101"},
                "end_month_id": {"type": "integer", "description": "Last month, e.g. 202312"},
                "by_year": {"type": "boolean", "description": "Yearly totals instead of months"},
            },
        },
    },
    {
        "name": "get_segments",
        "description": (
            "Zilretta's share of visits over all months for each specialty, age band or gender "
            "(groups with at least 1,000 category visits), largest share first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "by": {"type": "string", "enum": list(SEGMENT_GROUPS)},
                "top": {"type": "integer", "description": f"How many groups (up to {MAX_TOP})"},
            },
            "required": ["by"],
        },
    },
    {
        "name": "get_forecasts",
        "description": (
            "The monthly forecasts. view=national: the national share 1, 3 and 6 months ahead "
            "with the 90% range. view=specialty: the segment forecasts rolled up to specialties, "
            "with the bias recorded in testing. view=alerts: the 1-month decline-alert list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": list(FORECAST_VIEWS)},
                "top": {"type": "integer", "description": f"How many rows (up to {MAX_TOP})"},
            },
            "required": ["view"],
        },
    },
    {
        "name": "get_model_results",
        "description": (
            "How each forecast scored in testing against a simple rule, which model is used "
            "and what to use or not use each forecast for, plus the bias and the post-hoc check "
            "on the decline alert."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_findings",
        "description": (
            "The 17 numbered findings with their evidence and business impact. Optionally "
            "filter by a word that appears in the finding, evidence or impact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
        },
    },
]


# ------------------------------------------------------------------ helpers


def _clean(value: Any) -> Any:
    """Make a value JSON-safe: numpy to Python, NaN and inf to None, tuples to lists."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m")
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, 4)
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _records(frame: pd.DataFrame, cap: int = MAX_ROWS) -> list[dict]:
    return _clean(frame.head(cap).to_dict(orient="records"))


def _int_or_none(arguments: dict, key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be a whole number")
    return value


def _top(arguments: dict, default: int = 10) -> int:
    top = _int_or_none(arguments, "top") or default
    return max(1, min(top, MAX_TOP))


# ------------------------------------------------------------------ the tools


def _data_status(snap: Snapshot, arguments: dict) -> dict:
    head = views.headline(snap.monthly, snap.pred)
    status = views.status_block(snap.run)
    out = {
        "latest_month": head["latest_month"],
        "latest_share_pct": round(100 * head["latest_share"], 2),
        "peak_month": head["peak_month"],
        "peak_share_pct": round(100 * head["peak_share"], 2),
        "change_since_peak_pct": round(100 * head["change_since_peak"], 1),
        "months_of_data": head["months_of_data"],
        "freshness": status["freshness"],
        "age_days": status["age_days"],
        "stale_banner": status["banner"],
        "last_run_status": status["run_status"],
        "monitoring": status["drift_text"],
        "limits": LIMITS,
    }
    if "next_month" in head:
        low, high = head["next_month_range90"]
        out["next_month"] = head["next_month"]
        out["next_month_forecast_pct"] = round(100 * head["next_month_forecast"], 2)
        out["next_month_range90_pct"] = [round(100 * low, 2), round(100 * high, 2)]
    return _clean(out)


def _market_trend(snap: Snapshot, arguments: dict) -> dict:
    start = _int_or_none(arguments, "start_month_id")
    end = _int_or_none(arguments, "end_month_id")
    monthly = snap.monthly
    if start is not None:
        monthly = monthly[monthly["month_id"] >= start]
    if end is not None:
        monthly = monthly[monthly["month_id"] <= end]
    if monthly.empty:
        return {"error": "No months in that range.", "data_covers": _span(snap.monthly)}
    if arguments.get("by_year"):
        table = views.yearly_table(monthly)
        rows = table[["year", "zilretta", "months", "share_pct", "partial_year"]]
        rows = rows.rename(columns={"zilretta": "zilretta_visits"})
        return {"by": "year", "rows": _records(rows), "note": views.trend_note(snap.monthly)}
    frame = views.trend_frame(monthly)
    rows = frame[["month_id", "share_pct", "in_dip"]].copy()
    rows["month"] = [views.month_label(m) for m in rows["month_id"]]
    rows["zilretta_visits"] = monthly["zilretta"].to_numpy()
    return {
        "by": "month",
        "rows": _records(rows[["month", "month_id", "zilretta_visits", "share_pct", "in_dip"]], 80),
        "note": views.trend_note(snap.monthly),
    }


def _span(monthly: pd.DataFrame) -> str:
    return f"{views.month_label(monthly['month_id'].iloc[0])} to " + views.month_label(
        monthly["month_id"].iloc[-1]
    )


def _segments(snap: Snapshot, arguments: dict) -> dict:
    by = arguments.get("by")
    frames = {"specialty": snap.by_specialty, "age_band": snap.by_age, "gender": snap.by_gender}
    if by not in frames:
        return {"error": f"by must be one of {list(SEGMENT_GROUPS)}"}
    table = views.specialty_frame(frames[by], _top(arguments))
    rows = table[["group", "zilretta_visits", "category_visits", "share_pct", "small_segment"]]
    return {
        "by": by,
        "period": _span(snap.monthly),
        "rows": _records(rows),
        "note": "Shares over all months. Groups under 5,000 category visits are marked small "
        "and are noisy.",
    }


def _forecasts(snap: Snapshot, arguments: dict) -> dict:
    view = arguments.get("view")
    if view not in FORECAST_VIEWS:
        return {"error": f"view must be one of {list(FORECAST_VIEWS)}"}
    if view == "national":
        table = views.national_forecast(snap.pred)
        rows = table[
            ["months_ahead", "month", "forecast_pct", "low_90_pct", "high_90_pct", "model"]
        ].copy()
        rows["model"] = [views.method_text(m) for m in rows["model"]]
        return {
            "view": view,
            "rows": _records(rows),
            "coverage_in_testing": views.coverage_sentence(snap.scores),
            "note": "The national forecast is the simple rule 'same as last month'; no model "
            "beat it.",
        }
    if view == "specialty":
        table = views.specialty_forecast(snap.pred)
        return {
            "view": view,
            "rows": _records(table.head(_top(arguments))),
            "bias": views.bias_sentence(snap.pred),
            "note": "Shares in percent, weighted by visits at the forecast origin. Only the "
            "1 and 3 month segment forecasts use a model; 6 months is last month's share.",
        }
    alerts = views.alert_frame(snap.pred)
    summary = views.alert_summary(alerts, snap.scores)
    flagged = alerts[alerts["alert_flag"] == 1]
    return {
        "view": view,
        "summary": _clean(summary),
        "rows": _records(flagged.head(_top(arguments))),
        "note": "1-month alert only: a supporting flag, never a reason to act alone.",
    }


def _model_results(snap: Snapshot, arguments: dict) -> dict:
    board = views.scoreboard(snap.scores, snap.adoption, snap.posthoc)
    return {
        "scoreboard": _records(board),
        "use_and_dont": _clean(views.use_and_dont(snap.adoption)),
        "segment_bias": _records(views.bias_table(snap.scores)),
        "alert_post_hoc_check": _records(views.posthoc_table(snap.posthoc)),
        "decision": snap.adoption.get("decision"),
    }


def _findings(snap: Snapshot, arguments: dict) -> dict:
    keyword = str(arguments.get("keyword") or "").strip().lower()
    frame = views.findings_frame(snap.findings)
    if keyword:
        text = frame[["finding", "evidence", "impact"]].agg(" ".join, axis=1).str.lower()
        frame = frame[text.str.contains(keyword, regex=False)]
    return {"count": int(len(frame)), "findings": _records(frame)}


TOOLS = {
    "get_data_status": _data_status,
    "get_market_trend": _market_trend,
    "get_segments": _segments,
    "get_forecasts": _forecasts,
    "get_model_results": _model_results,
    "get_findings": _findings,
}


def run_tool(name: str, arguments: dict | None, *, snapshot: Snapshot) -> dict:
    """Run one named tool. Unknown names and bad arguments come back as {"error": ...}."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name!r}. Available: {sorted(TOOLS)}"}
    try:
        return tool(snapshot, arguments or {})
    except ValueError as error:
        return {"error": str(error)}
    except Exception:  # noqa: BLE001 - Claude gets a plain error, never a stack trace
        return {"error": f"{name} could not be answered from the data."}
