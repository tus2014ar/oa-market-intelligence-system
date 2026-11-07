"""The Forecasts tab (DL-99): a horizon filter, four cards, the national forecast with its ranges,
how far to trust it, the segment forecasts next to last month's share, and the decline alert. A thin
page over `serving.forecasts`, `serving.style` and `serving.charts`, which hold (and test) the
logic, the wording and the drawing.
"""

from __future__ import annotations

import common
import live_panel
import streamlit as st

from oa_market_intelligence.serving import charts, forecasts, links, style, views

HORIZON_LABELS = {1: "1 month", 3: "3 months", 6: "6 months"}


def _horizon() -> int:
    """The one control that belongs to this page alone: how many months ahead. The specialty, age
    and gender filters are the shared bar's."""
    with st.container(border=True, key="fc_filters"):
        horizon = st.segmented_control(
            "Months ahead",
            list(HORIZON_LABELS),
            default=1,
            format_func=HORIZON_LABELS.get,
            key="fc_horizon",
        )
    return horizon or 1


def render(D: dict, adoption: dict, data_chip, flt: dict) -> None:
    """Draw the page from the loaded tables `D` (needs `monthly`, `pred`, `scores` and `seg`) and
    the shared filters `flt` (specialty, age and gender; the period is not used here)."""
    data_chip()
    pred, scores, seg = D["pred"], D["scores"], D["seg"]
    horizon = _horizon()
    specialty, age, gender = flt["specialty"], flt["age"], flt["gender"]
    nat = forecasts.national(pred)
    held = forecasts.coverage(scores)
    view = forecasts.select(forecasts.segment_view(pred, seg, horizon), specialty, age, gender)
    first = forecasts.select(forecasts.segment_view(pred, seg, 1), specialty, age, gender)

    # ---- headline cards: the national forecast at each horizon, and how often the range held
    cards = st.columns(4)
    for col, row in zip(cards[:3], nat.itertuples(), strict=False):
        col.markdown(
            style.kpi_card(
                f"In {forecasts.horizon_label(row.horizon)} ({row.month})",
                f"{row.forecast_pct:.2f}%",
                f"Likely {row.lo90_pct:.2f}% to {row.hi90_pct:.2f}% (90%) · {row.method}",
            ),
            unsafe_allow_html=True,
        )
    rate = held.get(horizon)
    cards[3].markdown(
        style.kpi_card(
            f"Range held at {forecasts.horizon_label(horizon)}",
            "n/a" if rate is None else f"{round(100 * rate)}%",
            "of test months: a 90% range should hold about 9 months in 10",
        ),
        unsafe_allow_html=True,
    )

    # ---- the national chart and how far to trust it
    left, right = st.columns([2.1, 1])
    with left.container(border=True):
        st.markdown("**National share, and the forecast**")
        st.altair_chart(
            charts.national_fan_chart(forecasts.fan(D["monthly"], pred)), width="stretch"
        )
        st.caption(
            "Blue: the share so far. Orange: the forecast at 1, 3 and 6 months, with a thick bar "
            "for the 80% range and a thin one for the 90% range. The forecast is last month's "
            "value: no model beat that simple rule at any horizon."
        )
        common.explain("fc_fan")
    trust = forecasts.trust(adoption, scores, horizon)
    with right.container(border=True, height="stretch"):
        st.markdown(f"**How far to trust it, {forecasts.horizon_label(horizon)} ahead**")
        st.markdown(
            style.stand_rows(
                [
                    ("National method", trust["national_method"].capitalize()),
                    ("Segment method", trust["segment_method"].capitalize()),
                    (
                        "National range held",
                        "n/a"
                        if trust["range_held"] is None
                        else f"{round(100 * trust['range_held'])}%",
                    ),
                ]
            ),
            unsafe_allow_html=True,
        )
        st.caption(
            views.coverage_sentence(scores)
            + " The months forecast here have already passed: no newer extract has arrived."
        )
        st.markdown(
            style.link_line(
                "How the models were tested",
                links.href("Model results", source="Forecasts", section="How the models scored"),
            ),
            unsafe_allow_html=True,
        )

    # ---- segment forecasts
    st.markdown("##### Segment forecasts")
    st.markdown(
        style.callout(
            forecasts.bias_text(pred, horizon), "down" if trust["segment_is_model"] else "up"
        ),
        unsafe_allow_html=True,
    )
    st.caption(views.bias_sentence(pred))
    spec = forecasts.specialty_view(view)
    with st.container(border=True):
        st.markdown(f"**By specialty, {forecasts.horizon_label(horizon)} ahead**")
        if spec.empty:
            st.write("No specialty in this selection has enough visits to chart.")
        else:
            event = st.altair_chart(
                charts.specialty_forecast_chart(spec.head(14), horizon),
                width="stretch",
                on_select="rerun",
                key=common.nav_key("fc_spec"),
            )
            hit = common.clicked(event, ["specialty"])
            if hit:
                common.go_detail("specialty", hit["specialty"], flt, "Forecasts")
        left_out = spec.attrs.get("left_out", 0)
        st.caption(
            "Orange bar: the forecast. Dark tick: the share in the latest month. Specialties "
            "with the most visits first, each weighted by its segments' visits"
            + (
                f"; {left_out} with under {forecasts.MIN_SPECIALTY_VISITS} visits in the latest "
                "month are left out here and are in the table below"
                if left_out
                else ""
            )
            + ". Part of any forecast change is a segment falling back toward its usual level, "
            "so read the forecast as a baseline, not as a prediction for one segment. Click a "
            "bar to open that specialty."
        )
        common.explain("fc_spec")
    with st.container(border=True):
        shown = view
        st.markdown(
            f"**Segments{' in this selection' if (specialty or age or gender) else ''}"
            f" ({len(shown)})**"
        )
        event = st.dataframe(
            shown[
                [
                    "specialty_label",
                    "age_label",
                    "gender_label",
                    "last_pct",
                    "forecast_pct",
                    "change_pp",
                    "origin_visits",
                    "small",
                ]
            ].rename(
                columns={
                    "specialty_label": "Specialty",
                    "age_label": "Age band",
                    "gender_label": "Gender",
                    "last_pct": "Latest month (%)",
                    "forecast_pct": f"Forecast, {horizon} mo (%)",
                    "change_pp": "Change (points)",
                    "origin_visits": "Visits in latest month",
                    "small": "Small (noisy)",
                }
            ),
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=common.nav_key("fc_table"),
            column_config={
                "Latest month (%)": st.column_config.NumberColumn(format="%.2f"),
                f"Forecast, {horizon} mo (%)": st.column_config.NumberColumn(format="%.2f"),
                "Change (points)": st.column_config.NumberColumn(format="%+.2f"),
                "Visits in latest month": st.column_config.NumberColumn(format="%d"),
            },
        )
        row = common.clicked_row(event)
        if row is not None:  # a click on a row opens that segment
            picked = shown.iloc[row]
            common.go(
                "Segment detail",
                "Forecasts",
                specialty=picked["specialty"],
                age=picked["age_band"],
                gender=picked["gender"],
                period=flt["period"],
            )
        st.caption(
            f"Segments under {forecasts.MIN_ALERT_VISITS} visits in the latest month are marked "
            "small: one prescription moves their share a lot. Click a row to open that segment."
        )
        common.explain("fc_table")
        st.download_button(
            "Download these forecasts (CSV)",
            forecasts.csv(shown),
            f"zilretta_segment_forecast_{horizon}_months.csv",
            "text/csv",
        )

    # ---- the model run live, through the API
    live_panel.render(forecasts.segment_view(pred, seg, horizon), pred, horizon)

    # ---- the decline alert
    st.markdown("##### Decline alert, 1 month ahead")
    flagged = forecasts.alerts(first)
    summary = views.alert_summary(views.alert_frame(pred), scores)
    with st.container(border=True):
        st.markdown(
            style.stand_rows(
                [
                    ("Segments scored", f"{summary['scored']}"),
                    ("Flagged as likely to fall 30% or more", f"{summary['flagged']}"),
                    (
                        "Alerts that were real declines in testing",
                        "n/a"
                        if summary["precision"] is None
                        else f"{round(100 * summary['precision'])}%",
                    ),
                ]
            ),
            unsafe_allow_html=True,
        )
        if flagged.empty:
            st.write("No segment is flagged.")
        else:
            event = st.dataframe(
                flagged[
                    ["specialty_label", "age_label", "gender_label", "alert_score", "origin_visits"]
                ].rename(
                    columns={
                        "specialty_label": "Specialty",
                        "age_label": "Age band",
                        "gender_label": "Gender",
                        "alert_score": "Alert score",
                        "origin_visits": "Visits in latest month",
                    }
                ),
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key=common.nav_key("fc_alerts"),
                column_config={
                    "Visits in latest month": st.column_config.NumberColumn(format="%d"),
                    "Alert score": st.column_config.ProgressColumn(
                        min_value=0.0, max_value=1.0, format="%.2f"
                    ),
                },
            )
        row = common.clicked_row(event)
        if row is not None:  # a click on a row opens that segment
            picked = flagged.iloc[row]
            common.go(
                "Segment detail",
                "Forecasts",
                specialty=picked["specialty"],
                age=picked["age_band"],
                gender=picked["gender"],
                period=flt["period"],
            )
        st.caption(
            "About half of the alerts were real declines in testing, and a one-line rule (this "
            "segment just spiked) does almost as well, so treat the list as a starting point for "
            "a look, never as a reason to act on its own. The alert is a 1-month flag only. Click a "
            "row to open that segment."
        )
        common.explain("fc_alerts")
        st.markdown(
            style.link_line(
                "How reliable the alert is, on Model results",
                links.href("Model results", source="Forecasts", section="How the models scored"),
            ),
            unsafe_allow_html=True,
        )
    st.caption(
        "Forecasts are the monthly job's stored output, not fitted while the page loads. They "
        "describe visits in IQVIA's panel, not company sales."
    )
