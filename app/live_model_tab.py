"""The Live model page (DL-133): the "Run the model live" panel with the story around it. What
pressing the button does, step by step, how it works, what it gives a brand manager, what makes the
service dependable and what it does not do. A thin page: the wording is in `serving.livemodel`
(read and tested in one place) and the panel itself in `live_panel`.
"""

from __future__ import annotations

import live_panel
import streamlit as st

from oa_market_intelligence.serving import forecasts, livemodel, style

HORIZON_LABELS = {1: "1 month", 3: "3 months", 6: "6 months"}


def render(D: dict, data_chip) -> None:
    """Draw the page from the loaded tables `D` (needs `pred` and `seg`)."""
    data_chip()
    pred, seg = D["pred"], D["seg"]
    n_segments = int(
        pred[(pred["level"] == "segment") & (pred["horizon"] == 1)].shape[0]
    )  # one row per segment at 1 month

    st.markdown(style.callout(livemodel.HEADLINE, "up"), unsafe_allow_html=True)

    # ---- try it: the panel, with its own horizon choice
    with st.container(border=True, key="lm_horizon_box"):
        horizon = st.segmented_control(
            "Months ahead",
            list(HORIZON_LABELS),
            default=1,
            format_func=HORIZON_LABELS.get,
            key="lm_horizon",
        )
    horizon = horizon or 1
    live_panel.render(forecasts.segment_view(pred, seg, horizon), pred, horizon)
    st.caption(livemodel.DEMO)

    # ---- what happens when you press the button
    st.subheader("What happens when you press the button")
    st.markdown(style.flow_row(livemodel.FLOW), unsafe_allow_html=True)
    for number, (title, plain, detail) in enumerate(livemodel.STEPS, start=1):
        with st.container(border=True):
            st.markdown(f"**{number}. {title}**")
            st.write(plain)
            with st.expander("The technical detail"):
                st.write(detail)

    # ---- what it gives a brand manager
    st.subheader("What it gives a brand manager")
    st.caption(
        f"The service can reproduce all {n_segments} segments the monthly job forecasts, at 1, 3 "
        "and 6 months."
    )
    cols = st.columns(2)
    for index, (label, title, caption) in enumerate(livemodel.BRAND_MANAGER):
        cols[index % 2].markdown(
            style.text_card(label, title, caption, tall=False), unsafe_allow_html=True
        )

    # ---- why it can be relied on
    st.subheader("What makes it dependable")
    st.caption(
        "The controls a real service has, each written down in the repository and tested. They "
        "are on a small scale: see what it does not do, below."
    )
    st.markdown(
        style.guide_rows(
            [(control, f"{what} Written down in: {where}.") for control, what, where in livemodel.DEPENDABLE]
        ),
        unsafe_allow_html=True,
    )

    # ---- what it does not do
    st.subheader("What it does not do")
    with st.container(border=True):
        for line in livemodel.LIMITS:
            st.markdown(f"- {line}")
