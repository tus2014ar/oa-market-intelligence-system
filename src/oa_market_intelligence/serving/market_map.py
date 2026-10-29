"""The Market map (DL-89): the knowledge graph as a picture a brand manager can explore.

`view_graph` turns the graph into the nodes and edges of one of three focused views (the whole
graph, 311 nodes, would be a hairball); `node_details` gives everything the graph holds about one
node, with its sources; `to_html` draws a view with pyvis (imported only there, so everything else
works without it). Nothing here adds analysis. In the competitive-set view the line from a
manufacturer to a group is *derived* (the manufacturer makes products that the project placed in
that group) and is marked as derived on its tooltip; no cause is ever drawn, and the competitor
groups are the project's own grouping.
"""

from __future__ import annotations

import json
import math
import re

import pandas as pd

from oa_market_intelligence.knowledge_graph import Graph, is_placeholder_manufacturer

VIEWS = {
    "competitive_set": "Zilretta and the groups it is compared with",
    "who_uses_it": "Which specialties each group's visits come from",
    "events": "The event timeline and who each event names",
}
# the dataviz reference palette's categorical slots, each type also has its own shape
COLORS = {
    "product": "#2a78d6",
    "group": "#eb6834",
    "manufacturer": "#1baf7a",
    "specialty": "#e87ba4",
    "event": "#6250d6",
    "unverified_event": "#9a9a94",
    "disease_area": "#eda100",
    "drug_class": "#c98500",
    "market": "#52514e",
}
SHAPES = {
    "product": "star",
    "group": "box",
    "manufacturer": "square",
    "specialty": "dot",
    "event": "diamond",
    "disease_area": "triangle",
    "drug_class": "triangleDown",
    "market": "hexagon",
}
GLYPHS = {
    "product": "★",
    "group": "■",
    "manufacturer": "■",
    "specialty": "●",
    "event": "◆",
    "disease_area": "▲",
    "drug_class": "▼",
    "market": "⬢",
}
TYPE_NAMES = {
    "product": "Product",
    "group": "Competitor group",
    "manufacturer": "Manufacturer",
    "specialty": "Specialty",
    "event": "Event",
    "drug_class": "Drug class",
    "disease_area": "Disease area",
    "market": "Market",
}
MIN_TOP, MAX_TOP = 3, 15
NOTE = (
    "Built from facts already in the project, each with its source. Competitor groups are the "
    "project's own grouping, not a market fact. Visits are product-visits inside IQVIA's panel. "
    "Events are context only: nothing here says an event changed the share."
)


def _size(value: float, biggest: float, low: int = 14, high: int = 44) -> int:
    if biggest <= 0 or value <= 0:
        return low
    return round(low + (high - low) * math.sqrt(value / biggest))


def _event_color(node: dict) -> str:
    return COLORS["event"] if node.get("verified") else COLORS["unverified_event"]


def _node(graph: Graph, node_id: str, size: int | None = None, extra: str = "") -> dict:
    node = graph.nodes[node_id]
    kind = node["type"]
    title = [f"{node['label']}", TYPE_NAMES[kind]]
    if kind == "product":
        title.append(f"Brand or generic tag: {node.get('brand_generic_tag', 'n/a')}")
        title.append(
            f"First FDA approval in the file: {node.get('first_fda_approval', 'not listed')}"
        )
    elif kind == "event":
        title = [
            f"{node['date']}: {node['label']}",
            f"Type: {node['event_type']}",
            f"Source: {node['source']}",
            "Verified" if node.get("verified") else "Not verified (the cause is unproven)",
        ]
    if extra:
        title.append(extra)
    label = node["label"]
    if kind == "event":
        label = f"{node['date'][:7]}: {label[:30]}{'…' if len(label) > 30 else ''}"
    elif len(label) > 28:
        label = label[:27] + "…"
    return {
        "id": node_id,
        "label": label,
        "type": kind,
        "size": size or 22,
        "color": _event_color(node) if kind == "event" else COLORS[kind],
        "shape": SHAPES[kind],
        "title": "\n".join(title),
    }


def _edge(
    a: str,
    b: str,
    relation: str,
    evidence: str,
    *,
    width: float = 1.5,
    note: str = "",
    derived: bool = False,
    dashes: bool = False,
) -> dict:
    title = [relation.replace("_", " "), f"Source: {evidence}"]
    if derived:
        title.insert(1, "Derived: the manufacturer makes products the project placed in this group")
    if note:
        title.append(note)
    return {
        "from": a,
        "to": b,
        "relation": relation,
        "evidence": evidence,
        "width": round(width, 2),
        "title": "\n".join(title),
        "derived": derived,
        "dashes": dashes or derived,
    }


