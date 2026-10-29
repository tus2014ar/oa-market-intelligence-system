"""The read-only tools Claude may call when it answers a question on the site (DL-86, DL-88).

Claude never sees SQL, a database connection or a patient-level row. It can ask for ten named
summaries (six about the tables, four about the knowledge graph) and ask the page to draw one of
four charts; each one is built from the same tested functions the site's tabs use (`views`), over a
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

from oa_market_intelligence import knowledge_graph as kg
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
CHARTS = ("trend", "segments", "forecast", "specialty_mix")
MAX_EDGES = 30
PRIORITY = (
    "product",
    "manufacturer",
    "specialty",
    "group",
    "drug_class",
    "disease_area",
    "event",
    "market",
)
EVENT_NOTE = (
    "Events are context only: the project links no event to a change in share, and the cause "
    "of the March to July 2024 dip is unverified."
)
GRAPH_NOTE = (
    "Product groups are the project's own frozen grouping, not a market fact. Visits are "
    "product-visits inside IQVIA's panel (a visit can count under more than one product)."
)
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
    graph: kg.Graph | None = None


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
        graph=kg.Graph.load(),
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
    {
        "name": "get_entity",
        "description": (
            "Look up one thing in the knowledge graph by name: a product, manufacturer, "
            "specialty, drug class, competitor group, disease area or event. Returns what it is "
            "and how many connections of each kind it has, and the sources. If several things "
            "match, returns the candidates so you can ask again with an id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "A name, or an id like product:ZILRETTA"},
                "type": {"type": "string", "enum": list(kg.NODE_TYPES)},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_neighbors",
        "description": (
            "The connections of one thing in the knowledge graph, each with its source. "
            f"Relations: {', '.join(kg.RELATIONS)}. Use it for who makes a product, which "
            "products are in a group, which specialties prescribe a group, or which events "
            "name a product."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "A name or an id"},
                "relation": {"type": "string", "enum": list(kg.RELATIONS)},
                "direction": {"type": "string", "enum": ["out", "in", "both"]},
                "top": {"type": "integer", "description": f"Up to {MAX_EDGES}"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "get_competitors",
        "description": (
            "How the project groups Zilretta's competition: the groups Zilretta is compared "
            "with, their products, manufacturers and share of product-visits. Defaults to "
            "Zilretta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"product": {"type": "string", "description": "A product name"}},
        },
    },
    {
        "name": "get_events",
        "description": (
            "The verified event timeline (approvals, payment rules, guidelines, the Pacira and "
            "Flexion deal, data events) with sources, optionally within a date range or for one "
            "entity. Events are context, never causes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "entity": {"type": "string", "description": "A name or an id"},
            },
        },
    },
    {
        "name": "show_chart",
        "description": (
            "Ask the page to draw a chart under your answer. trend: Zilretta's share by month "
            "(optional start_month_id, end_month_id). segments: share by specialty, age_band or "
            "gender (by, top). forecast: recent history with the national forecast and its 90% "
            "range. specialty_mix: where Zilretta's visits come from, by specialty (top). "
            "Describe what the chart shows; do not repeat every number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chart": {"type": "string", "enum": list(CHARTS)},
                "by": {"type": "string", "enum": list(SEGMENT_GROUPS)},
                "top": {"type": "integer"},
                "start_month_id": {"type": "integer"},
                "end_month_id": {"type": "integer"},
            },
            "required": ["chart"],
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


# ------------------------------------------------------------------ the knowledge-graph tools


def _graph(snap: Snapshot) -> kg.Graph:
    if snap.graph is None:
        raise ValueError("The knowledge graph is not available.")
    return snap.graph


def _pick(hits: list[dict], text: str) -> dict | None:
    """The one node a name means, or None when it is truly ambiguous.

    An exact label wins (a product before a manufacturer before a group when the same word names
    several); otherwise a single match that is not an event wins; events never win by being many.
    """
    wanted = text.lower()
    exact = [n for n in hits if n["label"].lower() == wanted or n["id"] == text]
    if exact:
        best = min(PRIORITY.index(n["type"]) for n in exact)
        top = [n for n in exact if PRIORITY.index(n["type"]) == best]
        return top[0] if len(top) == 1 else None
    pool = [n for n in hits if n["type"] != "event"] or hits
    return pool[0] if len(pool) == 1 else None


def _one(graph: kg.Graph, text: Any, kind: str | None = None) -> dict:
    """The single node a name or id stands for; several matches are an error listing them."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("A name is required.")
    if text in graph.nodes:
        return graph.nodes[text]
    hits = graph.find(text, kind)
    if not hits:
        raise ValueError(f"Nothing in the graph matches {text!r}.")
    node = _pick(hits, text)
    if node is None:
        ids = [n["id"] for n in hits[:6]]
        raise ValueError(f"{text!r} matches several things; ask again with one of these ids: {ids}")
    return node


