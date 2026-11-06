"""The Data status tab (DL-103): four cards for the last publish run, how the earlier forecasts held,
the versions of the run, the stages and the input extracts. A thin page over `serving.overview`,
`serving.views` and `serving.style`.
"""

from __future__ import annotations

import streamlit as st

from oa_market_intelligence.serving import overview, style, views


def render(run: dict, status: dict, data_chip) -> None:
    """Draw the tab from the last run (`data.last_run`) and its status block."""
    data_chip()
    cards = st.columns(4)
    for col, (label, value, caption) in zip(cards, overview.run_cards(status), strict=True):
        col.markdown(style.kpi_card(label, value, caption), unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left.container(border=True, height="stretch"):
        st.markdown("**Earlier forecasts**")
        st.write(status["drift_text"])
    with right.container(border=True, height="stretch"):
        st.markdown("**Versions of this run**")
        st.markdown(style.stand_rows(overview.version_rows(status)), unsafe_allow_html=True)

    st.markdown("##### Stages of the last run")
    with st.container(border=True):
        st.dataframe(views.stages_frame(run), hide_index=True)
    st.markdown("##### Input extracts")
    with st.container(border=True):
        st.dataframe(views.extracts_frame(run), hide_index=True)
        st.caption(
            "Each input file is identified by the start of its SHA-256 fingerprint, so a changed "
            "extract is noticed."
        )