def _visits_note(edge: dict, whose: str) -> str:
    share = 100 * edge["share_of_group_visits"]
    return f"{edge['visits']:,} visits, {share:.1f}% of {whose} visits"


def _group_visits(graph: Graph, group_id: str) -> int:
    return sum(e["visits"] for e in graph.neighbors(group_id, "prescribed_by", "out"))


def _compared(graph: Graph) -> list[str]:
    own = "group:zilretta"
    return [own, *[e["other"]["id"] for e in graph.neighbors(own, "compared_with", "out")]]


def _top_makers(graph: Graph, group_id: str, n: int) -> list[tuple[str, int]]:
    makers: dict[str, int] = {}
    for member in graph.neighbors(group_id, "in_group", "in"):
        for edge in graph.neighbors(member["other"]["id"], "made_by", "out"):
            if is_placeholder_manufacturer(edge["other"]["label"]):
                continue
            makers[edge["other"]["id"]] = makers.get(edge["other"]["id"], 0) + 1
    return sorted(makers.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _competitive_set(graph: Graph, top: int, show_events: bool) -> tuple[list[dict], list[dict]]:
    groups = _compared(graph)
    visits = {g: _group_visits(graph, g) for g in groups}
    biggest = max(visits.values())
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    nodes["product:ZILRETTA"] = _node(graph, "product:ZILRETTA", 30)
    for g in groups:
        members = len(graph.neighbors(g, "in_group", "in"))
        extra = f"Visits, all months: {visits[g]:,}\nProducts: {members}"
        nodes[g] = _node(graph, g, _size(visits[g], biggest, 26, 50), extra)
    edges.append(
        _edge("product:ZILRETTA", "group:zilretta", "in_group", "competitor_groups.csv", width=3)
    )
    for g in groups[1:]:
        edges.append(
            _edge(
                "group:zilretta", g, "compared_with", "competitor_groups.csv", width=3, dashes=True
            )
        )
    for g in groups:
        for maker, count in _top_makers(graph, g, top):
            nodes.setdefault(maker, _node(graph, maker, 18, "Makes products in the groups shown"))
            edges.append(
                _edge(
                    maker,
                    g,
                    "makes_products_in",
                    "dim_product + competitor_groups.csv",
                    width=1 + math.log2(count + 1),
                    note=f"Products in this group: {count}",
                    derived=True,
                )
            )
    zil = [e for e in graph.neighbors("group:zilretta", "prescribed_by", "out")]
    zil.sort(key=lambda e: -e["visits"])
    for edge in zil[:top]:
        spec = edge["other"]["id"]
        nodes.setdefault(
            spec,
            _node(
                graph,
                spec,
                _size(edge["visits"], zil[0]["visits"], 12, 34),
                f"Zilretta visits, all months: {edge['visits']:,}",
            ),
        )
        edges.append(
            _edge(
                "group:zilretta",
                spec,
                "prescribed_by",
                "fact_product_visits",
                width=1 + 14 * edge["share_of_group_visits"],
                note=_visits_note(edge, "Zilretta's"),
            )
        )
    if show_events:
        shown = set(nodes)
        for event in graph.nodes.values():
            if event["type"] != "event":
                continue
            targets = [e["other"]["id"] for e in graph.neighbors(event["id"], "involves", "out")]
            targets = [t for t in targets if t in shown]
            if targets:
                nodes[event["id"]] = _node(graph, event["id"], 14)
                for target in targets:
                    edges.append(_edge(event["id"], target, "involves", "event_links.csv", width=1))
    return list(nodes.values()), edges


def _who_uses_it(graph: Graph, top: int) -> tuple[list[dict], list[dict]]:
    groups = _compared(graph)
    total: dict[str, int] = {}
    for g in groups:
        for e in graph.neighbors(g, "prescribed_by", "out"):
            total[e["other"]["id"]] = total.get(e["other"]["id"], 0) + e["visits"]
    chosen = [s for s, _ in sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
    nodes = {
        g: _node(graph, g, 40, f"Visits, all months: {_group_visits(graph, g):,}") for g in groups
    }
    edges = []
    for g in groups:
        for e in graph.neighbors(g, "prescribed_by", "out"):
            spec = e["other"]["id"]
            if spec not in chosen:
                continue
            nodes.setdefault(
                spec,
                _node(
                    graph,
                    spec,
                    _size(total[spec], max(total.values()), 12, 34),
                    f"Visits across the three groups: {total[spec]:,}",
                ),
            )
            edges.append(
                _edge(
                    g,
                    spec,
                    "prescribed_by",
                    "fact_product_visits",
                    width=1 + 18 * e["share_of_group_visits"],
                    note=_visits_note(e, "this group's"),
                )
            )
    return list(nodes.values()), edges


def _events_view(graph: Graph) -> tuple[list[dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    nodes["market:iqvia_nmta"] = _node(graph, "market:iqvia_nmta", 30)
    for event in graph.nodes.values():
        if event["type"] != "event":
            continue
        nodes[event["id"]] = _node(graph, event["id"], 18)
        edges.append(_edge(event["id"], "market:iqvia_nmta", "occurred_in", "events.csv", width=1))
        for e in graph.neighbors(event["id"], "involves", "out"):
            target = e["other"]["id"]
            nodes.setdefault(target, _node(graph, target, 24))
            edges.append(_edge(event["id"], target, "involves", "event_links.csv", width=2))
    return list(nodes.values()), edges


def view_graph(
    graph: Graph, view: str = "competitive_set", top: int = 8, show_events: bool = True
) -> dict:
    """The nodes and edges of one view, ready to draw. Raises ValueError for an unknown view."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {sorted(VIEWS)}")
    top = max(MIN_TOP, min(int(top), MAX_TOP))
    if view == "competitive_set":
        nodes, edges = _competitive_set(graph, top, show_events)
    elif view == "who_uses_it":
        nodes, edges = _who_uses_it(graph, top)
    else:
        nodes, edges = _events_view(graph)
    nodes.sort(key=lambda n: n["id"])
    edges.sort(key=lambda e: (e["relation"], e["from"], e["to"]))
    return {"view": view, "title": VIEWS[view], "nodes": nodes, "edges": edges, "note": NOTE}


def node_details(graph: Graph, node_id: str, limit: int = 40) -> dict:
    """Everything the graph holds about one node: its attributes, its connections and sources."""
    if node_id not in graph.nodes:
        raise KeyError(node_id)
    node = graph.nodes[node_id]
    rows = []
    for edge in graph.neighbors(node_id):
        other = edge["other"]
        rows.append(
            {
                "relation": edge["relation"].replace("_", " "),
                "direction": "from this" if edge["direction"] == "out" else "to this",
                "other": other["label"],
                "other_type": TYPE_NAMES[other["type"]],
                "visits": edge.get("visits"),
                "share_of_group_visits_pct": (
                    round(100 * edge["share_of_group_visits"], 1)
                    if "share_of_group_visits" in edge
                    else None
                ),
                "source": edge["evidence"],
            }
        )
    rows.sort(key=lambda r: (-(r["visits"] or 0), r["relation"], r["other"]))
    attributes = {k: v for k, v in node.items() if k not in ("id", "type", "label")}
    return {
        "id": node_id,
        "label": node["label"],
        "type": TYPE_NAMES[node["type"]],
        "attributes": attributes,
        "connections": rows[:limit],
        "total_connections": len(rows),
        "sources": sorted({r["source"] for r in rows}),
    }


def event_table(graph: Graph) -> pd.DataFrame:
    """Every event with its date, type, source and who it names, oldest first."""
    rows = []
    for node in graph.nodes.values():
        if node["type"] != "event":
            continue
        involves = [e["other"]["label"] for e in graph.neighbors(node["id"], "involves", "out")]
        rows.append(
            {
                "date": node["date"],
                "until": node.get("end_date") or "",
                "type": node["event_type"],
                "event": node["label"],
                "names": ", ".join(involves),
                "verified": "yes" if node.get("verified") else "no",
                "source": node["source"],
            }
        )
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def legend(view: dict) -> list[tuple[str, str, str]]:
    """(glyph, colour, name) for each kind of node in the view, in a fixed order."""
    kinds = []
    for node in view["nodes"]:
        key = "unverified_event" if node["color"] == COLORS["unverified_event"] else node["type"]
        if key not in kinds:
            kinds.append(key)
    order = [
        "product",
        "group",
        "manufacturer",
        "specialty",
        "event",
        "unverified_event",
        "disease_area",
        "drug_class",
        "market",
    ]
    out = []
    for key in sorted(kinds, key=order.index):
        base = "event" if key == "unverified_event" else key
        name = "Event (not verified)" if key == "unverified_event" else TYPE_NAMES[base]
        out.append((GLYPHS[base], COLORS[key], name))
    return out


def positions(view: dict, width: int = 600, height: int = 340) -> dict[str, tuple[float, float]]:
    """A fixed layout for a view (networkx spring layout, fixed seed), as x, y in canvas units."""
    import networkx as nx

    graph = nx.Graph()
    graph.add_nodes_from(n["id"] for n in view["nodes"])
    graph.add_edges_from((e["from"], e["to"]) for e in view["edges"])
    count = max(graph.number_of_nodes(), 2)
    raw = nx.spring_layout(graph, seed=7, k=3.0 / math.sqrt(count), iterations=300)
    return {
        k: (round(float(x) * width, 1), round(float(y) * height, 1)) for k, (x, y) in raw.items()
    }


LIGHT = {"bg": "#ffffff", "text": "#52514e", "edge": "#b9b8b2", "border": "#d5d4cf"}
DARK = {"bg": "#0e1626", "text": "#e6e8ec", "edge": "#5d6b82", "border": "#2c3a52"}


def to_html(view: dict, height: int = 620, physics: bool = False, dark: bool = False) -> str:
    """The view drawn with pyvis as one self-contained HTML page (no network needed), in light or
    dark colours to match the page."""
    palette = DARK if dark else LIGHT
    from pyvis.network import Network  # imported here so the rest works without pyvis

    net = Network(
        height=f"{height}px",
        width="100%",
        bgcolor=palette["bg"],
        font_color=palette["text"],
        directed=False,
        notebook=False,
        cdn_resources="in_line",
    )
    where = positions(view)
    for node in view["nodes"]:
        net.add_node(
            node["id"],
            x=where[node["id"]][0],
            y=where[node["id"]][1],
            label=node["label"],
            title=node["title"],
            size=node["size"],
            color=node["color"],
            shape=node["shape"],
        )
    for edge in view["edges"]:
        net.add_edge(
            edge["from"],
            edge["to"],
            width=edge["width"],
            title=edge["title"],
            dashes=edge["dashes"],
            color=palette["edge"],
        )
    net.set_options(
        json.dumps(
            {
                "interaction": {"hover": True, "tooltipDelay": 80, "navigationButtons": False},
                "physics": {
                    "enabled": physics,
                    "solver": "barnesHut",
                    "barnesHut": {"gravitationalConstant": -9000, "springLength": 150},
                    "stabilization": {"enabled": False},
                },
                "nodes": {"font": {"size": 15, "color": palette["text"]}},
                "edges": {"smooth": {"type": "continuous"}},
            }
        )
    )
    html = net.generate_html()
    html = html.replace(
        "</head>",
        f"<style>body{{background:{palette['bg']};margin:0}}"
        f"#mynetwork{{border:1px solid {palette['border']} !important}}</style></head>",
        1,
    )
    # pyvis always links Bootstrap from a CDN; the page needs none of it, and without the link
    # the map also draws with no network
    html = re.sub(r"<link[^>]*bootstrap[^>]*>", "", html)
    html = re.sub(r"<script[^>]*bootstrap[^>]*></script>", "", html)
    # fit the whole map in the frame once the frame has its real size, and again if it is resized
    html = re.sub(
        r"drawGraph\(\);\s*</script>",
        "var map = drawGraph();"
        "function refit() { map.redraw(); map.fit({animation: false}); }"
        "map.once('afterDrawing', refit);"
        "setTimeout(refit, 150); setTimeout(refit, 700);"
        "window.addEventListener('resize', refit);</script>",
        html,
        count=1,
    )
    key = "".join(
        f'<span style="margin-right:14px"><span style="color:{c}">{g}</span> {n}</span>'
        for g, c, n in legend(view)
    )
    bar = (
        f'<div style="font:13px sans-serif;color:{palette["text"]};padding:6px 4px">'
        f"{key}<br>Hover a point for its facts and source. Drag to move, scroll to zoom. "
        "Dashed lines are derived or comparisons, not direct facts.</div>"
    )
    return html.replace("<body>", "<body>" + bar, 1)
