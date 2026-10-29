"""Tests for the knowledge graph (src/.../knowledge_graph.py, DL-87).

Small hand-built inputs check the builder's rules; the committed graph is then checked against the
committed warehouse and reference files: it is up to date, every fact names its source, and the
visit weights add up to the warehouse's own totals.
"""

import json
import sqlite3

import pandas as pd
import pytest

from oa_market_intelligence import knowledge_graph as kg

REAL = kg.WAREHOUSE.exists() and kg.GRAPH_JSON.exists()
real_only = pytest.mark.skipif(not REAL, reason="no published warehouse or committed graph")


# ------------------------------------------------------------------ the builder, by hand


def _inputs():
    products = pd.DataFrame(
        {
            "product_name": ["ZILRETTA", "KENALOG", "DEPO-MEDROL", "ADVIL"],
            "manufacturer": ["PACIRA PHARM", "BMS", "PFIZER", None],
            "brand_generic_tag": ["BRANDED GENERIC", "BRANDED GENERIC", "BRAND", "OTHER"],
            "disease_area": ["OA", "OA", "OA", "OA"],
            "treatment_category": [
                "branded_injectable",
                "generic_corticosteroid",
                "generic_corticosteroid",
                "nsaid_otc",
            ],
        }
    )
    groups = pd.DataFrame(
        {
            "product_name": ["ZILRETTA", "KENALOG", "DEPO-MEDROL", "ADVIL"],
            "group": ["zilretta", "triamcinolone_ir", "other_injectable_corticosteroid", "other"],
            "rule": ["r1", "r2", "r3", "r4"],
        }
    )
    visits = pd.DataFrame(
        {
            "product_name": ["ZILRETTA", "ZILRETTA", "KENALOG", "DEPO-MEDROL", "ADVIL"],
            "specialty": ["ORTHO", "PM&R", "ORTHO", "ORTHO", "FAMILY"],
            "visits": [30, 10, 200, 50, 400],
        }
    )
    events = pd.DataFrame(
        {
            "event_date": ["2017-10-06", "2020-03-11"],
            "event_end_date": [None, None],
            "event_type": ["regulatory", "public_health"],
            "description": ["FDA approves Zilretta", "WHO declares a pandemic"],
            "source": ["letter", "10-K"],
            "verified": [1, 1],
        }
    )
    links = pd.DataFrame(
        {
            "event_date": ["2017-10-06"],
            "match": ["FDA approves Zilretta"],
            "entity": ["product:ZILRETTA"],
            "note": [""],
        }
    )
    approvals = pd.DataFrame({"product_name": ["ZILRETTA"], "earliest_orig": ["2017-10-06"]})
    return products, groups, visits, events, links, approvals


def _graph(**changes):
    args = list(_inputs())
    names = ["products", "groups", "visits", "events", "links", "approvals"]
    for name, value in changes.items():
        args[names.index(name)] = value
    return kg.Graph(kg.build_graph(*args))


def test_a_product_gets_its_manufacturer_class_area_and_group_each_once():
    graph = _graph()
    mine = {
        e["relation"]: e["other"]["id"]
        for e in graph.neighbors("product:ZILRETTA", direction="out")
    }
    assert mine == {
        "made_by": "manufacturer:PACIRA PHARM",
        "in_class": "drug_class:branded_injectable",
        "in_disease_area": "disease_area:OA",
        "in_group": "group:zilretta",
    }
    assert graph.nodes["product:ZILRETTA"]["first_fda_approval"] == "2017-10-06"
    # a product with no manufacturer gets no manufacturer edge, not a made-up one
    assert [e for e in graph.neighbors("product:ADVIL", "made_by")] == []


def test_zilretta_is_compared_with_the_two_injectable_steroid_groups_only():
    graph = _graph()
    compared = {e["other"]["id"] for e in graph.neighbors("group:zilretta", "compared_with", "out")}
    assert compared == {
        "group:triamcinolone_ir",
        "group:other_injectable_corticosteroid",
    }


