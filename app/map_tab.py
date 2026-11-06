"""The Market map page (DL-89, moved here in DL-104): the knowledge graph as three views, a closer
look at any point and the events against the share. A thin page over `serving.market_map`.
"""

from __future__ import annotations

import common
import pandas as pd
import streamlit as st

from oa_market_intelligence.serving import charts, market_map


def render(D: dict, graph, data_chip) -> None:
    """Draw the page from the loaded tables `D` and the knowledge graph."""
    data_chip()
    GRAPH = graph
    st.write(
        "The facts behind the numbers (products, makers, specialties and events) as a map you can "
        "explore. Hover over a point for its facts and where they come from; drag to move, scroll "
        "to zoom."
    )
    st.caption(market_map.NOTE)
    with st.container(border=True):
        left, right = st.columns([3, 2])
        view_key = left.radio(
            "What to show", list(market_map.VIEWS), format_func=market_map.VIEWS.get
        )
        top = right.slider(
            "How many manufacturers and specialties",
            market_map.MIN_TOP,
            market_map.MAX_TOP,
            8,
            disabled=view_key == "events",
        )
        show_events = right.checkbox(
            "Show the events that name Zilretta or its maker",
            value=True,
            disabled=view_key != "competitive_set",
        )
        moving = right.checkbox("Let the points drift and spread out", value=False)
    view = market_map.view_graph(GRAPH, view_key, top, show_events)
    with st.container(border=True):
        try:
            st.iframe(market_map.to_html(view, 620, moving, common.dark_page()), height=700)
        except ImportError:
            st.info(
                "The interactive map needs the pyvis package. The details and events below work."
            )

    labels = {n["id"]: f"{n['label']} ({market_map.TYPE_NAMES[n['type']]})" for n in view["nodes"]}
    ids = list(labels)
    choice = st.selectbox(
        "Look closer at one thing in this map",
        ids,
        index=ids.index("product:ZILRETTA") if "product:ZILRETTA" in ids else 0,
        format_func=labels.get,
        key="map-node",
    )
    detail = market_map.node_details(GRAPH, choice)
    st.subheader(detail["label"])
    st.caption(detail["type"])
    facts = [f"**{k.replace('_', ' ')}:** {v}" for k, v in detail["attributes"].items()]
    if facts:
        st.write("  \n".join(facts))
    st.dataframe(pd.DataFrame(detail["connections"]), hide_index=True)
    st.caption(
        f"Showing {len(detail['connections'])} of {detail['total_connections']} connections. "
        "Sources: " + ", ".join(detail["sources"])
    )

    st.subheader("Events against the share")
    st.altair_chart(charts.events_chart(D["monthly"], GRAPH), width="stretch")
    st.caption(
        "Events are context only: nothing here says an event changed the share, and the cause of "
        "the March to July 2024 dip is unverified. Events before the data begins (August 2019) "
        "are in the table only."
    )
    common.explain("map_events")
    st.dataframe(market_map.event_table(GRAPH), hide_index=True)
