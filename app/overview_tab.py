"""The Overview (DL-103): four cards with their own small lines, the share so far with the national
forecast, what to use and what not to trust, what to read first, what each tab answers and the two
downloads. A thin page over `serving.views`, `serving.overview`, `serving.forecasts`,
`serving.style` and `serving.charts`, which hold (and test) the logic and the wording.
"""

from __future__ import annotations

import streamlit as st

from oa_market_intelligence.serving import charts, forecasts, links, overview, style, views

FROM = "Overview"
# each "what to use" card links to the part of Forecasts that shows it
SEE = {
    "National share forecast": ("See the national forecast", None),
    "Segment forecast": ("See the segment forecasts", "Segment forecasts"),
    "Decline alert": ("See the alert list", "Decline alert, 1 month ahead"),
}


def render(D: dict, status: dict, head: dict, adoption: dict) -> None:
    """Draw the Overview from the loaded tables `D`, the run status, the headline numbers and the
    adoption map."""
    if status["banner"]:
        st.warning(status["banner"])
    low, high = head["next_month_range90"]
    cards = st.columns(4)
    cards[0].markdown(
        style.kpi_card(
            f"Share in {head['latest_month']}",
            views.pct(head["latest_share"]),
            "The line is the last 36 months.",
            spark=views.share_series(D["monthly"]),
            href=links.href("Market trend", source=FROM),
        ),
        unsafe_allow_html=True,
    )
    cards[1].markdown(
        style.kpi_card(
            f"Peak ({head['peak_month']})",
            views.pct(head["peak_share"]),
            "The highest month in the series.",
            href=links.href("Market trend", source=FROM),
        ),
        unsafe_allow_html=True,
    )
    cards[2].markdown(
        style.kpi_card(
            "Change since the peak",
            f"{100 * head['change_since_peak']:.0f}%",
            f"From {head['peak_month']} to {head['latest_month']}.",
            spark=views.since_peak_series(D["monthly"]),
            area=True,
            href=links.href("Market trend", source=FROM),
        ),
        unsafe_allow_html=True,
    )
    cards[3].markdown(
        style.kpi_card(
            f"Forecast for {head['next_month']}",
            views.pct(head["next_month_forecast"]),
            f"Likely {views.pct(low)} to {views.pct(high)} (90%). It held about 9 months in 10 "
            "at 1 month ahead in testing.",
            href=links.href("Forecasts", source=FROM),
        ),
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown("**Share so far, and the national forecast**")
        st.altair_chart(
            charts.national_fan_chart(forecasts.fan(D["monthly"], D["pred"])), width="stretch"
        )
        st.caption(
            "Share so far (blue) and the national forecast at 1, 3 and 6 months with its 80% and "
            "90% ranges (orange). The forecast is last month's value: no model beat that simple "
            "rule."
        )

    st.markdown("##### What to use, and what not to trust")
    for col, row in zip(st.columns(3), views.use_and_dont(adoption), strict=True):
        with col.container(border=True, height="stretch"):
            st.badge(row["status"], color=row["color"])
            st.markdown(f"**{row['what']}**")
            st.write(f"**Use it for:** {row['use']}")
            st.write(f"**Do not:** {row['dont']}")
            address = links.href("Forecasts", source=FROM, section=SEE[row["what"]][1])
            st.markdown(style.link_line(SEE[row["what"]][0], address), unsafe_allow_html=True)

    st.markdown("##### Read this first")
    with st.container(border=True):
        st.markdown(
            "- The numbers describe **visits in IQVIA's panel**, not company sales, and they do "
            "not explain *why* the share moves.\n"
            f"- {views.bias_sentence(D['pred'])}\n"
            "- Everything here was tested under rules written before any model ran; where "
            "nothing beat a simple rule, the site says so.\n"
            "- Payer, regional and prescription-volume data are not part of this analysis.\n"
            "- Company sales kept rising while these visits fell, and the size of the visits "
            "decline is unverified: see the Context page before quoting it."
        )

    st.markdown("##### What each page answers")
    with st.container(border=True):
        pages = overview.guide()
        addresses = [links.href(title, source=FROM) for title, _ in pages]
        st.markdown(style.guide_rows(pages, addresses), unsafe_allow_html=True)

    left, right = st.columns(2)
    left.download_button(
        "Download the monthly share (CSV)",
        views.monthly_csv(D["monthly"]),
        "zilretta_monthly_share.csv",
        "text/csv",
        width="stretch",
    )
    right.download_button(
        "Download the forecast (CSV)",
        views.forecast_csv(D["pred"]),
        "zilretta_forecast.csv",
        "text/csv",
        width="stretch",
    )
