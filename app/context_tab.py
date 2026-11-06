"""The Context tab (DL-102): the company's sales set beside IQVIA's visits, what was tested to
explain the gap, Medicare adoption by state and the dated events. A thin page over
`serving.context`, `serving.style` and `serving.charts`, which hold (and test) the logic, the
wording and the drawing. Every number here is stored output of the public-data analyses or the
company's filings; the tab sets sources side by side and says nothing about cause.
"""

from __future__ import annotations

import streamlit as st

from oa_market_intelligence.serving import charts, context, links, style


def _here(heading: str) -> str:
    """A link to a section further down this page."""
    return "#" + links.anchor_id(heading)


def _cards(years, head, national) -> None:
    peak = national.loc[national["pct"].idxmax()]
    last = national.iloc[-1]
    cards = st.columns(4)
    cards[0].markdown(
        style.kpi_card(
            f"Net sales, {head['year']} vs {head['base_year']}",
            f"{head['sales_vs_base_pct']:+.0f}%",
            f"Zilretta net sales, all payers. {head['year']} against the previous year: "
            f"{head['sales_change_pct']:+.1f}%.",
            delta=head["sales_vs_base_pct"],
            href=_here("Company sales against IQVIA visits"),
        ),
        unsafe_allow_html=True,
    )
    cards[1].markdown(
        style.kpi_card(
            f"IQVIA visits, {head['year']} vs {head['base_year']}",
            f"{head['visits_vs_base_pct']:+.0f}%",
            f"Zilretta visits in IQVIA's sample. {head['year']} against the previous year: "
            f"{head['visits_change_pct']:+.1f}%.",
            delta=head["visits_vs_base_pct"],
            href=_here("Company sales against IQVIA visits"),
        ),
        unsafe_allow_html=True,
    )
    cards[2].markdown(
        style.kpi_card(
            f"Medicare providers billing it, {int(last['year'])}",
            f"{last['pct']:.2f}%",
            f"Of visible Medicare providers. Peak {peak['pct']:.2f}% in {int(peak['year'])}.",
            href=_here("Medicare adoption by state"),
        ),
        unsafe_allow_html=True,
    )
    cards[3].markdown(
        style.kpi_card(
            "Tested explanations for the gap",
            "None",
            "Four checks; one holds on direction only. None accounts for the size.",
            href=_here("What was tested to explain the gap"),
        ),
        unsafe_allow_html=True,
    )


def _sales_and_visits(ctx, years, findings_items) -> None:
    left, right = st.columns([2.1, 1])
    with left.container(border=True):
        st.markdown("**Company net sales and IQVIA visits**")
        st.altair_chart(
            charts.company_visits_chart(context.indexed_quarters(ctx["company"])), width="stretch"
        )
        st.caption(
            "Each line is a percentage of its own average quarter in 2021, so the two share one "
            "axis. Net sales are the company's filed figures (Flexion to the third quarter of "
            "2021, Pacira from 2022; the fourth quarter of each year is derived). Visits are "
            "IQVIA's sample, so compare direction and timing, not levels."
        )
    with right.container(border=True, height="stretch"):
        st.markdown("**Change from the year before**")
        rows = []
        for r in years.itertuples():
            if r.year == years["year"].min():
                rows.append((str(r.year), "–", None, "–"))
            else:
                rows.append(
                    (
                        str(r.year),
                        f"{r.sales_change_pct:+.1f}%",
                        r.visits_change_pct,
                        f"{r.visits_change_pct:+.1f}%",
                    )
                )
        st.markdown(style.rank_rows(rows, ("Year", "Sales", "Visits")), unsafe_allow_html=True)
        st.caption("The two move together to 2022. From 2023 sales keep rising while visits fall.")
        st.download_button(
            "Download sales and visits (CSV)",
            context.csv_sales_visits(years),
            "zilretta_sales_vs_visits.csv",
            "text/csv",
        )
    st.markdown(
        style.callout(context.decline_caveat(findings_items), "down"), unsafe_allow_html=True
    )


def _checks(ctx) -> None:
    checks = context.gap_checks(ctx["verdicts"])
    with st.container(border=True):
        st.markdown(
            style.test_rows(
                [(c["question"], c["check"], c["result"], False, c["number"], "") for c in checks]
            ),
            unsafe_allow_html=True,
        )
        st.caption(
            f"{context.overall_gap(ctx['verdicts'])} Comparing 2021 with 2024; the four checks "
            "and their thresholds were written down before any code ran. Not tested: how well "
            "IQVIA covers Zilretta's settings or specialties, and company-side causes "
            "(inventory, gross-to-net adjustments, channel mix). None of this identifies a cause."
        )


def _states(ctx) -> None:
    states = ctx["states"]
    national = context.national_adoption(states)
    years = [int(y) for y in national["year"]]
    with st.container(border=True):
        year = st.segmented_control(
            "Year", years, default=years[-1], key="ctx_year", label_visibility="collapsed"
        )
        year = year or years[-1]
        view = context.state_year(states, year)
        left, right = st.columns([2.1, 1])
        with left:
            st.altair_chart(charts.state_tile_map(view), width="content")
        with right:
            rate = float(national.loc[national["year"] == year, "pct"].iloc[0])
            rho = context.rank_stability(states)
            st.markdown(
                style.stand_rows(
                    [
                        (f"National, {year}", f"{rate:.2f}%"),
                        ("States shown", f"{int((view['reported'] == 1).sum())}"),
                        ("Order kept, 2022 to 2024", f"{rho:.2f}"),
                    ]
                ),
                unsafe_allow_html=True,
            )
            below = context.below_national(view)
            st.markdown("**Furthest below the national rate**")
            st.markdown(
                style.rank_rows(
                    [
                        (
                            r.state_name,
                            f"{r.adoption_pct:.2f}%",
                            None,
                            f"{r.headroom:.0f} providers",
                        )
                        for r in below.itertuples()
                    ],
                    ("State", "Adoption", "Room"),
                ),
                unsafe_allow_html=True,
            )
        st.caption(
            "The share of Medicare providers who billed Zilretta's code (J3304) among those "
            "billing any steroid injection code, from public CMS data on Original Medicare. It "
            "is not IQVIA data, and Medicare Advantage patients are not in it. A pale tile is a "
            "state with under 100 visible providers, so its rate has a wide interval. Room is "
            "the providers who would need to bill for the state to reach the national rate. The "
            "states keep much the same order from 2022 to 2024 (rank correlation "
            f"{context.rank_stability(states):.2f}), which passes the project's own test (0.7) for using the order."
        )


def render(ctx: dict, findings_items: list[dict], data_chip) -> None:
    """Draw the tab from the public-data subset `ctx` (company, states, events, verdicts) and the
    stored findings."""
    data_chip()
    years = context.annual(ctx["company"])
    head = context.headline(years)
    national = context.national_adoption(ctx["states"])
    _cards(years, head, national)

    st.markdown("##### Company sales against IQVIA visits")
    _sales_and_visits(ctx, years, findings_items)

    st.markdown("##### What was tested to explain the gap")
    _checks(ctx)

    st.markdown("##### Medicare adoption by state")
    _states(ctx)

    st.markdown("##### Events, oldest first")
    with st.container(border=True):
        st.markdown(style.timeline_rows(context.event_rows(ctx["events"])), unsafe_allow_html=True)
        st.caption(
            "Events are context only: nothing here says an event changed the share, and the "
            "cause of the March to July 2024 data-capture dip is unverified."
        )
    st.caption(
        "Company sales come from SEC filings and Medicare figures from public CMS files; the "
        "visits are IQVIA's. They are different sources and are set side by side, not combined."
    )