def test_group_visits_by_specialty_are_summed_and_shares_add_to_one():
    graph = _graph()
    edges = {e["other"]["id"]: e for e in graph.neighbors("group:zilretta", "prescribed_by", "out")}
    assert edges["specialty:ORTHO"]["visits"] == 30 and edges["specialty:PM&R"]["visits"] == 10
    assert edges["specialty:ORTHO"]["share_of_group_visits"] == pytest.approx(0.75)
    assert sum(e["share_of_group_visits"] for e in edges.values()) == pytest.approx(1.0)


def test_events_sit_in_the_market_and_link_only_where_their_text_names_an_entity():
    graph = _graph()
    assert {e["other"]["id"] for e in graph.neighbors("event:1", "occurred_in")} == {kg.MARKET_ID}
    assert {e["other"]["id"] for e in graph.neighbors("event:1", "involves")} == {
        "product:ZILRETTA"
    }
    assert graph.neighbors("event:2", "involves") == []  # the pandemic names no product


def test_there_is_no_causal_relation():
    graph = _graph()
    assert {e["relation"] for e in graph.edges} <= set(kg.RELATIONS)
    assert not any("caus" in r or "driv" in r or "affect" in r for r in kg.RELATIONS)


def test_bad_inputs_fail_loudly():
    products, groups, visits, events, links, approvals = _inputs()
    with pytest.raises(ValueError, match="twice"):
        _graph(products=pd.concat([products, products.iloc[[0]]]))
    with pytest.raises(ValueError, match="without a competitor group"):
        _graph(groups=groups.iloc[:3])
    with pytest.raises(ValueError, match="matches 0 events"):
        _graph(links=links.assign(match="nothing like this"))
    with pytest.raises(ValueError, match="unknown entity"):
        _graph(links=links.assign(entity="product:NOPE"))
    with pytest.raises(ValueError, match="no competitor group"):
        _graph(
            visits=pd.concat([visits, pd.DataFrame([["X", "ORTHO", 1]], columns=visits.columns)])
        )


def test_a_link_that_matches_two_events_is_refused():
    products, groups, visits, events, links, approvals = _inputs()
    twin = events.iloc[[0]].copy()
    with pytest.raises(ValueError, match="matches 2 events"):
        _graph(events=pd.concat([events, twin]))


def test_the_same_inputs_give_the_same_graph_byte_for_byte():
    first, second = kg.to_json(_graph().data), kg.to_json(_graph().data)
    assert first == second and first.endswith("\n")


def test_find_neighbors_and_networkx():
    graph = _graph()
    # exact labels first (the product and the group), then labels that only contain the word
    assert [n["id"] for n in graph.find("zilretta")] == [
        "product:ZILRETTA",
        "group:zilretta",
        "event:1",
    ]
    assert [n["id"] for n in graph.find("zilretta", kind="product")] == ["product:ZILRETTA"]
    assert graph.find("") == [] and graph.find("no such thing") == []
    assert [n["id"] for n in graph.find("kenal")] == ["product:KENALOG"]  # a part of a name
    assert {e["direction"] for e in graph.neighbors("product:ZILRETTA")} == {"out", "in"}
    with pytest.raises(KeyError):
        graph.neighbors("product:NOPE")
    nx = pytest.importorskip("networkx")  # not a pinned dependency of the pipeline
    nxg = graph.to_networkx()
    assert isinstance(nxg, nx.MultiDiGraph)
    assert nxg.number_of_nodes() == len(graph.nodes) and nxg.number_of_edges() == len(graph.edges)


# ------------------------------------------------------------------ the committed graph


@real_only
def test_the_committed_graph_is_the_one_the_inputs_build():
    committed = kg.GRAPH_JSON.read_text("utf-8").replace("\r\n", "\n")
    assert committed == kg.to_json(kg.build_from_files()), (
        "run: python -m oa_market_intelligence.knowledge_graph"
    )


