"""The charts the site draws on request (the question box asks for one by name, DL-88).

Each function takes the frames the page already holds and returns an Altair chart; `build` picks
one from a name and the few parameters `qa_tools.show_chart` validated, so Claude only chooses
which chart and its settings and never writes drawing code. Colours are the dataviz reference
palette (slot 1 blue for the series, slot 2 orange for what is projected, muted grey for context);
the page's theme can replace them in one place. Marks are thin, the grid is quiet, every mark has
a tooltip, and small groups are drawn faded so they do not look as solid as large ones.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from oa_market_intelligence.serving import views
from oa_market_intelligence.serving.qa_tools import Snapshot

PRIMARY = "#2a78d6"
PROJECTED = "#eb6834"
HEIGHT = 300
BAR_STEP = 28  # pixels per bar, so a long list grows the chart instead of squeezing it


def _style(chart: alt.Chart) -> alt.Chart:
    """Quiet frame. Text and grid colours are left to Streamlit's theme so the chart reads in both
    light and dark mode; only the series colours are ours."""
    return chart.configure_view(stroke=None).configure_title(anchor="start", fontSize=14)


def trend_chart(
    monthly: pd.DataFrame, start_month_id: int | None = None, end_month_id: int | None = None
) -> alt.Chart:
    """Zilretta's share by month, with a tooltip on every month."""
    frame = views.trend_frame(monthly)
    if start_month_id is not None:
        frame = frame[frame["month_id"] >= start_month_id]
    if end_month_id is not None:
        frame = frame[frame["month_id"] <= end_month_id]
    frame = frame.assign(label=[views.month_label(m) for m in frame["month_id"]])
    base = alt.Chart(frame, title="Zilretta's share of visits by month")
    line = base.mark_line(color=PRIMARY, strokeWidth=2).encode(
        x=alt.X("month:T", title=None),
        y=alt.Y("share_pct:Q", title="Share of visits (%)", scale=alt.Scale(zero=False)),
    )
    points = base.mark_circle(color=PRIMARY, size=45, opacity=0).encode(
        x="month:T",
        y="share_pct:Q",
        tooltip=[
            alt.Tooltip("label:N", title="Month"),
            alt.Tooltip("share_pct:Q", title="Share (%)", format=".2f"),
            alt.Tooltip("in_dip:N", title="In the 2024 data dip"),
        ],
    )
    return _style((line + points).properties(height=HEIGHT).interactive(bind_y=False))


