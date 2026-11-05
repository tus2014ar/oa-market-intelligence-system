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
SERIES = "#0F8B8D"  # the site's teal, for the tab-level series
TREND_GUIDE = "#E8A33D"  # amber: the straight-line guide and the data-capture dip
FALL = "#C8553D"  # rust: a fall
GRID_CAP = 8  # the grid's colour scale stops here, so a few extreme cells do not wash out the rest
CATEGORICAL = [
    "#0F8B8D",
    "#E8A33D",
    "#1B2A41",
    "#C8553D",
    "#7A8794",
    "#2a78d6",
    "#8B5FBF",
    "#3FA34D",
]
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


def market_trend_chart(
    frame: pd.DataFrame,
    fitted: pd.Series | None = None,
    events: pd.DataFrame | None = None,
    dip: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> alt.Chart:
    """The Market trend tab's chart: the share by month (or quarter) with an optional straight-line
    guide (amber, dashed), the 2024 data-capture dip (shaded) and dated markers (grey, dotted).
    `frame` comes from `trend.monthly` or `trend.quarterly`."""
    data = frame.assign(fitted=None if fitted is None else fitted.to_numpy())
    base = alt.Chart(data)
    x = alt.X("month:T", title=None)
    y = alt.Y("share_pct:Q", title="Share of visits (%)", scale=alt.Scale(zero=False))
    tip = [
        alt.Tooltip("label:N", title="Period"),
        alt.Tooltip("share_pct:Q", title="Share (%)", format=".2f"),
        alt.Tooltip("zilretta:Q", title="Zilretta visits", format=","),
        alt.Tooltip("category:Q", title="All three classes", format=","),
    ]
    layers = []
    if dip is not None:
        band = pd.DataFrame({"start": [dip[0]], "end": [dip[1]]})
        shade = alt.Chart(band).mark_rect(color=TREND_GUIDE, opacity=0.18)
        layers.append(shade.encode(x="start:T", x2="end:T"))
    layers.append(base.mark_line(color=SERIES, strokeWidth=3).encode(x=x, y=y))
    layers.append(base.mark_circle(color=SERIES, size=46).encode(x=x, y=y, tooltip=tip))
    if fitted is not None:
        layers.append(
            base.mark_line(color=TREND_GUIDE, strokeDash=[6, 5], strokeWidth=2).encode(
                x="month:T", y="fitted:Q"
            )
        )
    if events is not None and len(events):
        layers.append(
            alt.Chart(events)
            .mark_rule(color="#7A8794", strokeDash=[3, 4])
            .encode(x="month:T", tooltip=alt.Tooltip("label:N", title="Marker"))
        )
    return _style(alt.layer(*layers).properties(height=HEIGHT + 60))


def group_chart(table: pd.DataFrame, show: str = "share") -> alt.Chart:
    """The Segments tab's ranked bars: share, Zilretta's visits, or the change in share over the
    last 12 months (teal for a rise, rust for a fall). Groups with few visits are drawn faded."""
    column, title, fmt = {
        "share": ("share_pct", "Zilretta's share of visits (%)", ".2f"),
        "visits": ("zilretta_visits", "Zilretta visits", ","),
        "change": ("delta_pp", "Change in share, last 12 months vs the 12 before (points)", "+.2f"),
    }[show]
    frame = table.dropna(subset=[column]).assign(
        size=lambda f: f["small"].map({True: "Small group (noisy)", False: "Group"}),
        direction=lambda f: (f[column] >= 0).map({True: "up", False: "down"}),
    )
    colour = (
        alt.Color(
            "direction:N",
            scale=alt.Scale(domain=["up", "down"], range=[SERIES, FALL]),
            legend=None,
        )
        if show == "change"
        else alt.value(SERIES)
    )
    return _style(
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            y=alt.Y(
                "label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)
            ),
            x=alt.X(f"{column}:Q", title=title),
            color=colour,
            opacity=alt.Opacity(
                "size:N",
                scale=alt.Scale(domain=["Group", "Small group (noisy)"], range=[1.0, 0.4]),
                legend=alt.Legend(title=None, orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Group"),
                alt.Tooltip("share_pct:Q", title="Share (%)", format=".2f"),
                alt.Tooltip("zilretta_visits:Q", title="Zilretta visits", format=","),
                alt.Tooltip("category_visits:Q", title="All three classes", format=","),
                alt.Tooltip("delta_pp:Q", title="Change (points)", format="+.2f"),
            ],
        )
        .properties(height=alt.Step(BAR_STEP))
    )


def share_grid(cells: pd.DataFrame) -> alt.Chart:
    """Specialty by age band: a grid coloured by Zilretta's share, with the share written in each
    cell that has enough visits and grey where it does not."""
    base = alt.Chart(cells).encode(
        y=alt.Y(
            "row_label:N",
            sort=list(dict.fromkeys(cells["row_label"])),
            title=None,
            axis=alt.Axis(labelLimit=240, labelOverlap=False),
        ),
        x=alt.X(
            "column_label:N",
            sort=list(dict.fromkeys(cells["column_label"])),
            title=None,
            axis=alt.Axis(labelAngle=0),
        ),
    )
    tip = [
        alt.Tooltip("row_label:N", title="Specialty"),
        alt.Tooltip("column_label:N", title="Age band"),
        alt.Tooltip("share_pct:Q", title="Share (%)", format=".2f"),
        alt.Tooltip("zilretta:Q", title="Zilretta visits", format=","),
        alt.Tooltip("category:Q", title="All three classes", format=","),
    ]
    empty = base.transform_filter("!datum.shown").mark_rect(color="#7A879433").encode(tooltip=tip)
    filled = (
        base.transform_filter("datum.shown")
        .mark_rect()
        .encode(
            color=alt.Color(
                "share_pct:Q",
                scale=alt.Scale(
                    domain=[0, GRID_CAP], clamp=True, range=["#E3F1F1", SERIES, "#0B4F50"]
                ),
                legend=alt.Legend(title=f"Share (%), darkest at {GRID_CAP}%+", orient="bottom"),
            ),
            tooltip=tip,
        )
    )
    text = (
        base.transform_filter("datum.shown")
        .mark_text(fontSize=11)
        .encode(
            text=alt.Text("share_pct:Q", format=".1f"),
            color=alt.condition("datum.share_pct > 4", alt.value("white"), alt.value("#1B2A41")),
        )
    )
    return _style((empty + filled + text).properties(height=alt.Step(26)))


def group_trend_chart(frame: pd.DataFrame) -> alt.Chart:
    """The chosen groups' share by calendar year (a part year is marked in the tooltip)."""
    labels = list(dict.fromkeys(frame["label"]))
    colours = alt.Scale(domain=labels, range=CATEGORICAL[: max(len(labels), 1)])
    base = alt.Chart(frame).encode(
        x=alt.X("year:O", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("share_pct:Q", title="Zilretta's share of visits (%)"),
        color=alt.Color("label:N", scale=colours, legend=alt.Legend(title=None, orient="bottom")),
    )
    tip = [
        alt.Tooltip("label:N", title="Group"),
        alt.Tooltip("year:O", title="Year"),
        alt.Tooltip("share_pct:Q", title="Share (%)", format=".2f"),
        alt.Tooltip("zilretta:Q", title="Zilretta visits", format=","),
        alt.Tooltip("partial_year:N", title="Part year"),
    ]
    line = base.mark_line(strokeWidth=2.5)
    points = base.mark_circle(size=55).encode(tooltip=tip)
    return _style((line + points).properties(height=HEIGHT))


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