@real_only
def test_every_edge_has_known_ends_a_named_source_and_every_node_is_connected():
    graph = kg.Graph.load()
    for edge in graph.edges:
        assert edge["source"] in graph.nodes and edge["target"] in graph.nodes
        assert edge["evidence"] in graph.data["sources"] or edge["evidence"] == "events.csv"
        assert edge["relation"] in kg.RELATIONS
    touched = {e["source"] for e in graph.edges} | {e["target"] for e in graph.edges}
    assert set(graph.nodes) == touched  # no orphan node
    assert {n["type"] for n in graph.nodes.values()} == set(kg.NODE_TYPES)


@real_only
def test_the_products_and_events_are_all_there_with_one_of_each_basic_edge():
    graph = kg.Graph.load()
    products = [n for n in graph.nodes.values() if n["type"] == "product"]
    assert len(products) == 160
    for product in products:
        out = [e["relation"] for e in graph.neighbors(product["id"], direction="out")]
        assert out.count("in_class") == out.count("in_disease_area") == out.count("in_group") == 1
        assert out.count("made_by") <= 1
    events = pd.read_csv(kg.EVENTS_CSV)
    assert len([n for n in graph.nodes.values() if n["type"] == "event"]) == len(events) == 14
    assert len(graph.neighbors(kg.MARKET_ID, "occurred_in", "in")) == 14
    assert all(
        e["relation"] != "involves" or e["evidence"] == "event_links.csv" for e in graph.edges
    )


@real_only
def test_zilretta_facts_equal_the_warehouse_and_the_site():
    graph = kg.Graph.load()
    zilretta = graph.nodes["product:ZILRETTA"]
    assert zilretta["first_fda_approval"] == "2017-10-06"
    assert [e["other"]["label"] for e in graph.neighbors("product:ZILRETTA", "made_by")] == [
        "PACIRA PHARM"
    ]
    with sqlite3.connect(f"file:{kg.WAREHOUSE.as_posix()}?mode=ro", uri=True) as conn:
        total = conn.execute(
            "SELECT SUM(f.patient_visits) FROM fact_product_visits f "
            "JOIN dim_product p USING (product_id) WHERE p.product_name = 'ZILRETTA'"
        ).fetchone()[0]
        approval = conn.execute(
            "SELECT fda_approval_date FROM dim_product WHERE product_name = 'ZILRETTA'"
        ).fetchone()[0]
    weights = sum(e["visits"] for e in graph.neighbors("group:zilretta", "prescribed_by", "out"))
    assert weights == total and approval == zilretta["first_fda_approval"]


@real_only
def test_group_visit_weights_add_up_to_the_warehouse_total_and_shares_to_one():
    graph = kg.Graph.load()
    with sqlite3.connect(f"file:{kg.WAREHOUSE.as_posix()}?mode=ro", uri=True) as conn:
        total = conn.execute("SELECT SUM(patient_visits) FROM fact_product_visits").fetchone()[0]
    edges = [e for e in graph.edges if e["relation"] == "prescribed_by"]
    assert sum(e["visits"] for e in edges) == total
    for group in [n for n in graph.nodes.values() if n["type"] == "group"]:
        shares = [
            e["share_of_group_visits"] for e in graph.neighbors(group["id"], "prescribed_by", "out")
        ]
        assert sum(shares) == pytest.approx(1.0, abs=1e-3)


@real_only
def test_the_event_links_point_at_the_entities_their_text_names():
    graph = kg.Graph.load()
    linked = {(e["source"], e["target"]) for e in graph.edges if e["relation"] == "involves"}
    by_date = {
        n["date"] + n["label"][:12]: n["id"] for n in graph.nodes.values() if n["type"] == "event"
    }
    assert (by_date["2021-11-19Pacira compl"], "manufacturer:PACIRA PHARM") in linked
    assert (by_date["2021-11-19Pacira compl"], "product:ZILRETTA") in linked
    assert not any(
        "dip" in graph.nodes[s]["label"].lower() for s, _ in linked
    )  # the 2024 dip is unlinked
    text = json.loads(kg.GRAPH_JSON.read_text("utf-8"))
    dip = next(
        n for n in text["nodes"] if n["type"] == "event" and n["label"].startswith("Dip in IQVIA")
    )
    assert dip["verified"] == 0  # the cause of the dip stays unverified in the graph too
