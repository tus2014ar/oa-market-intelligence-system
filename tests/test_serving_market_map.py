"""Tests for the Market map (src/.../serving/market_map.py, DL-89): what each view contains, that
every line has a source and every point a tooltip, that derived lines say they are derived, and
that the map never draws a cause. The drawing itself needs pyvis and is tested where it is
installed (the CI job `site`)."""

import re

import pytest

from oa_market_intelligence import knowledge_graph as kg
from oa_market_intelligence.serving import market_map as mm

pytestmark = pytest.mark.skipif(not kg.GRAPH_JSON.exists(), reason="no committed graph")


@pytest.fixture(scope="module")
def graph():
    return kg.Graph.load()


def _ids(view):
    return {n["id"] for n in view["nodes"]}


@pytest.mark.parametrize("name", list(mm.VIEWS))
def test_every_view_has_whole_edges_tooltips_and_a_source_on_every_line(graph, name):
    view = mm.view_graph(graph, name)
    ids = [n["id"] for n in view["nodes"]]
    assert len(ids) == len(set(ids)) and view["title"] == mm.VIEWS[name]
    assert all(e["from"] in ids and e["to"] in ids for e in view["edges"])
    assert all(n["title"] and n["shape"] and n["color"].startswith("#") for n in view["nodes"])
    assert all("Source:" in e["title"] and e["evidence"] for e in view["edges"])
    assert {e["relation"] for e in view["edges"]} <= set(kg.RELATIONS) | {"makes_products_in"}
    assert "not a market fact" in view["note"] and "context only" in view["note"]


def test_the_competitive_set_is_zilretta_its_groups_their_makers_and_its_specialties(graph):
    view = mm.view_graph(graph, "competitive_set", top=5)
    ids = _ids(view)
    assert {"product:ZILRETTA", "group:zilretta", "group:triamcinolone_ir"} <= ids
    assert "group:other_injectable_corticosteroid" in ids
    makers = [n for n in view["nodes"] if n["type"] == "manufacturer"]
    assert "manufacturer:PACIRA PHARM" in ids and 0 < len(makers) <= 3 * 5
    specialties = [n for n in view["nodes"] if n["type"] == "specialty"]
    assert len(specialties) == 5 and "specialty:ORTHOPEDIC SURGERY" in ids


def test_the_no_manufacturer_placeholder_is_not_drawn_as_a_company(graph):
    for name in mm.VIEWS:
        view = mm.view_graph(graph, name, top=15)
        assert not any("no manufacturer" in n["label"].lower() for n in view["nodes"])
    assert kg.is_placeholder_manufacturer(
        " No Manufacturer "
    ) and not kg.is_placeholder_manufacturer("PFIZER")


def test_manufacturer_lines_are_marked_derived_and_dashed(graph):
    view = mm.view_graph(graph, "competitive_set")
    derived = [e for e in view["edges"] if e["relation"] == "makes_products_in"]
    assert derived and all(e["derived"] and e["dashes"] for e in derived)
    assert all("Derived:" in e["title"] for e in derived)
    assert not any(e["derived"] for e in view["edges"] if e["relation"] != "makes_products_in")
    compared = [e for e in view["edges"] if e["relation"] == "compared_with"]
    assert len(compared) == 2 and all(e["dashes"] for e in compared)


def test_the_slider_is_clamped_and_events_can_be_hidden(graph):
    small = mm.view_graph(graph, "competitive_set", top=1)
    big = mm.view_graph(graph, "competitive_set", top=99)
    count = lambda v: len([n for n in v["nodes"] if n["type"] == "specialty"])  # noqa: E731
    assert count(small) == mm.MIN_TOP and count(big) == mm.MAX_TOP
    quiet = mm.view_graph(graph, "competitive_set", show_events=False)
    assert not any(n["type"] == "event" for n in quiet["nodes"])
    loud = mm.view_graph(graph, "competitive_set", show_events=True)
    events = [n for n in loud["nodes"] if n["type"] == "event"]
    # the seven events that name Zilretta or Pacira (the guideline, payment and data events do not)
    assert sorted(n["id"] for n in events) == [
        "event:1",
        "event:10",
        "event:11",
        "event:14",
        "event:2",
        "event:4",
        "event:5",
    ]
    shown = _ids(loud)
    assert all(
        e["to"] in shown for e in loud["edges"] if e["relation"] == "involves"
    )  # an event hangs only on something that is drawn


def test_the_specialty_view_compares_the_groups_by_their_own_mix(graph):
    view = mm.view_graph(graph, "who_uses_it", top=6)
    groups = [n for n in view["nodes"] if n["type"] == "group"]
    specialties = [n for n in view["nodes"] if n["type"] == "specialty"]
    assert len(groups) == 3 and len(specialties) == 6
    assert all(e["relation"] == "prescribed_by" for e in view["edges"])
    zil = {e["to"]: e for e in view["edges"] if e["from"] == "group:zilretta"}
    smallest = min(zil.values(), key=lambda e: e["width"])
    assert zil["specialty:ORTHOPEDIC SURGERY"]["width"] > smallest["width"]
    assert "% of this group's visits" in zil["specialty:ORTHOPEDIC SURGERY"]["title"]