def _label_row(node: dict) -> dict:
    return {"id": node["id"], "type": node["type"], "label": node["label"]}


def _entity(snap: Snapshot, arguments: dict) -> dict:
    graph = _graph(snap)
    kind = arguments.get("type")
    if kind is not None and kind not in kg.NODE_TYPES:
        raise ValueError(f"type must be one of {list(kg.NODE_TYPES)}")
    text = str(arguments.get("name") or "").strip()
    if not text:
        raise ValueError("A name is required.")
    hits = [graph.nodes[text]] if text in graph.nodes else graph.find(text, kind)
    if not hits:
        raise ValueError(f"Nothing in the graph matches {text!r}.")
    chosen = _pick(hits, text)
    if chosen is None:
        return {
            "matches": [_label_row(n) for n in hits[:8]],
            "note": "Several matches: ask again with an id.",
        }
    counts: dict[str, int] = {}
    evidence = set()
    for edge in graph.neighbors(chosen["id"]):
        key = f"{edge['relation']} ({edge['direction']})"
        counts[key] = counts.get(key, 0) + 1
        evidence.add(edge["evidence"])
    attrs = {k: v for k, v in chosen.items() if k not in ("id", "type", "label")}
    return _clean(
        {
            **_label_row(chosen),
            "attributes": attrs,
            "connections": counts,
            "evidence": sorted(evidence),
            "note": EVENT_NOTE if chosen["type"] == "event" else GRAPH_NOTE,
        }
    )


def _edge_row(edge: dict) -> dict:
    other = edge["other"]
    row = {
        "relation": edge["relation"],
        "direction": edge["direction"],
        "other_id": other["id"],
        "other_type": other["type"],
        "other_label": other["label"],
        "evidence": edge["evidence"],
    }
    for key in ("visits", "share_of_group_visits", "rule"):
        if key in edge:
            row[key] = edge[key]
    if other["type"] == "event":
        row["date"] = other.get("date")
    return row


def _neighbors(snap: Snapshot, arguments: dict) -> dict:
    graph = _graph(snap)
    node = _one(graph, arguments.get("entity"))
    relation = arguments.get("relation")
    if relation is not None and relation not in kg.RELATIONS:
        raise ValueError(f"relation must be one of {list(kg.RELATIONS)}")
    direction = arguments.get("direction") or "both"
    if direction not in ("out", "in", "both"):
        raise ValueError("direction must be out, in or both")
    top = max(1, min(_int_or_none(arguments, "top") or 15, MAX_EDGES))
    edges = graph.neighbors(node["id"], relation, direction)
    edges.sort(key=lambda e: (-e.get("visits", 0), e["other"]["label"]))
    return _clean(
        {
            **_label_row(node),
            "total_connections": len(edges),
            "shown": min(top, len(edges)),
            "connections": [_edge_row(e) for e in edges[:top]],
            "evidence": sorted({e["evidence"] for e in edges[:top]}),
            "note": EVENT_NOTE if relation in ("involves", "occurred_in") else GRAPH_NOTE,
        }
    )


def _group_visits(graph: kg.Graph, group: dict) -> int:
    return sum(e["visits"] for e in graph.neighbors(group["id"], "prescribed_by", "out"))


