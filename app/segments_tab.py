"""The Segments tab (DL-98): a filter bar, four cards, the ranked groups as bars, how concentrated
Zilretta's visits are, a specialty-by-age grid, the groups over time, and the table with a
download. A thin page over `serving.segments`, `serving.style` and `serving.charts`, which hold
(and test) the logic, the wording and the drawing.
"""

from __future__ import annotations

import streamlit as st

from oa_market_intelligence.serving import charts, segments, style, trend

SHOW = {"share": "Share", "visits": "Visits", "change": "Change"}
SHOW_COLUMN = {"share": "share_pct", "visits": "zilretta_visits", "change": "delta_pp"}
PERIOD_LABELS = {"1Y": "1Y", "3Y": "3Y", "5Y": "5Y", "All": "All"}
GROUP_LABELS = {"specialty": "Specialty", "age_band": "Age", "gender": "Gender"}


def _filters() -> tuple[str, str, str]:
    with st.container(border=True, key="seg_filters"):
        cols = st.columns([2.6, 1.9, 1.9])
        by = cols[0].segmented_control(
            "Group visits by",
            list(segments.GROUPS),
            default="specialty",
            format_func=GROUP_LABELS.get,
            key="seg_by",
        )
        period = cols[1].segmented_control(
            "Period",
            list(PERIOD_LABELS),
            default="All",
            format_func=PERIOD_LABELS.get,
            key="seg_period",
        )
        show = cols[2].segmented_control(
            "Show",
            list(SHOW),
            default="share",
            format_func=SHOW.get,
            key="seg_show",
        )
    return by or "specialty", period or "All", show or "share"


def _card(label: str, found: tuple[str, float] | None, caption: str, *, as_change: bool) -> str:
    if found is None:
        return style.kpi_card(label, "n/a", caption)
    name, value = found
    text = style.signed(value) if as_change else f"{value:.2f}%"
    return style.kpi_card(label, text, f"{name} · {caption}", delta=value if as_change else None)


def render(D: dict, data_chip) -> None:
    """Draw the tab from the loaded tables `D` (needs `seg`)."""
    st.markdown(style.CSS, unsafe_allow_html=True)
    data_chip()
    seg = D["seg"]
    by, period, show = _filters()
    name = segments.GROUPS[by].lower()

    table = segments.group_table(seg, by, period)
    notes = segments.extremes(table)
    conc = segments.concentration(table)

    # ---- headline cards
    cards = st.columns(4)
    cards[0].markdown(
        _card(
            "Highest share",
            notes["highest"],
            f"Among {name} groups with {segments.SMALL_VISITS:,}+ visits",
            as_change=False,
        ),
        unsafe_allow_html=True,
    )
    top = None if conc["top"] is None else (conc["top"], conc["top_pct"])
    cards[1].markdown(
        _card(
            "Largest source of visits", top, "of Zilretta's visits come from it", as_change=False
        ),
        unsafe_allow_html=True,
    )
    window = "last 12 months against the 12 before"
    cards[2].markdown(
        _card("Biggest rise", notes["rising"], window, as_change=True), unsafe_allow_html=True
    )
    cards[3].markdown(
        _card("Biggest fall", notes["falling"], window, as_change=True), unsafe_allow_html=True
    )

    # ---- ranked bars and concentration
    left, right = st.columns([2.1, 1])
    with left.container(border=True):
        st.markdown(f"**Zilretta's {SHOW[show].lower()} by {name}**")
        drawable = table.dropna(subset=[SHOW_COLUMN[show]]) if len(table) else table
        if drawable.empty:
            st.write("Not enough visits to draw this.")
        else:
            st.altair_chart(charts.group_chart(table, show), width="stretch")
        st.caption(
            "Share is Zilretta's visits over the visits of Zilretta, generic steroid injections and "
            f"NSAID/OTC together. Groups under {segments.SMALL_VISITS:,} visits are drawn faded: "
            "one prescription moves a small group's share a lot. Change compares the last 12 "
            "months with the 12 before."
        )
    with right.container(border=True, height="stretch"):
        st.markdown("**How concentrated are the visits?**")
        st.caption(f"Zilretta's visits across {name} groups")
        if conc["top"] is None:
            st.write("No Zilretta visits in this selection.")
        else:
            st.markdown(
                style.stand_rows(
                    [
                        (f"{name.capitalize()} groups ranked", f"{conc['n_groups']}"),
                        ("Largest source", f"{conc['top']} · {conc['top_pct']:.0f}%"),
                        ("Three largest together", f"{conc['top3_pct']:.0f}%"),
                        ("Groups making up 80%", f"{conc['n_for_80']}"),
                    ]
                ),
                unsafe_allow_html=True,
            )
            st.caption(
                "A concentrated brand depends on a few groups; how those groups move matters "
                "more than the rest."
            )
    st.write(segments.sentence(table, by, period))

    # ---- the specialty-by-age grid
    st.markdown("##### Where the share is highest")
    with st.container(border=True):
        cells = segments.cross_table(seg, period)
        st.altair_chart(charts.share_grid(cells), width="stretch")
        st.caption(
            "Specialty by age band over the same period: the twelve specialties with the most "
            f"Zilretta visits and the age bands with at least {segments.MIN_VISITS:,} visits. A grey "
            f"cell has under {segments.MIN_CELL_VISITS} visits, too few to show a rate."
        )

    # ---- groups over time
    st.markdown(f"##### {segments.GROUPS[by]} over time")
    with st.container(border=True):
        options = list(table.sort_values("zilretta_visits", ascending=False)["group"])
        pick = st.multiselect(
            f"{segments.GROUPS[by]} groups to compare",
            options,
            default=options[:5],
            format_func=trend.pretty,
            key=f"seg_pick_{by}",
        )
        if pick:
            st.altair_chart(
                charts.group_trend_chart(segments.group_trend(seg, by, pick)), width="stretch"
            )
            st.caption(
                "Share by calendar year; the first and last years are part years (August 2019 to "
                "July 2025). Every year uses all months of the series, whatever the period above."
            )
        else:
            st.write("Pick at least one group to draw.")

    # ---- the numbers behind it
    with st.expander("The table, and download"):
        columns = ["label", "share_pct", "zilretta_visits", "category_visits", "pct_of_zilretta"]
        st.dataframe(
            table[[*columns, "delta_pp"]].rename(
                columns={
                    "label": segments.GROUPS[by],
                    "share_pct": "Share (%)",
                    "zilretta_visits": "Zilretta visits",
                    "category_visits": "All three classes",
                    "pct_of_zilretta": "Share of Zilretta's visits (%)",
                    "delta_pp": "Change, last 12 months (points)",
                }
            ),
            hide_index=True,
            column_config={
                "Share (%)": st.column_config.NumberColumn(format="%.2f"),
                "Share of Zilretta's visits (%)": st.column_config.NumberColumn(format="%.1f"),
                "Change, last 12 months (points)": st.column_config.NumberColumn(format="%+.2f"),
            },
        )
        st.download_button(
            "Download this table (CSV)",
            segments.csv(table),
            f"zilretta_share_by_{by}.csv",
            "text/csv",
        )
    st.caption(
        "These are visits in IQVIA's panel, not company sales, and nothing here says why a group's "
        "share moved. Small groups are noisy."
    )
