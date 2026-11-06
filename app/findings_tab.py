"""The Findings tab (DL-101): the four findings to read first as cards, then every finding grouped
by theme with what it means up front and the evidence folded away. A thin page over
`serving.findings` and `serving.style`, which hold (and test) the grouping and the HTML.
"""

from __future__ import annotations

import streamlit as st

from oa_market_intelligence.serving import findings, style


def render(items: list[dict], data_chip) -> None:
    """Draw the tab from the stored findings (a list of dicts from `data.findings()`)."""
    data_chip()

    st.markdown("##### Read these first")
    cards = st.columns(4)
    for col, item in zip(cards, findings.start_here(items), strict=False):
        col.markdown(
            style.text_card(f"Finding {item['id']}", item["finding"], item["impact"]),
            unsafe_allow_html=True,
        )

    with st.container(border=True, key="find_filters"):
        theme = st.segmented_control(
            "Theme",
            [findings.ALL, *findings.THEMES],
            default=findings.ALL,
            key="find_theme",
        )
    shown = findings.themed(items, theme or findings.ALL)
    total = sum(len(group) for _, group in shown)
    st.caption(
        f"{total} of {len(items)} findings. Each says what it means first; open "
        '"Evidence" for the numbers and where they come from.'
    )
    for name, group in shown:
        spec = findings.THEMES.get(name, {"title": name, "blurb": ""})
        with st.container(border=True):
            st.markdown(f"**{spec['title']}**")
            if spec["blurb"]:
                st.caption(spec["blurb"])
            if name == "The models":
                st.markdown(style.callout(findings.ROUND2_NOTE, "down"), unsafe_allow_html=True)
            st.markdown(
                "".join(
                    style.finding_card(
                        x["id"], x["finding"], x["impact"], x["evidence"], x["source"]
                    )
                    for x in group
                ),
                unsafe_allow_html=True,
            )
    counts = findings.counts(items)
    st.download_button(
        "Download the findings (CSV)", findings.csv(items), "zilretta_findings.csv", "text/csv"
    )
    st.caption(
        "Sources: "
        + "; ".join(f"{n} from {source}" for source, n in counts.items())
        + ". The findings are the same as the table in the project README."
    )
