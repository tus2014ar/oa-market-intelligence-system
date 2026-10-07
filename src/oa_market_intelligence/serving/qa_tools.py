"""The only things Claude can reach from the question box.

Four pre-written, read-only tools that return small aggregate summaries as plain JSON. None
takes SQL and none returns patient-visit-level rows. Errors come back as `{"error": ...}`
results so the model can say what went wrong; internal detail is never passed on.
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
        "description": "How the forecasting models scored on walk-forward testing of the "
        "monthly direction task: baselines, the logistic regression, the range random "
        "guessing produces, and which model the site serves and why.",
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


def _model_results(panel_loader: Callable[[], dict]) -> dict:
    panel = panel_loader()
    return {
        "n_test_months": panel["n_test"],
        "test_label_counts": panel["label_counts"],
        "scores": _clean(panel["scores"].round(3).to_dict(orient="index")),
        "chance_band": _clean(panel["chance"].round(3).to_dict(orient="index")),
        "decision": _clean(panel["decision"]),
    }


def run_tool(
    name: str,
    arguments: Any,
    *,
    engine: Engine,
    panel_loader: Callable[[], dict],
) -> dict:
    """Run one tool and return a JSON-safe result. Never raises."""
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
            return _model_results(panel_loader)
    except ValueError as error:
        return {"error": str(error)}
    except Exception:  # noqa: BLE001 - nothing internal should reach the model or the page
        return dict(_UNAVAILABLE)
    return {"error": f"Unknown tool {name!r}."}
