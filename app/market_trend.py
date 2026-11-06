"""The Market trend tab (DL-97): a filter bar, four headline cards, the share over time with a
straight-line guide, a panel on where the share stands, two "explore" cards, a profile of who the
visits come from and a year-by-year table. A thin page over `serving.trend`, `serving.style` and
`serving.charts`, which hold (and test) the logic, the wording and the drawing.
"""

from __future__ import annotations

import streamlit as st

from oa_market_intelligence.serving import charts, style, trend, views

ALL = "All"
PERIOD_LABELS = {"1Y": "1Y", "3Y": "3Y", "5Y": "5Y", "All": "All"}
DIMENSIONS = (("specialty", "specialty"), ("age_band", "age band"), ("gender", "gender"))


def _pick(value: str) -> str | None:
    return None if value == ALL else value


def _filters(seg) -> tuple[str | None, str | None, str | None, str]:
    """The filter bar: three selections and the period. Returns specialty, age band, gender and
    the period key."""
    with st.container(border=True, key="trend_filters"):
        cols = st.columns([2.3, 1.4, 1.3, 2.4])
        by_visits = seg.groupby("specialty")["category"].sum().sort_values(ascending=False)
        specialty = cols[0].selectbox(
            "Specialty",
            [ALL, *by_visits.index],
            format_func=lambda v: "All specialties" if v == ALL else trend.pretty(v),
            key="trend_specialty",
        )
        ages = sorted(seg["age_band"].unique())
        age = cols[1].selectbox(
            "Age band",
            [ALL, *ages],
            format_func=lambda v: "All ages" if v == ALL else trend.pretty(v),
            key="trend_age",
        )
        genders = sorted(seg["gender"].unique())
        gender = cols[2].selectbox(
            "Gender",
            [ALL, *genders],
            format_func=lambda v: "All genders" if v == ALL else trend.pretty(v),
            key="trend_gender",
        )
        period = cols[3].segmented_control(
            "Period",
            list(PERIOD_LABELS),
            default="All",
            format_func=PERIOD_LABELS.get,
            key="trend_period",
        )
    return _pick(specialty), _pick(age), _pick(gender), period or "All"


def _who(specialty, age, gender) -> str:
    parts = [
        trend.pretty(specialty) if specialty else None,
        f"ages {trend.pretty(age)}" if age else None,
        trend.pretty(gender).lower() if gender else None,
    ]
    named = [p for p in parts if p]
    return ", ".join(named) if named else "All visits"


def _share(value: float | None) -> str:
    return "n/a" if value is None else views.pct(value)