def segments_chart(seg: pd.DataFrame, by: str, top: int = 10) -> alt.Chart:
    """Share by specialty, age band or gender; groups under 5,000 category visits are faded."""
    frame = views.specialty_frame(seg, top).assign(
        size=lambda f: f["small_segment"].map({True: "Small group (noisy)", False: "Group"})
    )
    title = {"specialty": "specialty", "age_band": "age band", "gender": "gender"}[by]
    return _style(
        alt.Chart(frame, title=f"Zilretta's share of visits by {title}, all months")
        .mark_bar(color=PRIMARY, cornerRadiusEnd=3)
        .encode(
            y=alt.Y(
                "group:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)
            ),
            x=alt.X("share_pct:Q", title="Share of visits (%)"),
            opacity=alt.Opacity(
                "size:N",
                scale=alt.Scale(domain=["Group", "Small group (noisy)"], range=[1.0, 0.4]),
                legend=alt.Legend(title=None, orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("group:N", title=title.capitalize()),
                alt.Tooltip("share_pct:Q", title="Share (%)", format=".2f"),
                alt.Tooltip("zilretta_visits:Q", title="Zilretta visits", format=","),
                alt.Tooltip("category_visits:Q", title="All three classes", format=","),
            ],
        )
        .properties(height=alt.Step(BAR_STEP))
    )


def forecast_chart(monthly: pd.DataFrame, pred: pd.DataFrame, months_back: int = 36) -> alt.Chart:
    """The last three years of share with the national forecast and its 90% range."""
    history = views.trend_frame(monthly).tail(months_back)
    forecast = views.national_forecast(pred)
    forecast = forecast.assign(
        date=[pd.Timestamp(year=int(m) // 100, month=int(m) % 100, day=1) for m in _targets(pred)]
    )
    line = (
        alt.Chart(history, title="Share so far, and the national forecast with its 90% range")
        .mark_line(color=PRIMARY, strokeWidth=2)
        .encode(
            x=alt.X("month:T", title=None),
            y=alt.Y("share_pct:Q", title="Share of visits (%)", scale=alt.Scale(zero=False)),
        )
    )
    band = (
        alt.Chart(forecast)
        .mark_rule(color=PROJECTED, strokeWidth=2)
        .encode(x="date:T", y="low_90_pct:Q", y2="high_90_pct:Q")
    )
    points = (
        alt.Chart(forecast)
        .mark_point(color=PROJECTED, filled=True, size=70)
        .encode(
            x="date:T",
            y="forecast_pct:Q",
            tooltip=[
                alt.Tooltip("month:N", title="Month"),
                alt.Tooltip("months_ahead:Q", title="Months ahead"),
                alt.Tooltip("forecast_pct:Q", title="Forecast (%)", format=".2f"),
                alt.Tooltip("low_90_pct:Q", title="Likely low (%)", format=".2f"),
                alt.Tooltip("high_90_pct:Q", title="Likely high (%)", format=".2f"),
            ],
        )
    )
    return _style((line + band + points).properties(height=HEIGHT))


def _targets(pred: pd.DataFrame) -> list[int]:
    rows = pred[pred["level"] == "national"].sort_values("horizon")
    return [int(m) for m in rows["target_month_id"]]


def specialty_mix_chart(graph, top: int = 10) -> alt.Chart:
    """Where Zilretta's visits come from, by specialty (from the knowledge graph's weights)."""
    rows = [
        {
            "specialty": e["other"]["label"],
            "visits": e["visits"],
            "share_pct": 100 * e["share_of_group_visits"],
        }
        for e in graph.neighbors("group:zilretta", "prescribed_by", "out")
    ]
    frame = pd.DataFrame(rows).sort_values("visits", ascending=False).head(top)
    return _style(
        alt.Chart(frame, title="Where Zilretta's visits come from, by specialty (all months)")
        .mark_bar(color=PRIMARY, cornerRadiusEnd=3)
        .encode(
            y=alt.Y(
                "specialty:N",
                sort="-x",
                title=None,
                axis=alt.Axis(labelLimit=260, labelOverlap=False),
            ),
            x=alt.X("share_pct:Q", title="Share of Zilretta's visits (%)"),
            tooltip=[
                alt.Tooltip("specialty:N", title="Specialty"),
                alt.Tooltip("share_pct:Q", title="Share of Zilretta visits (%)", format=".1f"),
                alt.Tooltip("visits:Q", title="Visits", format=","),
            ],
        )
        .properties(height=alt.Step(BAR_STEP))
    )


def build(chart: str, params: dict, snap: Snapshot) -> alt.Chart:
    """The chart named `chart` (one of qa_tools.CHARTS) with its validated parameters."""
    if chart == "trend":
        return trend_chart(snap.monthly, params.get("start_month_id"), params.get("end_month_id"))
    if chart == "segments":
        by = params.get("by", "specialty")
        frames = {"specialty": snap.by_specialty, "age_band": snap.by_age, "gender": snap.by_gender}
        return segments_chart(frames[by], by, int(params.get("top", 10)))
    if chart == "forecast":
        return forecast_chart(snap.monthly, snap.pred)
    if chart == "specialty_mix":
        return specialty_mix_chart(snap.graph, int(params.get("top", 10)))
    raise ValueError(f"unknown chart {chart!r}")


def events_chart(monthly: pd.DataFrame, graph) -> alt.Chart:
    """The share line with the events that fall inside the data drawn against it.

    Events are context, never causes: the chart draws a thin rule at each event's date (a shaded
    band for an event with an end date, dashed and grey when the event is not verified) and says
    nothing about what any of them did to the share. Events before the data begins are left out
    here and stay in the event table.
    """
    from oa_market_intelligence.serving.market_map import event_table

    line_frame = views.trend_frame(monthly)
    first, last = line_frame["month"].min(), line_frame["month"].max()
    table = event_table(graph).assign(
        start=lambda f: pd.to_datetime(f["date"]),
        stop=lambda f: pd.to_datetime(f["until"].replace("", pd.NA).fillna(f["date"])),
    )
    inside = table[(table["stop"] >= first) & (table["start"] <= last)].copy()
    inside["x"] = inside["start"].clip(lower=first)
    inside["x2"] = inside["stop"].clip(upper=last)
    inside["kind"] = inside["verified"].map({"yes": "Verified event", "no": "Not verified"})
    inside["short"] = inside["event"].str.slice(0, 90)
    low = float(line_frame["share_pct"].min())
    inside["y"] = low - 0.08
    line = (
        alt.Chart(line_frame, title="Share of visits, with the events that fall inside the data")
        .mark_line(color=PRIMARY, strokeWidth=2)
        .encode(
            x=alt.X("month:T", title=None),
            y=alt.Y("share_pct:Q", title="Share of visits (%)", scale=alt.Scale(zero=False)),
        )
    )
    bands = (
        alt.Chart(inside[inside["x"] < inside["x2"]])
        .mark_rect(color=PROJECTED, opacity=0.12)
        .encode(x="x:T", x2="x2:T")
    )
    rules = (
        alt.Chart(inside)
        .mark_rule(strokeWidth=1.5)
        .encode(
            x="x:T",
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(
                    domain=["Verified event", "Not verified"], range=["#6250d6", "#9a9a94"]
                ),
                legend=alt.Legend(title=None, orient="bottom"),
            ),
            strokeDash=alt.StrokeDash(
                "kind:N",
                scale=alt.Scale(domain=["Verified event", "Not verified"], range=[[1, 0], [4, 3]]),
                legend=None,
            ),
        )
    )
    marks = (
        alt.Chart(inside)
        .mark_point(filled=True, size=70, shape="diamond")
        .encode(
            x="x:T",
            y="y:Q",
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(
                    domain=["Verified event", "Not verified"], range=["#6250d6", "#9a9a94"]
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("date:N", title="Date"),
                alt.Tooltip("short:N", title="Event"),
                alt.Tooltip("type:N", title="Type"),
                alt.Tooltip("verified:N", title="Verified"),
            ],
        )
    )
    return _style((bands + line + rules + marks).properties(height=HEIGHT))