def _group_summary(graph: kg.Graph, group: dict, total: int) -> dict:
    products = [e["other"] for e in graph.neighbors(group["id"], "in_group", "in")]
    makers: dict[str, int] = {}
    for product in products:
        for edge in graph.neighbors(product["id"], "made_by", "out"):
            name = edge["other"]["label"]
            makers[name] = makers.get(name, 0) + 1
    visits = _group_visits(graph, group)
    top_makers = sorted(makers.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    return {
        "group": group["label"],
        "id": group["id"],
        "products": len(products),
        "example_products": sorted(p["label"] for p in products)[:8],
        "manufacturers": [{"name": name, "products": n} for name, n in top_makers],
        "visits": visits,
        "share_of_compared_visits_pct": round(100 * visits / total, 1) if total else None,
    }


def _competitors(snap: Snapshot, arguments: dict) -> dict:
    graph = _graph(snap)
    product = _one(graph, arguments.get("product") or "ZILRETTA", "product")
    groups = [e["other"] for e in graph.neighbors(product["id"], "in_group", "out")]
    if not groups:
        raise ValueError("That product has no competitor group in the graph.")
    own = groups[0]
    compared = [e["other"] for e in graph.neighbors(own["id"], "compared_with", "out")]
    chosen = [own, *compared]
    total = sum(_group_visits(graph, g) for g in chosen)
    out = {
        "product": product["label"],
        "own_group": own["label"],
        "groups": [_group_summary(graph, g, total) for g in chosen],
        "evidence": ["competitor_groups.csv", "dim_product", "fact_product_visits"],
        "note": GRAPH_NOTE,
    }
    if not compared:
        out["note"] += " Only Zilretta's group is compared with other groups in the project."
    return _clean(out)


def _date(arguments: dict, key: str) -> pd.Timestamp | None:
    value = arguments.get(key)
    if value is None or value == "":
        return None
    try:
        return pd.Timestamp(str(value))
    except (ValueError, TypeError):
        raise ValueError(f"{key} must be a date like 2021-11-19") from None


def _events(snap: Snapshot, arguments: dict) -> dict:
    graph = _graph(snap)
    start, end = _date(arguments, "start_date"), _date(arguments, "end_date")
    wanted = None
    if arguments.get("entity"):
        node = _one(graph, arguments["entity"])
        wanted = {e["other"]["id"] for e in graph.neighbors(node["id"], "involves", "in")}
    rows = []
    for node in graph.nodes.values():
        if node["type"] != "event":
            continue
        begins = pd.Timestamp(node["date"])
        ends = pd.Timestamp(node.get("end_date") or node["date"])
        if (start is not None and ends < start) or (end is not None and begins > end):
            continue
        if wanted is not None and node["id"] not in wanted:
            continue
        involves = [e["other"]["label"] for e in graph.neighbors(node["id"], "involves", "out")]
        rows.append(
            {
                "date": node["date"],
                "end_date": node.get("end_date"),
                "type": node["event_type"],
                "description": node["label"],
                "source": node["source"],
                "verified": bool(node["verified"]),
                "involves": involves,
            }
        )
    rows.sort(key=lambda r: r["date"])
    return _clean(
        {
            "count": len(rows),
            "events": rows,
            "evidence": ["events.csv", "event_links.csv"],
            "note": EVENT_NOTE,
        }
    )


def _show_chart(snap: Snapshot, arguments: dict) -> dict:
    chart = arguments.get("chart")
    if chart not in CHARTS:
        raise ValueError(f"chart must be one of {list(CHARTS)}")
    params: dict[str, Any] = {}
    if chart == "segments":
        by = arguments.get("by") or "specialty"
        if by not in SEGMENT_GROUPS:
            raise ValueError(f"by must be one of {list(SEGMENT_GROUPS)}")
        params["by"] = by
    if chart in ("segments", "specialty_mix"):
        params["top"] = _top(arguments, 10)
    if chart == "trend":
        for key in ("start_month_id", "end_month_id"):
            value = _int_or_none(arguments, key)
            if value is not None:
                params[key] = value
    if chart == "specialty_mix":
        _graph(snap)
    return {
        "chart": chart,
        "params": params,
        "shown": True,
        "note": "The page draws this chart under your answer. Say what it shows; do not repeat "
        "every number.",
    }


TOOLS = {
    "get_data_status": _data_status,
    "get_market_trend": _market_trend,
    "get_segments": _segments,
    "get_forecasts": _forecasts,
    "get_model_results": _model_results,
    "get_findings": _findings,
    "get_entity": _entity,
    "get_neighbors": _neighbors,
    "get_competitors": _competitors,
    "get_events": _events,
    "show_chart": _show_chart,
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