def render(D: dict, data_chip) -> None:
    """Draw the tab from the loaded tables `D` (needs `seg`, `monthly` and `pred`)."""
    data_chip()
    seg, national, pred = D["seg"], D["monthly"], D["pred"]
    specialty, age, gender, period = _filters(seg)
    filtered = any(v is not None for v in (specialty, age, gender))
    who = _who(specialty, age, gender)

    full = trend.monthly(seg, specialty, age, gender)
    win = trend.window(full, period)
    k = trend.kpis(full, win)

    # ---- headline cards
    cards = st.columns(4)
    gap = k["change_vs_year_ago_pp"]
    cards[0].markdown(
        style.kpi_card(
            f"Share in {views.month_label(k['latest_month_id'])}",
            _share(k["latest_share"]),
            f"A year earlier: {_share(k['year_ago_share'])}",
            delta=gap,
            pill=style.signed(gap) if gap is not None else "",
        ),
        unsafe_allow_html=True,
    )
    peak_text = (
        "No month with enough visits"
        if k["peak_month_id"] is None
        else f"{views.month_label(k['peak_month_id'])}"
        + (
            f" · latest is {abs(100 * k['change_since_peak']):.0f}% below"
            if k["change_since_peak"] is not None and k["change_since_peak"] < 0
            else ""
        )
    )
    cards[1].markdown(
        style.kpi_card("Peak share", _share(k["peak_share"]), peak_text), unsafe_allow_html=True
    )
    cards[2].markdown(
        style.kpi_card(
            "Zilretta visits",
            f"{k['period_zilretta_visits']:,}",
            f"{trend.PERIOD_NAMES[period].capitalize()} · {_share(k['period_share'])} of "
            f"{k['period_category_visits']:,} visits",
        ),
        unsafe_allow_html=True,
    )
    slope = k["slope_pp_per_year"]
    cards[3].markdown(
        style.kpi_card(
            "Trend per year",
            "n/a" if slope is None else f"{slope:+.2f} pp",
            "A straight line through the period: a guide, not a forecast",
            delta=slope,
        ),
        unsafe_allow_html=True,
    )
    if trend.is_small(k):
        st.warning(
            f"This group is small (a median of {k['median_monthly_visits']:,.0f} visits a month), "
            "so its share jumps around from month to month by chance. Read the direction, not "
            "single months."
        )

    # ---- the chart and where the share stands
    left, right = st.columns([2.1, 1])
    with left.container(border=True):
        st.markdown("**Share of visits over time**")
        top = st.columns([1.6, 1, 1])
        grain = top[0].segmented_control(
            "Grain",
            ["Monthly", "Quarterly"],
            default="Monthly",
            key="trend_grain",
            label_visibility="collapsed",
        )
        show_line = top[1].toggle("Trend line", value=True, key="trend_line")
        show_events = top[2].toggle("Markers", value=True, key="trend_markers")
        frame = trend.quarterly(win) if grain == "Quarterly" else win
        fitted, _ = trend.trend_line(frame)
        st.altair_chart(
            charts.market_trend_chart(
                frame,
                fitted if show_line else None,
                trend.events(win) if show_events else None,
                trend.dip_range(win) if show_events else None,
            ),
            width="stretch",
        )
        st.caption(
            f"{who}: Zilretta's visits as a share of Zilretta, generic steroid injection and "
            "NSAID/OTC visits. Amber dashes: the straight-line guide. Grey dotted lines: the peak, "
            "the turn in March 2022 and the start of the 2024 dip. Shaded: the March to July 2024 "
            "data-capture dip, which hit all three classes."
        )
    with right.container(border=True, height="stretch"):
        st.markdown("**Where the share stands**")
        st.caption(who)
        rows = [
            (f"Latest ({views.month_label(k['latest_month_id'])})", _share(k["latest_share"])),
            ("A year earlier", _share(k["year_ago_share"])),
            ("Change over the year", style.signed(gap)),
            (
                "Peak"
                + (
                    ""
                    if k["peak_month_id"] is None
                    else f" ({views.month_label(k['peak_month_id'])})"
                ),
                _share(k["peak_share"]),
            ),
            (
                "Since the peak",
                "n/a"
                if k["change_since_peak"] is None
                else f"{100 * k['change_since_peak']:+.0f}%",
            ),
        ]
        st.markdown(style.stand_rows(rows), unsafe_allow_html=True)
        if not filtered:
            nat = views.national_forecast(pred)
            first = nat.iloc[0]
            st.markdown(f"**Next month ({first['month']})**")
            st.markdown(
                style.stand_rows(
                    [
                        ("National forecast", f"{first['forecast_pct']:.2f}%"),
                        (
                            "Likely range (90%)",
                            f"{first['low_90_pct']:.2f}–{first['high_90_pct']:.2f}%",
                        ),
                    ]
                ),
                unsafe_allow_html=True,
            )
            st.caption(
                'The method is "same as last month": no model beat it. More on the Forecasts tab.'
            )
        else:
            st.caption(
                "Forecasts for a group are on the Forecasts tab. The segment forecasts run high "
                "in testing, so read them as optimistic."
            )

    st.write(trend.sentence(k, who))

    # ---- explore further
    st.markdown("##### Explore further")
    a, b = st.columns(2)
    chosen = {"specialty": specialty, "age_band": age, "gender": gender}
    dimension = next(((c, n) for c, n in DIMENSIONS if chosen[c] is None), None)
    with a.container(border=True, height="stretch"):
        if dimension is None:
            st.markdown("**Where it is moving**")
            st.caption("Every filter is set, so there is nothing left to compare across.")
        else:
            column, name = dimension
            others = {c: v for c, v in chosen.items() if c != column}
            table = trend.movers(
                trend.select(
                    seg,
                    others["specialty"] if "specialty" in others else None,
                    others.get("age_band"),
                    others.get("gender"),
                ),
                column,
            )
            st.markdown(f"**Where it is moving, by {name}**")
            st.caption(
                "Share in the last 12 months, and the change from the 12 months before "
                f"(points of share). Groups under {trend.MIN_GROUP_VISITS:,} visits are left out."
            )
            if table.empty:
                st.write("Not enough visits to compare.")
            else:
                st.markdown(
                    style.rank_rows(
                        [
                            (r.label, f"{r.share_pct:.2f}%", r.delta_pp, style.signed(r.delta_pp))
                            for r in table.head(5).itertuples()
                        ],
                        (name.capitalize(), "Share", "Change"),
                    ),
                    unsafe_allow_html=True,
                )
                notes = trend.callouts(table)
                if notes["rising"]:
                    st.markdown(style.callout(notes["rising"], "up"), unsafe_allow_html=True)
                if notes["falling"]:
                    st.markdown(style.callout(notes["falling"], "down"), unsafe_allow_html=True)
    with b.container(border=True, height="stretch"):
        if not filtered:
            mix = trend.class_mix(national)
            st.markdown("**The three classes**")
            st.caption(
                "Each class's share of all three classes' visits in the last 12 months, and the "
                "change from the 12 months before."
            )
            st.markdown(
                style.rank_rows(
                    [
                        (r.label, f"{r.pct:.2f}%", r.delta_pp, style.signed(r.delta_pp))
                        for r in mix.itertuples()
                    ],
                    ("Class", "Share", "Change"),
                ),
                unsafe_allow_html=True,
            )
            st.caption(
                "A fall in Zilretta's share can be a rise in the other two; the data cannot say "
                "why visits moved."
            )
        else:
            whole = trend.monthly(seg)
            now = whole.tail(12)
            mine = full.tail(12)

            def rate(f):
                return f["zilretta"].sum() / f["category"].sum() if f["category"].sum() else None

            st.markdown("**How this group compares**")
            st.caption("Share of visits in the last 12 months, this group against all visits.")
            values = [(who, rate(mine)), ("All visits", rate(now))]
            top_value = max((v for _, v in values if v is not None), default=0) or 1
            st.markdown(
                style.bar_rows(
                    [(label, v or 0.0, _share(v)) for label, v in values], scale_to=1.15 * top_value
                ),
                unsafe_allow_html=True,
            )

    # ---- profile: who the visits come from
    st.markdown("##### Who the visits come from")
    st.caption(
        f"Each group's share of Zilretta's visits {trend.PERIOD_PHRASE[period]}"
        f" ({who if filtered else 'all visits'})."
    )
    months = set(win["month_id"])
    sel = trend.select(seg, specialty, age, gender)
    sel = sel[sel["month_id"].isin(months)]
    for col, (column, name) in zip(st.columns(3), DIMENSIONS, strict=True):
        with col.container(border=True, height="stretch"):
            st.markdown(f"**By {name}**")
            if chosen[column] is not None:
                st.caption(f"Filtered to {trend.pretty(chosen[column])}.")
                continue
            comp = trend.composition(sel, column, top=6)
            if comp.empty:
                st.caption("No Zilretta visits in this selection.")
                continue
            st.markdown(
                style.bar_rows(
                    [(r.label, r.pct, f"{r.pct:.0f}%") for r in comp.itertuples()], scale_to=100
                ),
                unsafe_allow_html=True,
            )

    # ---- the numbers behind it
    with st.expander("Year by year, and download"):
        yearly = trend.yearly(full)
        st.dataframe(
            yearly[["year", "share_pct", "zilretta", "category", "months", "partial_year"]].rename(
                columns={
                    "year": "Year",
                    "share_pct": "Share (%)",
                    "zilretta": "Zilretta visits",
                    "category": "All three classes",
                    "months": "Months of data",
                    "partial_year": "Part year",
                }
            ),
            hide_index=True,
            column_config={"Share (%)": st.column_config.NumberColumn(format="%.2f")},
        )
        st.download_button(
            "Download this series (CSV)",
            trend.csv(full),
            "zilretta_share_selection.csv",
            "text/csv",
        )
    st.caption(
        "These are visits in IQVIA's panel, not company sales, and nothing here says why the share "
        "moved. Small groups are noisy: one prescription can move a small group's share a lot."
    )
