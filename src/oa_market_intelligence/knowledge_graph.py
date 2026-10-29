"""The knowledge graph (DL-87): the facts the project already holds, as nodes and named relations.

It adds no analysis. Every node and edge comes from a table or reference file already in the
repository and carries the name of its source, so an answer that uses the graph can say where each
fact came from.

Nodes: product, manufacturer, drug class, competitor group, specialty, disease area, event and the
market itself. Relations (the only ones allowed):

    made_by          product -> manufacturer              dim_product
    in_class         product -> drug class                dim_product
    in_disease_area  product -> disease area (OA or RA)   dim_product
    in_group         product -> competitor group          competitor_groups.csv
    compared_with    group   -> group                     competitor_groups.csv (Zilretta against
                                                          the two injectable-steroid groups)
    prescribed_by    group   -> specialty                 fact_product_visits (visits and share)
    involves         event   -> product, manufacturer     event_links.csv (named in the event's
                              or disease area             own text)
    occurred_in      event   -> the market                events.csv (the event falls in the period)

There is deliberately no causal relation: an event is never linked to a change in share. The graph
is committed as `data/reference/knowledge_graph.json`; rebuild it with `oa-graph` after a new
extract (a test fails when it is stale).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = REPO_ROOT / "data" / "reference"
WAREHOUSE = REPO_ROOT / "data" / "published" / "warehouse.db"
GRAPH_JSON = REFERENCE / "knowledge_graph.json"
GROUPS_CSV = REFERENCE / "competitor_groups.csv"
EVENTS_CSV = REFERENCE / "events.csv"
EVENT_LINKS_CSV = REFERENCE / "event_links.csv"
APPROVALS_CSV = REFERENCE / "openfda_competitive_set_approvals_2026-10-07.csv"

MARKET_ID = "market:iqvia_nmta"
NODE_TYPES = (
    "product",
    "manufacturer",
    "drug_class",
    "group",
    "specialty",
    "disease_area",
    "event",
    "market",
)
RELATIONS = (
    "made_by",
    "in_class",
    "in_disease_area",
    "in_group",
    "compared_with",
    "prescribed_by",
    "involves",
    "occurred_in",
)
SOURCES = {
    "dim_product": "Warehouse table dim_product (the IQVIA product list with the project's "
    "taxonomy)",
    "competitor_groups.csv": "data/reference/competitor_groups.csv: the product groups frozen "
    "before any group-level number was looked at (a documented choice, not a market fact)",
    "fact_product_visits": "Warehouse table fact_product_visits: patient visits by month, "
    "product, specialty and demographic group, summed over all months",
    "events.csv": "data/reference/events.csv: the verified event timeline, each with its source",
    "event_links.csv": "data/reference/event_links.csv: which entity an event's own text names",
    "openfda_competitive_set_approvals": "data/reference/"
    "openfda_competitive_set_approvals_2026-10-07.csv: earliest original FDA approval per product",
}
GROUP_LABELS = {
    "zilretta": "Zilretta",
    "triamcinolone_ir": "Triamcinolone (immediate release)",
    "other_injectable_corticosteroid": "Other injectable corticosteroids",
    "other": "Other products (NSAIDs, opioids, other)",
}
COMPARED_GROUPS = ("triamcinolone_ir", "other_injectable_corticosteroid")
# IQVIA writes this where a product has no manufacturer; it is in the graph as the source gives
# it, but it is not a company, so lists and maps of manufacturers leave it out
PLACEHOLDER_MANUFACTURERS = ("no manufacturer",)


def is_placeholder_manufacturer(label: str) -> bool:
    return str(label).strip().lower() in PLACEHOLDER_MANUFACTURERS


def node_id(kind: str, key: str) -> str:
    return f"{kind}:{str(key).strip()}"


def _node(kind: str, key: str, label: str | None = None, **attrs) -> dict:
    clean = {k: v for k, v in attrs.items() if v is not None and v == v}
    return {"id": node_id(kind, key), "type": kind, "label": label or str(key), **clean}


def _edge(source: str, target: str, relation: str, evidence: str, **attrs) -> dict:
    clean = {k: v for k, v in attrs.items() if v is not None and v == v}
    return {"source": source, "target": target, "relation": relation, "evidence": evidence, **clean}


def build_graph(
    products: pd.DataFrame,
    groups: pd.DataFrame,
    specialty_visits: pd.DataFrame,
    events: pd.DataFrame,
    links: pd.DataFrame,
    approvals: pd.DataFrame,
    *,
    built_from: dict | None = None,
) -> dict:
    """The graph as a plain dict ({nodes, edges, sources, ...}), in a fixed order.

    products: product_name, manufacturer, brand_generic_tag, disease_area, treatment_category
    groups: product_name, group, rule          specialty_visits: product_name, specialty, visits
    events: event_date, event_end_date, event_type, description, source, verified
    links: event_date, match, entity, note     approvals: product_name, earliest_orig
    """
    if products["product_name"].duplicated().any():
        raise ValueError("a product appears twice in the product list")
    group_of = dict(zip(groups["product_name"], groups["group"], strict=True))
    rule_of = dict(zip(groups["product_name"], groups["rule"], strict=True))
    missing = set(products["product_name"]) - set(group_of)
    if missing:
        raise ValueError(f"products without a competitor group: {sorted(missing)[:5]}")
    first_approval = dict(zip(approvals["product_name"], approvals["earliest_orig"], strict=True))

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add(node: dict) -> str:
        nodes.setdefault(node["id"], node)
        return node["id"]

    market = add(
        _node(
            "market", "iqvia_nmta", "US osteoarthritis and rheumatoid arthritis visits (IQVIA NMTA)"
        )
    )
    for key in sorted(set(groups["group"])):
        add(_node("group", key, GROUP_LABELS.get(key, key)))
    for area in sorted(products["disease_area"].dropna().unique()):
        add(_node("disease_area", area, f"{area} (IQVIA disease area)"))
    for category in sorted(products["treatment_category"].dropna().unique()):
        add(_node("drug_class", category, category.replace("_", " ")))

    for row in products.sort_values("product_name").itertuples(index=False):
        pid = add(
            _node(
                "product",
                row.product_name,
                brand_generic_tag=row.brand_generic_tag,
                first_fda_approval=first_approval.get(row.product_name),
            )
        )
        if isinstance(row.manufacturer, str) and row.manufacturer.strip():
            mid = add(_node("manufacturer", row.manufacturer))
            edges.append(_edge(pid, mid, "made_by", "dim_product"))
        edges.append(
            _edge(pid, node_id("drug_class", row.treatment_category), "in_class", "dim_product")
        )
        edges.append(
            _edge(pid, node_id("disease_area", row.disease_area), "in_disease_area", "dim_product")
        )
        edges.append(
            _edge(
                pid,
                node_id("group", group_of[row.product_name]),
                "in_group",
                "competitor_groups.csv",
                rule=rule_of[row.product_name],
            )
        )

    zilretta = node_id("group", "zilretta")
    for other in COMPARED_GROUPS:
        if node_id("group", other) in nodes and zilretta in nodes:
            edges.append(
                _edge(zilretta, node_id("group", other), "compared_with", "competitor_groups.csv")
            )

    visits = specialty_visits.assign(group=specialty_visits["product_name"].map(group_of))
    if visits["group"].isna().any():
        raise ValueError("visits for a product that has no competitor group")
    by_group = visits.groupby(["group", "specialty"], as_index=False)["visits"].sum()
    totals = by_group.groupby("group")["visits"].transform("sum")
    by_group["share"] = (by_group["visits"] / totals).round(6)
    for row in by_group.sort_values(["group", "specialty"]).itertuples(index=False):
        sid = add(_node("specialty", row.specialty))
        edges.append(
            _edge(
                node_id("group", row.group),
                sid,
                "prescribed_by",
                "fact_product_visits",
                visits=int(row.visits),
                share_of_group_visits=float(row.share),
            )
        )

    event_ids = {}
    for number, row in enumerate(events.itertuples(index=False), start=1):
        eid = add(
            _node(
                "event",
                str(number),
                row.description,
                date=str(row.event_date),
                end_date=None if pd.isna(row.event_end_date) else str(row.event_end_date),
                event_type=row.event_type,
                verified=int(row.verified),
                source=row.source,
            )
        )
        event_ids[number] = (eid, str(row.event_date), row.description)
        edges.append(_edge(eid, market, "occurred_in", "events.csv"))
    for link in links.itertuples(index=False):
        hits = [
            eid
            for eid, date, text in event_ids.values()
            if date == str(link.event_date) and link.match in text
        ]
        if len(hits) != 1:
            raise ValueError(
                f"event link {link.event_date} {link.match!r} matches {len(hits)} events"
            )
        if link.entity not in nodes:
            raise ValueError(f"event link names an unknown entity {link.entity!r}")
        edges.append(_edge(hits[0], link.entity, "involves", "event_links.csv"))

    unknown = {e["relation"] for e in edges} - set(RELATIONS)
    if unknown:
        raise ValueError(f"unknown relations {sorted(unknown)}")
    ordered_nodes = sorted(nodes.values(), key=lambda n: (NODE_TYPES.index(n["type"]), n["id"]))
    ordered_edges = sorted(
        edges, key=lambda e: (RELATIONS.index(e["relation"]), e["source"], e["target"])
    )
    return {
        "built_from": built_from or {},
        "sources": SOURCES,
        "node_types": list(NODE_TYPES),
        "relations": list(RELATIONS),
        "nodes": ordered_nodes,
        "edges": ordered_edges,
    }


def build_from_files(
    warehouse: Path = WAREHOUSE,
    groups_csv: Path = GROUPS_CSV,
    events_csv: Path = EVENTS_CSV,
    links_csv: Path = EVENT_LINKS_CSV,
    approvals_csv: Path = APPROVALS_CSV,
) -> dict:
    """Build the graph from the committed warehouse and reference files."""
    import sqlite3

    with sqlite3.connect(f"file:{Path(warehouse).as_posix()}?mode=ro", uri=True) as conn:
        products = pd.read_sql(
            "SELECT product_name, manufacturer, brand_generic_tag, disease_area, "
            "treatment_category FROM dim_product",
            conn,
        )
        visits = pd.read_sql(
            "SELECT p.product_name, s.specialty_name AS specialty, "
            "SUM(f.patient_visits) AS visits FROM fact_product_visits f "
            "JOIN dim_product p USING (product_id) "
            "JOIN dim_specialty s USING (specialty_id) GROUP BY 1, 2",
            conn,
        )
        last = conn.execute("SELECT MAX(month_id), COUNT(*) FROM dim_month").fetchone()
    return build_graph(
        products,
        pd.read_csv(groups_csv),
        visits,
        pd.read_csv(events_csv),
        pd.read_csv(links_csv),
        pd.read_csv(approvals_csv),
        built_from={"last_month_id": int(last[0]), "months": int(last[1])},
    )


def to_json(graph: dict) -> str:
    return json.dumps(graph, indent=1, ensure_ascii=False) + "\n"


def write_graph(path: Path = GRAPH_JSON, **inputs) -> dict:
    graph = build_from_files(**inputs)
    Path(path).write_text(to_json(graph), "utf-8")
    return graph


class Graph:
    """The committed graph, with the few lookups the site and Claude need."""

    def __init__(self, data: dict):
        self.data = data
        self.nodes: dict[str, dict] = {n["id"]: n for n in data["nodes"]}
        self.edges: list[dict] = data["edges"]
        self._out: dict[str, list[dict]] = defaultdict(list)
        self._in: dict[str, list[dict]] = defaultdict(list)
        for edge in self.edges:
            self._out[edge["source"]].append(edge)
            self._in[edge["target"]].append(edge)

    @classmethod
    def load(cls, path: Path = GRAPH_JSON) -> Graph:
        return cls(json.loads(Path(path).read_text("utf-8")))

    def find(self, text: str, kind: str | None = None, limit: int = 10) -> list[dict]:
        """Nodes whose label matches `text`: exact (any case) first, then as a substring."""
        wanted = str(text).strip().lower()
        if not wanted:
            return []
        pool = [n for n in self.nodes.values() if kind in (None, n["type"])]
        exact = [n for n in pool if n["label"].lower() == wanted]
        partial = [n for n in pool if wanted in n["label"].lower() and n not in exact]
        return (exact + sorted(partial, key=lambda n: n["label"]))[:limit]

    def neighbors(
        self, node: str, relation: str | None = None, direction: str = "both"
    ) -> list[dict]:
        """Edges touching a node, each with the node at the other end as `other`."""
        if node not in self.nodes:
            raise KeyError(node)
        out = []
        if direction in ("out", "both"):
            out += [
                {**e, "other": self.nodes[e["target"]], "direction": "out"} for e in self._out[node]
            ]
        if direction in ("in", "both"):
            out += [
                {**e, "other": self.nodes[e["source"]], "direction": "in"} for e in self._in[node]
            ]
        return [e for e in out if relation in (None, e["relation"])]

    def to_networkx(self):
        """The graph as a networkx MultiDiGraph (networkx is imported only here)."""
        import networkx as nx

        graph = nx.MultiDiGraph()
        for node in self.data["nodes"]:
            graph.add_node(node["id"], **node)
        for edge in self.edges:
            graph.add_edge(edge["source"], edge["target"], **edge)
        return graph


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild data/reference/knowledge_graph.json.")
    parser.add_argument("--out", default=str(GRAPH_JSON))
    args = parser.parse_args(argv)
    graph = write_graph(Path(args.out))
    print(f"wrote {args.out}: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
