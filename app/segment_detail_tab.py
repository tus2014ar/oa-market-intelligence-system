"""The Segment detail page (DL-105): one group on its own, set beside the whole market and beside its
neighbours. Reached by clicking a group anywhere on the site (the click carries the group in the
address) or by choosing one in the filter bar. A thin page over `serving.detail`, `serving.style`,
`serving.links` and `serving.charts`, which hold (and test) the logic, the wording and the drawing.
"""

from __future__ import annotations

from html import escape

import common
import streamlit as st

from oa_market_intelligence.serving import charts, detail, links, style, trend, views

PAGE = "Segment detail"
BY_NAME = {"specialty": "specialty", "age_band": "age band", "gender": "gender"}
PLURAL = {"specialty": "specialties", "age_band": "age bands", "gender": "genders"}


def _crumbs(specialty, age, gender, period: str) -> str:
    """The path to the group as links: every part above the last goes back up a level."""
    labels = detail.crumbs(specialty, age, gender)
    chosen = [(k, v) for k, v in (("specialty", specialty), ("age", age), ("gender", gender)) if v]
    pieces = []
    for i, label in enumerate(labels):
        if i == len(labels) - 1:
            pieces.append(f"<b>{escape(label)}</b>")
        else:
            above = dict(chosen[:i])  # the parts chosen above this crumb
            address = links.href(PAGE, period=period, source=PAGE, **above)
            pieces.append(style.link(address, escape(label), "oa-crumb"))
    return " › ".join(pieces)


def _choose(seg, period: str) -> None:
    """Nothing chosen yet: a list of the biggest groups to click, a column for each way to cut."""
    st.markdown(
        style.callout("Pick a group below, or choose one in the filter bar above.", "up"),
        unsafe_allow_html=True,
    )
    cols = st.columns(3)
    for col, (column, key, title) in zip(
        cols,
        (
            ("specialty", "specialty", "By specialty"),
            ("age_band", "age", "By age band"),
            ("gender", "gender", "By gender"),
        ),
        strict=True,
    ):
        with col.container(border=True, height="stretch"):
            st.markdown(f"**{title}**")
            comp = trend.composition(seg, column, top=8)
            rows = comp[comp["group"].notna()]
            st.markdown(
                style.bar_rows(
                    [(r.label, r.pct, f"{r.pct:.0f}%") for r in rows.itertuples()],
                    scale_to=100,
                    links=[
                        links.href(PAGE, period=period, source=PAGE, **{key: r.group})
                        for r in rows.itertuples()
                    ],
                ),
                unsafe_allow_html=True,
            )
            st.caption("Share of Zilretta's visits. Click one to open it.")


