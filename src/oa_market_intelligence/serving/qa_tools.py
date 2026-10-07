"""The only things Claude can reach from the question box.

Six pre-written, read-only tools that return small aggregate summaries as plain JSON. None
takes SQL and none returns patient-visit-level rows. Three of them read the Phase 4 results
that the publish step stored in the database file; nothing is fitted when a question is asked.
Errors come back as `{"error": ...}` results so the model can say what went wrong; internal
detail is never passed on.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.serving.queries import (
    GROUPINGS,
    data_status,
    market_trend,
    segment_table,
)

MAX_SEGMENT_ROWS = 25
MAX_RESULT_ROWS = 15
FINDINGS_SECTIONS = ("summary", "trend", "decomposition", "specialties", "robustness")
MODEL_TASKS = ("direction", "segment", "forecast", "all")
FINDINGS_CAVEATS = [
    "These are descriptions of what the data shows, not causes: the data cannot see payer, "
    "geography, price or practice mix, so no finding says why a share moved.",
    "Intervals understate the real uncertainty because visits are not independent.",
]
FORECAST_LIMITS = [
    "The served forecast is the last observed month carried forward: no trained model beat it "
    "on the held-out months, so it is a reference range, not a prediction of a change.",
    "The direction models showed no skill beyond chance on the held-out months, and 35 test "
    "months could only detect a large accuracy gain.",
]
_NOT_PUBLISHED = {"error": "That result is not published with this database."}
SEGMENT_CAVEAT = (
    "A ratio above or below 1 is a lead to investigate, not proof of an opportunity: the "
    "data cannot see payer, geography or practice mix, and the intervals understate the "
    "real uncertainty because visits are not independent."
)
_UNAVAILABLE = {"error": "That information is temporarily unavailable."}

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "get_data_status",
        "description": "Months covered and headline totals (Zilretta visits and total "
        "category visits) for the data the site is currently showing.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_market_trend",
        "description": "Monthly Zilretta visit share (Zilretta divided by Zilretta plus "
        "generic corticosteroid plus NSAID visits, osteoarthritis only), its Up/Flat/Down "
        "direction label, and the three category visit counts. Optionally limit the range "
        "with start_month and end_month as YYYYMM numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_month": {"type": "integer", "description": "First month, e.g. 202201"},
                "end_month": {"type": "integer", "description": "Last month, e.g. 202312"},
            },
        },
    },
    {
        "name": "get_segment_table",
        "description": "Zilretta's observed share of the competitive set for each group, "
        "against the share the market-wide trend would predict for that group's volume. "
        "Group by specialty, age_band or gender.",
        "input_schema": {
            "type": "object",
            "properties": {
                "by": {"type": "string", "enum": sorted(GROUPINGS)},
                "top_n": {
                    "type": "integer",
                    "description": f"Rows to return, largest first (max {MAX_SEGMENT_ROWS}).",
                },
            },
            "required": ["by"],
        },
    },
    {
        "name": "get_model_results",
        "description": "How the models scored on walk-forward testing, and which model the "
        "site serves and why. task is direction (monthly Up/Flat/Down, the default), segment "
        "(next-month share of each specialty-age-gender segment), forecast (next-month share "
        "forecast) or all.",
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string", "enum": list(MODEL_TASKS)}},
        },
    },
    {
        "name": "get_findings",
        "description": "The main findings on Zilretta's visit share: when the trend changed "
        "(trend), whether the change comes from specialty mix or from share within specialties "
        "(decomposition), which specialties use Zilretta more or less than the market after "
        "adjusting for age and gender (specialties), and which findings survived robustness "
        "checks (robustness). summary gives the headlines of each.",
        "input_schema": {
            "type": "object",
            "properties": {"section": {"type": "string", "enum": list(FINDINGS_SECTIONS)}},
        },
    },
    {
        "name": "get_forecast_and_monitoring",
        "description": "The next month's forecast of Zilretta's visit share with 80% and 90% "
        "ranges, how accurate the forecast has been, and the monitoring status: whether the "
        "recent accuracy of the models or the forecast ranges show a reason to review them.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _clean(value: Any) -> Any:
    """Make a value strictly JSON-safe: plain types, NaN to None."""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(value) or math.isinf(value) else round(float(value), 4)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return None if pd.isna(value) else value


def _records(frame: pd.DataFrame) -> list[dict]:
    return _clean(frame.astype(object).to_dict(orient="records"))


def _optional_int(arguments: dict, key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{key} must be a whole number")
    return int(value)


def _trend(engine: Engine, arguments: dict) -> dict:
    start, end = _optional_int(arguments, "start_month"), _optional_int(arguments, "end_month")
    trend = market_trend(engine)
    if start is not None:
        trend = trend[trend["month_id"] >= start]
    if end is not None:
        trend = trend[trend["month_id"] <= end]
    return {"months": _records(trend.drop(columns="month"))}


def _segments(engine: Engine, arguments: dict) -> dict:
    top_n = _optional_int(arguments, "top_n") or MAX_SEGMENT_ROWS
    top_n = max(1, min(top_n, MAX_SEGMENT_ROWS))
    table = segment_table(engine, by=arguments.get("by", "specialty")).head(top_n)
    return {"rows": _records(table), "caveat": SEGMENT_CAVEAT}


def _direction_panel(panel_loader: Callable[[], dict]) -> dict:
    panel = panel_loader()
    return {
        "n_test_months": panel["n_test"],
        "test_label_counts": panel["label_counts"],
        "scores": _clean(panel["scores"].round(3).to_dict(orient="index")),
        "chance_band": _clean(panel["chance"].round(3).to_dict(orient="index")),
        "decision": _clean(panel["decision"]),
    }


def _stored(results_loader: Callable[[str], dict | None] | None, key: str) -> dict | None:
    return results_loader(key) if results_loader is not None else None


def _rounded(value: Any, digits: int = 4) -> Any:
    """Round every float inside a stored result and make it strictly JSON-safe."""
    cleaned = _clean(value)
    if isinstance(cleaned, float):
        return round(cleaned, digits)
    if isinstance(cleaned, list):
        return [_rounded(item, digits) for item in cleaned]
    if isinstance(cleaned, dict):
        return {key: _rounded(item, digits) for key, item in cleaned.items()}
    return cleaned


def _pct(value: Any) -> float | None:
    return None if value is None else round(float(value) * 100, 2)


def _capped(rows: list, cap: int = MAX_RESULT_ROWS) -> list:
    return _rounded(rows[:cap])


def _specialty_rows(adoption: dict) -> list[dict]:
    ordered = sorted(adoption["table"], key=lambda row: -(row.get("adjusted_share") or 0))
    return [
        {
            "specialty": row["specialty"],
            "adjusted_share_pct": _pct(row["adjusted_share"]),
            "low_pct": _pct(row["low"]),
            "high_pct": _pct(row["high"]),
            "visit_share_pct": row["visit_share_pct"],
            "position": row["position"],
        }
        for row in ordered
    ]


def _trend_section(findings: dict) -> dict:
    trend = findings["trend"]
    return {
        "months_covered": findings["months"][:1] + findings["months"][-1:],
        "break_months": trend["break_months"],
        "break_uncertainty_months": _rounded(trend["intervals"]),
        "segments": _capped(trend["segments"]),
        "p_values": _rounded(trend["p_values"]),
        "events_overlapping_the_break": _rounded(trend["events_overlap"]),
        "other_categories_breaks": _rounded(trend["other_categories"]),
        "note": "Shares are percent of Zilretta + generic corticosteroid + NSAID visits.",
    }


def _decomposition_section(findings: dict) -> dict:
    parts = findings["decomposition"]
    return {
        "windows_months": _rounded(parts["windows"]),
        "year_by_year": _capped(parts["chain"]),
        "first_to_last_with_intervals": _rounded(parts["bootstrap"]),
        "largest_specialty_contributions": _capped(parts["top_specialties"]),
        "by_granularity": _rounded(parts["granularity"]),
        "note": "mix = change from visits moving between specialties; rate = change in "
        "Zilretta's share within specialties. Shares are fractions (0.01 = 1 percentage point).",
    }


def _specialties_section(findings: dict) -> dict:
    adoption = findings["adoption"]
    rows = _specialty_rows(adoption)
    stability = adoption["stability"]
    return {
        "overall_share_pct": _pct(adoption["overall_share"]),
        "rows": _capped(rows),
        "rows_shown": min(len(rows), MAX_RESULT_ROWS),
        "rows_total": len(rows),
        "variation_explained_by": _rounded(adoption["deviance"]),
        "stability": {
            key: _rounded(stability[key])
            for key in ("spearman", "low", "high", "same_side_fraction")
            if key in stability
        },
        "specialties_that_flipped_side_between_halves": [
            row["specialty"] for row in stability["table"] if row.get("flipped")
        ][:MAX_RESULT_ROWS],
        "note": "Adjusted shares account for age and gender mix; 'position' compares the "
        "interval with the overall share.",
    }


def _robustness_section(findings: dict) -> dict:
    verdicts = findings["robustness"]["verdicts"]
    out = {key: value["label"] for key, value in verdicts.items()}
    out["failed_checks"] = {
        key: value["failed"][:MAX_RESULT_ROWS]
        for key, value in verdicts.items() if value.get("failed")
    }
    return out


def _findings(results_loader: Callable[[str], dict | None] | None, arguments: dict) -> dict:
    section = arguments.get("section") or "summary"
    if section not in FINDINGS_SECTIONS:
        raise ValueError(f"section must be one of {', '.join(FINDINGS_SECTIONS)}")
    findings = _stored(results_loader, "findings")
    if findings is None:
        return dict(_NOT_PUBLISHED)
    sections = {
        "trend": _trend_section, "decomposition": _decomposition_section,
        "specialties": _specialties_section, "robustness": _robustness_section,
    }
    if section != "summary":
        return {**sections[section](findings), "caveats": FINDINGS_CAVEATS}
    specialties = _specialties_section(findings)
    return {
        "months_covered": findings["months"][:1] + findings["months"][-1:],
        "trend": {
            key: _trend_section(findings)[key]
            for key in ("break_months", "break_uncertainty_months", "segments")
        },
        "decomposition": _decomposition_section(findings)["first_to_last_with_intervals"],
        "specialties": specialties["rows"],
        "robustness": _robustness_section(findings),
        "caveats": FINDINGS_CAVEATS,
    }


def _segment_results(stored: dict) -> dict:
    calibration = stored["calibration"]
    return {
        "task": "segment: next month's Zilretta share in each specialty-age-gender segment",
        "n_test_months": stored["n_test_months"],
        "serving": stored["decision"]["serving"],
        "promoted": stored["decision"]["promoted"],
        "best_baseline": stored["best_baseline"],
        "why": stored["decision"].get("tie_break"),
        "scores": _rounded(stored["scores"]),
        "improvement_over_baseline": _rounded(stored.get("summaries")),
        "calibration_slope": _rounded(calibration["slope"]),
        "mean_predicted_share": _rounded(calibration["mean_pred"]),
        "mean_observed_share": _rounded(calibration["mean_obs"]),
        "by_size": _capped(stored["by_size"]),
        "note": "Lower log_loss and mae are better. The gain over the baseline is small.",
    }


def _forecast_results(stored: dict) -> dict:
    return {
        "task": "forecast: next month's overall Zilretta visit share (percent)",
        "n_test_months": stored["n_test_months"],
        "serving": stored["decision"]["serving"],
        "promoted": stored["decision"]["promoted"],
        "best_baseline": stored["best_baseline"],
        "scores": _rounded(stored["scores"]),
        "note": "mae is in percentage points; cover90 is how often the 90% range held the "
        "actual value (0.90 is ideal).",
    }


def _direction_results(
    panel_loader: Callable[[], dict], results_loader: Callable[[str], dict | None] | None
) -> dict:
    out = _direction_panel(panel_loader)
    stored = _stored(results_loader, "direction")
    if stored is not None:
        out["tree_models"] = _rounded(
            {name: stored["scores"][name] for name in ("rf", "gbm") if name in stored["scores"]}
        )
        out["power"] = {
            "smallest_detectable_gain": _rounded(stored["power"]["smallest_detectable_gain"]),
            "persistence_accuracy": _rounded(stored["power"]["persistence_accuracy"]),
            "table": _capped(stored["power"]["table"]),
        }
    return out


def _model_results(
    panel_loader: Callable[[], dict],
    results_loader: Callable[[str], dict | None] | None,
    arguments: dict,
) -> dict:
    task = arguments.get("task") or "direction"
    if task not in MODEL_TASKS:
        raise ValueError(f"task must be one of {', '.join(MODEL_TASKS)}")
    if task == "direction":
        return _direction_results(panel_loader, results_loader)
    builders = {"segment": ("segment_model", _segment_results),
                "forecast": ("forecast", _forecast_results)}
    if task == "all":
        out = {"direction": _direction_results(panel_loader, results_loader)}
        for name, (key, build) in builders.items():
            stored = _stored(results_loader, key)
            out[name] = build(stored) if stored is not None else dict(_NOT_PUBLISHED)
        return out
    key, build = builders[task]
    stored = _stored(results_loader, key)
    return build(stored) if stored is not None else dict(_NOT_PUBLISHED)


def _forecast_and_monitoring(results_loader: Callable[[str], dict | None] | None) -> dict:
    forecast = _stored(results_loader, "forecast")
    monitoring = _stored(results_loader, "monitoring")
    if forecast is None or monitoring is None:
        return dict(_NOT_PUBLISHED)
    nxt = forecast["next_month"]
    best = forecast["scores"].get(forecast["decision"]["serving"], {})
    direction, interval = monitoring["direction"], monitoring["forecast"]
    return {
        "next_month": {
            "month_id": nxt["month_id"], "model": nxt["model"],
            "point_pp": _rounded(nxt["point"]),
            "range80_pp": _rounded([nxt["lo80"], nxt["hi80"]]),
            "range90_pp": _rounded([nxt["lo90"], nxt["hi90"]]),
        },
        "serving": forecast["decision"]["serving"],
        "typical_error_pp": _rounded(best.get("mae")),
        "coverage_of_90_range": _rounded(best.get("cover90")),
        "n_test_months": forecast["n_test_months"],
        "monitoring": {
            "status": monitoring["status"],
            "banner": monitoring.get("banner"),
            "direction_flag": {
                key: _rounded(direction[key])
                for key in ("match_rate", "threshold", "latest_rolling", "flag", "random_line")
            },
            "forecast_alarm": {
                key: _rounded(interval[key])
                for key in ("rule", "alarm", "misses_in_window", "false_alarm_rate")
            },
            "recent_forecast_months": _capped(interval["recent"]),
            "detection": _capped(monitoring.get("detection") or []),
        },
        "limits": FORECAST_LIMITS,
    }


def run_tool(
    name: str,
    arguments: Any,
    *,
    engine: Engine,
    panel_loader: Callable[[], dict],
    results_loader: Callable[[str], dict | None] | None = None,
) -> dict:
    """Run one tool and return a JSON-safe result. Never raises.

    `results_loader(key)` returns a stored Phase 4 result, or None when the database has none."""
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        if name == "get_data_status":
            return _clean(data_status(engine))
        if name == "get_market_trend":
            return _trend(engine, arguments)
        if name == "get_segment_table":
            return _segments(engine, arguments)
        if name == "get_model_results":
            return _model_results(panel_loader, results_loader, arguments)
        if name == "get_findings":
            return _findings(results_loader, arguments)
        if name == "get_forecast_and_monitoring":
            return _forecast_and_monitoring(results_loader)
    except ValueError as error:
        return {"error": str(error)}
    except Exception:  # noqa: BLE001 - nothing internal should reach the model or the page
        return dict(_UNAVAILABLE)
    return {"error": f"Unknown tool {name!r}."}