def test_the_events_view_has_every_event_and_no_cause(graph):
    view = mm.view_graph(graph, "events")
    events = [n for n in view["nodes"] if n["type"] == "event"]
    assert len(events) == 14 and "market:iqvia_nmta" in _ids(view)
    grey = [n for n in events if n["color"] == mm.COLORS["unverified_event"]]
    assert len(grey) == 2 and all("Not verified" in n["title"] for n in grey)
    dip = next(n for n in events if "Dip in IQVIA" in n["title"])
    assert [e for e in view["edges"] if dip["id"] in (e["from"], e["to"])] == [
        e for e in view["edges"] if e["relation"] == "occurred_in" and e["from"] == dip["id"]
    ]  # the 2024 dip is tied to the market only, never to anything it might be blamed on
    assert {e["relation"] for e in view["edges"]} == {"occurred_in", "involves"}


def test_a_view_is_the_same_every_time_and_an_unknown_view_is_refused(graph):
    assert mm.view_graph(graph, "competitive_set", 8) == mm.view_graph(graph, "competitive_set", 8)
    with pytest.raises(ValueError, match="view must be one of"):
        mm.view_graph(graph, "everything")


def test_node_details_give_attributes_connections_and_sources(graph):
    detail = mm.node_details(graph, "product:ZILRETTA")
    assert detail["label"] == "ZILRETTA" and detail["type"] == "Product"
    assert detail["attributes"]["first_fda_approval"] == "2017-10-06"
    assert detail["total_connections"] == 10 and len(detail["connections"]) == 10
    assert {"dim_product", "competitor_groups.csv", "event_links.csv"} == set(detail["sources"])
    group = mm.node_details(graph, "group:zilretta", limit=3)
    assert len(group["connections"]) == 3 and group["total_connections"] > 3
    assert group["connections"][0]["other"] == "ORTHOPEDIC SURGERY"
    assert group["connections"][0]["share_of_group_visits_pct"] == pytest.approx(37.6, abs=0.1)
    assert mm.node_details(graph, "drug_class:nsaid_otc")["type"] == "Drug class"
    with pytest.raises(KeyError):
        mm.node_details(graph, "product:NOPE")


def test_the_event_table_lists_every_event_oldest_first_with_who_it_names(graph):
    table = mm.event_table(graph)
    assert len(table) == 14 and list(table["date"]) == sorted(table["date"])
    assert list(table.columns) == ["date", "until", "type", "event", "names", "verified", "source"]
    assert set(table["verified"]) == {"yes", "no"} and (table["verified"] == "no").sum() == 2
    pacira = table[table["event"].str.startswith("Pacira completes")].iloc[0]
    assert "PACIRA PHARM" in pacira["names"] and "ZILRETTA" in pacira["names"]
    assert table["source"].str.len().min() > 0


def test_the_legend_names_each_kind_of_point_once(graph):
    kinds = [name for _, _, name in mm.legend(mm.view_graph(graph, "competitive_set"))]
    assert kinds == ["Product", "Competitor group", "Manufacturer", "Specialty", "Event"]
    events = [name for _, _, name in mm.legend(mm.view_graph(graph, "events"))]
    assert "Event (not verified)" in events and "Market" in events


# ------------------------------------------------------------------ the drawing (needs pyvis)


def test_the_layout_is_fixed_and_inside_the_canvas(graph):
    pytest.importorskip("networkx")  # not a pinned dependency of the pipeline
    view = mm.view_graph(graph, "competitive_set")
    first, second = mm.positions(view), mm.positions(view)
    assert first == second and set(first) == _ids(view)
    assert all(abs(x) <= 700 and abs(y) <= 420 for x, y in first.values())


def test_the_map_page_is_self_contained_with_a_legend_and_tooltips(graph):
    pytest.importorskip("pyvis")
    view = mm.view_graph(graph, "competitive_set")
    html = mm.to_html(view)
    assert "Hover a point for its facts and source" in html and "Competitor group" in html
    assert "ZILRETTA" in html and "Source: dim_product" in html
    assert "Source: competitor_groups.csv" in html and "Source: fact_product_visits" in html
    assert re.findall(r'(?:src|href)="https?://[^"]+"', html) == []  # draws with no network
    assert "bootstrap" not in html.lower().split("<body>")[0]
    assert "refit" in html  # the map is fitted to its frame once the frame has a size
    moving = mm.to_html(view, physics=True)
    assert '"enabled": true' in moving.lower().replace('"enabled":true', '"enabled": true')
    still = mm.to_html(view, physics=False)
    assert '"enabled": false' in still.lower().replace('"enabled":false', '"enabled": false')


def test_the_map_page_can_be_drawn_in_dark_colours_to_match_the_page(graph):
    pytest.importorskip("pyvis")
    view = mm.view_graph(graph, "events")
    dark, light = mm.to_html(view, dark=True), mm.to_html(view)
    assert f"body{{background:{mm.DARK['bg']}" in dark and mm.DARK["bg"] not in light
    assert f"body{{background:{mm.LIGHT['bg']}" in light
    assert mm.DARK["text"] in dark and mm.LIGHT["text"] in light  # labels and legend follow too