def render(ctx, data_chip, flt: dict) -> None:
    """Draw the page from the loaded data (`ctx`) and the shared filters `flt`."""
    D = ctx.D
    seg, market, pred = D["seg"], D["monthly"], D["pred"]
    specialty, age, gender, period = (flt[k] for k in ("specialty", "age", "gender", "period"))
    data_chip()
    if not detail.chosen(specialty, age, gender):
        _choose(seg, period)
        return
    calendar = sorted(seg["month_id"].unique())
    parts = detail.crumbs(specialty, age, gender)
    who = " · ".join(parts[1:])
    st.markdown(_crumbs(specialty, age, gender, period), unsafe_allow_html=True)

    s = detail.summary(seg, specialty, age, gender, period, market)
    table = detail.peers(seg, specialty, age, gender, period, calendar)
    place = detail.rank_of(table)
    by = table.attrs.get("by") if not table.empty else None

    # ---- headline cards
    cards = st.columns(4)
    gap = s["gap_to_market_pp"]
    cards[0].markdown(
        style.kpi_card(
            f"Share in {views.month_label(s['latest_month_id'])}",
            "n/a" if s["latest_share"] is None else views.pct(s["latest_share"]),
            f"The whole market: {views.pct(s['market_share'])}",
            delta=gap,
            pill=style.signed(gap) if gap is not None else "",
        ),
        unsafe_allow_html=True,
    )
    cards[1].markdown(
        style.kpi_card(
            f"Zilretta visits, {trend.PERIOD_NAMES[period]}",
            f"{s['period_zilretta_visits']:,}",
            f"Of {s['period_category_visits']:,} visits in the three classes.",
        ),
        unsafe_allow_html=True,
    )
    change = s["change_12m_pp"]
    cards[2].markdown(
        style.kpi_card(
            "Change over the year",
            "n/a" if change is None else style.signed(change),
            "Last 12 months against the 12 before"
            + ("" if change is not None else " (too few visits to say)."),
            delta=change,
        ),
        unsafe_allow_html=True,
    )
    cards[3].markdown(
        style.kpi_card(
            f"Rank among {PLURAL[by]}" if by else "Rank",
            "not ranked" if place is None else f"{place[0]} of {place[1]}",
            "By share over the period; groups under 1,000 visits are left out.",
        ),
        unsafe_allow_html=True,
    )
    if s["small"]:
        st.warning(
            f"This group is small (a median of {s['median_monthly_visits']:,.0f} visits a month), "
            "so its share jumps around by chance. Read the direction, not single months."
        )
    st.write(detail.sentence(who, s))

    # ---- the group against the market, and its forecast
    left, right = st.columns([2.1, 1])
    with left.container(border=True):
        st.markdown("**This group against all visits**")
        series = detail.compare_series(seg, specialty, age, gender, market, period)
        event = st.altair_chart(
            charts.compare_chart(series, who, brush=True),
            width="stretch",
            on_select="rerun",
            key=f"detail_brush_{st.session_state.get('nav', 0)}_{specialty}|{age}|{gender}|{period}",
        )
        picked = detail.range_compare(series, common.brushed(event, "month"))
        if picked is not None:
            st.markdown(style.callout(detail.range_sentence(who, picked)), unsafe_allow_html=True)
        st.caption(
            "Zilretta's visits as a share of the visits of Zilretta, generic steroid injections "
            "and NSAID/OTC together: this group (teal) and every group (grey, dashed). Drag "
            "across the chart to compare the two over a range of months."
        )
        common.explain("detail_compare")
    block = detail.forecast_block(pred, seg, specialty, age, gender)
    with right.container(border=True, height="stretch"):
        st.markdown("**Forecast for this group**")
        if not block["rows"]:
            st.caption("No scored segment falls in this group, so there is no forecast for it.")
        else:
            st.markdown(
                style.stand_rows(
                    [
                        (
                            f"In {r['horizon']} month{'s' if r['horizon'] != 1 else ''}",
                            f"{r['forecast_pct']:.2f}%",
                        )
                        for r in block["rows"]
                    ]
                    + [("Latest month", f"{block['rows'][0]['last_pct']:.2f}%")]
                ),
                unsafe_allow_html=True,
            )
            seg_count = block["rows"][0]["segments"]
            st.caption(
                f"Weighted over {seg_count} scored segment{'s' if seg_count != 1 else ''}. "
                "The segment forecast runs high in testing: read it as optimistic."
            )
            if block["alerts_scored"]:
                st.write(
                    f"**Decline alert:** {block['alerts_flagged']} of {block['alerts_scored']} "
                    "scored segments flagged for the next month."
                )

    # ---- its neighbours
    if not table.empty:
        st.markdown(f"##### Next to its neighbours, by {BY_NAME[by]}")
        with st.container(border=True):
            ordered = table.head(15)
            rows = [
                (
                    ("▶ " if r.current else "") + r.label,
                    f"{r.share_pct:.2f}%",
                    r.delta_pp if r.delta_pp == r.delta_pp else None,
                    style.signed(r.delta_pp if r.delta_pp == r.delta_pp else None),
                )
                for r in ordered.itertuples()
            ]
            hrefs = [common.detail_href(by, r.group, flt, PAGE) for r in ordered.itertuples()]
            st.markdown(
                style.rank_rows(rows, (BY_NAME[by].capitalize(), "Share", "Change"), hrefs),
                unsafe_allow_html=True,
            )
            st.caption(
                "Ranked by share. The change is the last 12 months against the 12 before, in "
                "points. Click a neighbour to open it. The group you are on is marked ▶."
            )

    # ---- where else to look
    keep = dict(specialty=specialty, age=age, gender=gender, period=period, source=PAGE)
    st.markdown("##### Look at it elsewhere")
    with st.container(border=True):
        st.markdown(
            " &nbsp;·&nbsp; ".join(
                [
                    style.link_line(
                        "Its trend on Market trend", links.href("Market trend", **keep)
                    ),
                    style.link_line("Its forecast on Forecasts", links.href("Forecasts", **keep)),
                    style.link_line("All groups on Segments", links.href("Segments", **keep)),
                ]
            ),
            unsafe_allow_html=True,
        )
    st.caption(
        "These are visits in IQVIA's panel, not company sales, and nothing here says why a group's "
        "share moved. Small groups are noisy."
    )
