"""The public analytics site: Zilretta visit-share intelligence from IQVIA NMTA data.

Run locally:  streamlit run app/streamlit_app.py
The page is a thin layer over src/oa_market_intelligence/serving, which holds (and tests)
all the logic. It reads aggregate Gold tables only.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oa_market_intelligence.serving.guard import (  # noqa: E402
    AccessGate,
    RateLimiter,
    TokenBudget,
    validate_question,
)
from oa_market_intelligence.serving.model_panel import (  # noqa: E402
    load_stored_panel,
    model_panel,
)
from oa_market_intelligence.serving.qa import DEFAULT_MODEL, ask  # noqa: E402
from oa_market_intelligence.serving.queries import (  # noqa: E402
    data_status,
    market_trend,
    segment_table,
)
from oa_market_intelligence.serving.results import load_result  # noqa: E402
from oa_market_intelligence.serving.site_views import (  # noqa: E402
    adoption_frame,
    decomposition_frame,
    decomposition_headline,
    forecast_caveat,
    forecast_frame,
    forecast_headline,
    model_serving_lines,
    month_label,
    robustness_headline,
    rolling_frame,
    specialty_headline,
    trend_frame,
    trend_headline,
)

DB_CANDIDATES = [
    ROOT / "data" / "published" / "warehouse.db",
    ROOT / "data" / "processed" / "warehouse.db",
]
EXAMPLE_QUESTIONS = [
    "How has Zilretta's visit share changed over time, and when did it change?",
    "Which specialties use Zilretta clearly more than the overall share?",
    "What is the forecast for next month, and should I trust it?",
    "Is anything flagged for review right now?",
]
SEGMENT_LABELS = {"specialty": "Specialty", "age_band": "Age band", "gender": "Gender"}

st.set_page_config(page_title="Zilretta Market Intelligence", page_icon="📈", layout="wide")


def _secret(name: str, default: str | None = None) -> str | None:
    try:
        return st.secrets[name]
    except Exception:  # noqa: BLE001 - no secrets file locally is normal
        return os.environ.get(name, default)


_month_label = month_label


def _db_path() -> Path | None:
    override = _secret("OA_DB_PATH")
    candidates = [Path(override)] if override else DB_CANDIDATES
    return next((path for path in candidates if path.exists()), None)


@st.cache_resource
def get_engine(path: str):
    return create_engine(f"sqlite:///{Path(path).as_posix()}")


@st.cache_data(show_spinner=False)
def load_status(path: str, stamp: float) -> dict:
    return data_status(get_engine(path))


@st.cache_data(show_spinner=False)
def load_trend(path: str, stamp: float) -> pd.DataFrame:
    return market_trend(get_engine(path))


@st.cache_data(show_spinner=False)
def load_segments(path: str, stamp: float, by: str, min_visits: int) -> pd.DataFrame:
    return segment_table(get_engine(path), by=by, min_visits=min_visits)


@st.cache_data(show_spinner=False)
def load_stored(path: str, stamp: float) -> dict | None:
    """The panel computed when the database was published (see publish.py)."""
    return load_stored_panel(get_engine(path))


@st.cache_data(show_spinner=False)
def load_results(path: str, stamp: float) -> dict:
    """Every Phase 4 result stored in the database file when it was published."""
    engine = get_engine(path)
    keys = ("findings", "segment_model", "forecast", "direction", "monitoring")
    return {key: load_result(engine, key) for key in keys}


@st.cache_data(show_spinner="Running the walk-forward model evaluation (about 30 seconds)...")
def compute_panel(path: str, stamp: float) -> dict:
    return model_panel(get_engine(path))


def load_panel(path: str, stamp: float) -> dict | None:
    """Stored panel if there is one; otherwise one that was computed on request."""
    stored = load_stored(path, stamp)
    if stored is not None:
        return stored
    return st.session_state.get("computed_panel")


@st.cache_resource
def get_limits():
    """Shared across all visitors, so one person cannot spend the whole day's budget."""
    return {
        "per_visitor": RateLimiter(max_per_window=8, window_seconds=3600),
        "site": RateLimiter(max_per_window=200, window_seconds=3600),
        "tokens": TokenBudget(daily_tokens=int(_secret("DAILY_TOKEN_BUDGET", "300000"))),
    }


def _overview(status: dict, trend: pd.DataFrame, results: dict) -> None:
    first, last = _month_label(status["first_month_id"]), _month_label(status["last_month_id"])
    share = status["zilretta_visits"] / status["category_visits"]
    cols = st.columns(4)
    cols[0].metric("Months of data", status["n_months"], f"{first} to {last}")
    cols[1].metric("Zilretta visits", f"{status['zilretta_visits']:,}")
    cols[2].metric("Competitive-set visits", f"{status['category_visits']:,}")
    cols[3].metric("Overall visit share", f"{share:.2%}")
    st.markdown(
        "**Visit share** is Zilretta's share of osteoarthritis visits among Zilretta, generic "
        "corticosteroid injections and NSAIDs, month by month. This site shows how that share "
        "has moved, where Zilretta is used more or less than the market, and what simple models "
        "can and cannot say about the months ahead."
    )
    findings, forecast = results.get("findings"), results.get("forecast")
    if findings is None:
        st.info("This database was published without the analysis results.")
        return
    st.subheader("What the data shows")
    st.markdown(
        f"- {trend_headline(findings)}\n"
        f"- {decomposition_headline(findings)}\n"
        f"- {specialty_headline(findings)}\n"
        f"- {robustness_headline(findings)}"
    )
    if forecast is not None:
        st.subheader("What the models can say")
        st.markdown(f"- {forecast_headline(forecast)}\n- {forecast_caveat(forecast)}")
    st.caption(
        "These describe what happened in the data; they do not say why. Payer, geography, "
        "price and practice mix are not in the data."
    )


def _market_trend(trend: pd.DataFrame, findings: dict | None) -> None:
    if findings is not None:
        st.markdown(f"**{trend_headline(findings)}**")
        _trend_chart(findings)
        _decomposition(findings)
    else:
        st.info("This database was published without the trend analysis; showing the raw series.")
        _raw_trend(trend)
    with st.expander("Month-by-month direction labels"):
        if findings is not None:
            _raw_trend(trend)
        shown = trend[["month_id", "visit_share", "direction_label"]].copy()
        shown["visit_share"] = (shown["visit_share"] * 100).round(2)
        st.dataframe(shown.rename(columns={"visit_share": "visit share (%)"}), hide_index=True)


def _trend_chart(findings: dict) -> None:
    frame = trend_frame(findings)
    observed = alt.Chart(frame).mark_line(color="#9ecae1").encode(
        x=alt.X("month:T", title="Month"), y=alt.Y("share_pct:Q", title="Visit share (%)"),
    )
    fitted = alt.Chart(frame).mark_line(color="#d62728", strokeWidth=3).encode(
        x="month:T", y="fitted_pct:Q"
    )
    breaks = pd.DataFrame(
        {"month": [pd.Timestamp(year=m // 100, month=m % 100, day=1)
                   for m in findings["trend"]["break_months"]]}
    )
    marks = alt.Chart(breaks).mark_rule(strokeDash=[6, 4], color="black").encode(x="month:T")
    st.altair_chart((observed + fitted + marks).properties(height=360), use_container_width=True)
    trend = findings["trend"]
    intervals = ", ".join(
        f"{month_label(lo)} to {month_label(hi)}" for lo, hi in trend["intervals"]
    )
    plural = "s" if len(trend["break_months"]) != 1 else ""
    st.caption(
        "Light line: observed monthly share. Red line: the fitted trend, which may change "
        "direction only where a statistical test supports it. Dashed line: the detected "
        f"change{plural}" + (f" (plausible range: {intervals})" if intervals else "")
        + ". A change point shows when the pattern shifted, not why."
    )


def _decomposition(findings: dict) -> None:
    st.subheader("Where visits happen, or Zilretta's share within each place?")
    st.markdown(decomposition_headline(findings))
    labels = {"comparison": "Years compared", "total": "Total change (pp)",
              "mix": "From specialty mix (pp)", "rate": "From share within specialty (pp)"}
    st.dataframe(
        decomposition_frame(findings).rename(columns=labels),
        hide_index=True,
        column_config={
            name: st.column_config.NumberColumn(format="%.2f") for name in list(labels.values())[1:]
        },
    )
    st.caption(
        "Windows are consecutive 12-month periods of the data. 'pp' = percentage points. Mix + "
        "within-specialty share adds up to the total change."
    )


def _raw_trend(trend: pd.DataFrame) -> None:
    frame = trend.assign(share_pct=trend["visit_share"] * 100)
    line = alt.Chart(frame).mark_line(color="#4c78a8").encode(
        x=alt.X("month:T", title="Month"),
        y=alt.Y("share_pct:Q", title="Visit share (%)"),
    )
    marks = (
        alt.Chart(frame[frame["direction_label"].isin(["Up", "Down"])])
        .mark_point(size=90, filled=True)
        .encode(
            x="month:T",
            y="share_pct:Q",
            color=alt.Color(
                "direction_label:N",
                scale=alt.Scale(domain=["Up", "Down"], range=["#2ca02c", "#d62728"]),
                title="Unusual move",
            ),
            tooltip=["month:T", alt.Tooltip("share_pct:Q", format=".2f"), "direction_label"],
        )
    )
    st.altair_chart((line + marks).properties(height=300), use_container_width=True)
    st.caption(
        "Green and red points are months whose change was unusually large compared with the "
        "previous 12 months' changes (more than one standard deviation). All other months are "
        "labelled Flat."
    )


def _adoption(findings: dict) -> None:
    st.subheader("Which specialties use Zilretta more than the market?")
    st.markdown(specialty_headline(findings))
    frame = adoption_frame(findings)
    overall = findings["adoption"]["overall_share"] * 100
    base = alt.Chart(frame).encode(
        y=alt.Y("specialty:N", sort="-x", title="Specialty",
                axis=alt.Axis(labelLimit=260, labelOverlap=False))
    )
    bars = base.mark_bar(color="#9ecae1").encode(
        x=alt.X("adjusted_share:Q", title="Adjusted Zilretta share (%)"),
        tooltip=["specialty", alt.Tooltip("adjusted_share:Q", format=".2f"), "position"],
    )
    ranges = base.mark_rule(color="#3b3b3b").encode(x="low:Q", x2="high:Q")
    line = alt.Chart(pd.DataFrame({"x": [overall]})).mark_rule(
        color="#d62728", strokeDash=[6, 4]
    ).encode(x="x:Q")
    st.altair_chart((bars + ranges + line).properties(height=max(220, 30 * len(frame))),
                    use_container_width=True)
    st.caption(
        "Bars: Zilretta's share of each specialty's visits after adjusting for age and gender "
        f"mix, with its uncertainty range. Red line: the overall share ({overall:.2f}%). A "
        "specialty is 'clearly' above or below only when its whole range is on one side. "
        "Adjusted shares describe use, not why; payer, geography and practice mix are not in "
        "the data."
    )
    with st.expander("Table and stability across the two halves of the period"):
        labels = {"specialty": "Specialty", "adjusted_share": "Adjusted share (%)",
                  "low": "Low (%)", "high": "High (%)",
                  "visit_share_pct": "Share of all visits (%)", "position": "Versus overall"}
        st.dataframe(frame.rename(columns=labels), hide_index=True)
        stability = findings["adoption"]["stability"]
        flipped = [row["specialty"] for row in stability["table"] if row["flipped"]]
        moved = (f"Moved to the other side of the overall share: {', '.join(flipped)}."
                 if flipped else "No specialty moved to the other side of the overall share.")
        st.markdown(
            f"Rank correlation between the first and second half: **{stability['spearman']:.2f}** "
            f"(range {stability['low']:.2f} to {stability['high']:.2f}). {moved}"
        )


def _segments(path: str, stamp: float, findings: dict | None) -> None:
    if findings is not None:
        _adoption(findings)
        st.divider()
        st.subheader("Observed against expected share, by group")
    left, right = st.columns([2, 1])
    by = left.radio(
        "Group by", list(SEGMENT_LABELS), format_func=SEGMENT_LABELS.get, horizontal=True
    )
    min_visits = right.slider("Hide groups with fewer category visits than", 0, 2000, 200, 50)
    table = load_segments(path, stamp, by, min_visits)
    if table.empty:
        st.warning("No groups meet that volume threshold.")
        return
    top = table.head(15)
    chart = alt.Chart(top).mark_bar().encode(
        y=alt.Y("group:N", sort="-x", title=SEGMENT_LABELS[by],
                axis=alt.Axis(labelLimit=260, labelOverlap=False)),
        x=alt.X("ratio:Q", title="Observed share ÷ market-wide expected share"),
        color=alt.condition(
            alt.datum.ratio >= 1, alt.value("#2ca02c"), alt.value("#9ecae1")
        ),
        tooltip=["group", alt.Tooltip("ratio:Q", format=".2f"),
                 alt.Tooltip("observed_share:Q", format=".2%")],
    )
    rule = alt.Chart(pd.DataFrame({"x": [1.0]})).mark_rule(color="black").encode(x="x:Q")
    st.altair_chart((chart + rule).properties(height=max(200, 26 * len(top))),
                    use_container_width=True)
    st.dataframe(
        table.rename(
            columns={
                "group": SEGMENT_LABELS[by],
                "zilretta_visits": "Zilretta visits",
                "category_visits": "Category visits",
                "observed_share": "Observed share",
                "expected_share": "Expected share",
                "ratio": "Ratio",
                "ci_low": "Interval low",
                "ci_high": "Interval high",
            }
        ),
        hide_index=True,
        column_config={
            "Observed share": st.column_config.NumberColumn(format="%.2f%%"),
            "Expected share": st.column_config.NumberColumn(format="%.2f%%"),
            "Interval low": st.column_config.NumberColumn(format="%.2f%%"),
            "Interval high": st.column_config.NumberColumn(format="%.2f%%"),
            "Ratio": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "A ratio above 1 means the group uses Zilretta more than the market-wide share would "
        "suggest. Treat gaps as leads to investigate, not proof of an opportunity: the data "
        "cannot see payer, geography or practice mix, and these intervals understate the real "
        "uncertainty because visits are not independent."
    )


def _forecast_monitoring(results: dict) -> None:
    forecast, monitoring = results.get("forecast"), results.get("monitoring")
    if forecast is None or monitoring is None:
        st.info("This database was published without the forecast and monitoring results.")
        return
    nxt = forecast["next_month"]
    cols = st.columns(3)
    cols[0].metric(f"{month_label(nxt['month_id'])}: most likely share", f"{nxt['point']:.2f}%")
    cols[1].metric("80% range", f"{nxt['lo80']:.2f}–{nxt['hi80']:.2f}%")
    cols[2].metric("90% range", f"{nxt['lo90']:.2f}–{nxt['hi90']:.2f}%")
    st.markdown(forecast_caveat(forecast))
    history = forecast_frame(forecast)
    band90 = alt.Chart(history).mark_area(opacity=0.15, color="#4c78a8").encode(
        x=alt.X("month:T", title="Month"), y=alt.Y("lo90:Q", title="Visit share (%)"), y2="hi90:Q"
    )
    band80 = alt.Chart(history).mark_area(opacity=0.25, color="#4c78a8").encode(
        x="month:T", y="lo80:Q", y2="hi80:Q"
    )
    actual = alt.Chart(history).mark_line(color="#3b3b3b").encode(x="month:T", y="actual:Q")
    st.altair_chart((band90 + band80 + actual).properties(height=300), use_container_width=True)
    st.caption(
        "Dark line: the actual share. Shaded bands: the 80% and 90% ranges the forecast gave "
        "for each month before it was known, using only earlier months. If the ranges are "
        "honest, about 90% of actual values fall inside the wide band."
    )

    st.subheader("Is the model still behaving as it did in testing?")
    status = monitoring["status"]
    (st.warning if status == "review" else st.success)(
        monitoring["banner"] or "No monitor is signalling a problem."
    )
    direction, interval = monitoring["direction"], monitoring["forecast"]
    rolling = rolling_frame(monitoring)
    line = alt.Chart(rolling).mark_line(color="#4c78a8").encode(
        x=alt.X("month:T", title="Month"),
        y=alt.Y("accuracy:Q", title="Rolling 6-month accuracy", scale=alt.Scale(domain=[0, 1])),
    )
    rules = alt.Chart(
        pd.DataFrame(
            {"y": [direction["threshold"], direction["random_line"]],
             "label": ["Review threshold", "Random guessing"]}
        )
    ).mark_rule(strokeDash=[6, 4]).encode(
        y="y:Q", color=alt.Color("label:N", title=None)
    )
    st.altair_chart((line + rules).properties(height=240), use_container_width=True)
    st.markdown(
        f"- **Direction classifier ({direction['model'].replace('_', ' ')}):** latest rolling "
        f"accuracy {direction['latest_rolling']:.0%}; it matched {direction['match_rate']:.0%} "
        f"of the time in testing; review if it falls to {direction['threshold']:.0%} or below.\n"
        f"- **Forecast ranges:** {interval['misses_in_window']} of the last {interval['n']} "
        f"months fell outside the {interval['level']}% range; the alarm is raised at "
        f"{interval['k']} or more. During testing this alarm fired in "
        f"{interval['backtest_alarm_windows']} of {interval['n_windows']} windows."
    )
    st.caption(
        "A signal means 'look at the model again', not that it is wrong. The forecast alarm is "
        "deliberately conservative: it rarely fires by accident, so it can miss a modest "
        "change in how volatile the share is."
    )
    detection = monitoring.get("detection")
    if detection:
        with st.expander("How quickly would the forecast alarm notice a problem? (simulation)"):
            st.dataframe(pd.DataFrame(detection), hide_index=True)


def _model_results(path: str, stamp: float, results: dict) -> None:
    lines = model_serving_lines(results)
    if lines:
        st.subheader("What the site serves")
        st.dataframe(pd.DataFrame(lines), hide_index=True)
        st.caption(
            "A trained model is promoted only if it beats every simple baseline on held-out "
            "months by a margin larger than chance (rules fixed before the models were run)."
        )
    _segment_model(results.get("segment_model"))
    st.subheader("Direction: will next month's share go Up, Flat or Down?")
    _direction_model(path, stamp, results.get("direction"))


def _segment_model(segment: dict | None) -> None:
    if segment is None:
        return
    st.subheader("Segment share: Zilretta's share in each specialty-age-gender segment")
    decision = segment["decision"]
    st.markdown(
        f"Predicting next month's share for each segment, tested walk-forward on "
        f"{segment['n_test_months']} months. Serving: **{decision['serving']}**. The best "
        f"baseline was **{segment['best_baseline'].replace('_', ' ')}**."
    )
    scores = pd.DataFrame.from_dict(segment["scores"], orient="index").round(4)
    st.dataframe(scores.rename_axis("model"))
    calibration = segment["calibration"]
    st.markdown(
        f"Average predicted share {calibration['mean_pred']:.2%} against actual "
        f"{calibration['mean_obs']:.2%}; calibration slope {calibration['slope']:.2f} "
        "(1.0 is perfect)."
    )
    by_size = pd.DataFrame(segment["by_size"])
    if not by_size.empty:
        with st.expander("Where the model helps most (by segment size)"):
            st.dataframe(by_size.round(4), hide_index=True)
    st.caption(
        "Lower error is better. The gain over the simple baseline is modest, and is largest "
        "for small segments where last month's value is noisy."
    )


def _direction_model(path: str, stamp: float, direction: dict | None) -> None:
    panel = load_panel(path, stamp)
    if panel is None:
        st.info(
            "This database was not published with model results. "
            "Computing them here takes about 30 seconds."
        )
        if st.button("Run the model evaluation"):
            st.session_state["computed_panel"] = compute_panel(path, stamp)
            st.rerun()
        return
    decision = panel["decision"]
    (st.success if decision["promoted"] else st.warning)(
        f"**Serving: {decision['serving']}.** {decision['reason']}"
    )
    counts = panel["label_counts"]
    st.markdown(
        f"Task: predict whether next month's visit share moves **Up**, **Flat** or **Down**. "
        f"Tested walk-forward on {panel['n_test']} months "
        f"({counts.get('Flat', 0)} Flat, {counts.get('Down', 0)} Down, {counts.get('Up', 0)} Up): "
        f"each month is predicted using only earlier months."
    )
    scores = panel["scores"][["accuracy", "balanced_accuracy", "macro_f1"]].round(3)
    if direction is not None:
        extra = pd.DataFrame.from_dict(direction["scores"], orient="index")
        extra = extra.loc[[m for m in ("rf", "gbm") if m in extra.index],
                          ["accuracy", "balanced_accuracy", "macro_f1"]].round(3)
        scores = pd.concat([scores, extra.rename(index={"rf": "random forest",
                                                       "gbm": "gradient boosting"})])
    st.dataframe(scores.rename_axis("model"))
    band = panel["chance"].loc["balanced_accuracy"]
    st.markdown(
        f"Random guessing reaches a balanced accuracy of "
        f"{band['p05']:.2f} to {band['p95']:.2f} (5th to 95th percentile). A trained model "
        f"is promoted only if it is above both that range **and** every baseline."
    )
    st.caption(
        "Why so few months? The extract covers a short history, so the test set is small and "
        "every score has wide uncertainty. This is a negative result reported as such."
    )
    if direction is not None:
        power = direction["power"]
        st.markdown(
            f"**What these tests could detect.** With {direction['n_test']} test months, a model "
            f"would need to beat the persistence baseline by roughly "
            f"{power['smallest_detectable_gain'] * 100:.0f} percentage points of accuracy to be "
            "reliably distinguished from it, so a small real improvement would not have been "
            "visible."
        )


def _ask_claude(path: str, stamp: float) -> None:
    api_key = _secret("ANTHROPIC_API_KEY")
    if not api_key:
        st.info("The question box is not configured on this deployment (no API key).")
        return
    gate = AccessGate(_secret("ACCESS_CODE"))
    if "visitor" not in st.session_state:
        st.session_state["visitor"] = uuid.uuid4().hex
    attempt = None
    if not gate.allows(None):
        attempt = st.text_input("Access code", type="password")
        if not gate.allows(attempt):
            st.caption("Enter the access code to use the question box.")
            return

    st.caption(
        "Claude answers from pre-written, read-only summaries of the aggregate tables on this "
        "page. It cannot see patient-level rows or run its own queries."
    )
    for example in EXAMPLE_QUESTIONS:
        if st.button(example, key=example):
            st.session_state["question"] = example
    question = st.text_area("Your question", key="question", max_chars=500)
    if not st.button("Ask", type="primary"):
        return

    try:
        question = validate_question(question)
    except ValueError as error:
        st.warning(str(error))
        return
    limits = get_limits()
    if not limits["tokens"].has_room():
        st.warning("The question box has reached its daily limit. Please try again tomorrow.")
        return
    if not (limits["per_visitor"].allow(st.session_state["visitor"]) and limits["site"].allow("x")):
        st.warning("Too many questions in the last hour. Please try again later.")
        return

    import anthropic  # imported here so the rest of the site works without it

    client = anthropic.Anthropic(api_key=api_key)
    engine = get_engine(path)
    stored = load_results(path, stamp)
    with st.spinner("Asking Claude..."):
        result = ask(
            question,
            client=client,
            engine=engine,
            panel_loader=lambda: load_panel(path, stamp) or compute_panel(path, stamp),
            results_loader=stored.get,
            model=_secret("CLAUDE_MODEL", DEFAULT_MODEL),
        )
    limits["tokens"].charge(result["input_tokens"] + result["output_tokens"])
    (st.write if result["ok"] else st.warning)(result["answer"])
    if result["tool_calls"]:
        with st.expander("Data the answer used"):
            for name, arguments in result["tool_calls"]:
                st.code(f"{name}({arguments})")


def _about() -> None:
    st.markdown(
        """
**What this is.** A capstone project (Penn State DAAN 888) turned into a small, deployed
data-and-ML system: IQVIA NMTA visit data → Bronze/Silver/Gold warehouse → validation →
features → walk-forward model evaluation → this site. Every result on the site is computed
once when the data is published and stored with it, so the page never fits a model while you
wait and always shows numbers that match the tables beside them.

**What to trust.** Visit counts, shares, the trend change and the specialty comparisons are
descriptions of the data and reconcile to the source extract. The models are weaker:
- The segment model improves modestly on a simple baseline.
- No trained model beat the simple forecast of "next month looks like this month", so the
  forecast ranges are reference ranges, not predictions of a change.
- The direction (Up/Flat/Down) models did not beat chance on the held-out months, and the
  test set was too small to detect anything but a large improvement.

**Limits.**
- Findings describe what happened, never why. Payer, geography, price and prescriber
  practice mix are not in the data; reasons for a change in share cannot be read from it.
- Visit counts are distinct-patient-visit counts; a visit that involves several products is
  counted for each, so product rows can sum to slightly more than the overall total.
- Segment comparisons are observational. Intervals ignore dependence between visits.
- The history is 72 months, which limits any model.
- Only aggregate tables are shown here; no patient-level rows leave the warehouse.

**Source code, protocols and decision log:**
[github.com/tus2014ar/oa-market-intelligence-system](https://github.com/tus2014ar/oa-market-intelligence-system)
"""
    )


def main() -> None:
    st.title("Zilretta Market Intelligence")
    st.caption("Osteoarthritis visit-share analytics from IQVIA NMTA data · capstone project")
    path = _db_path()
    if path is None:
        st.error("The warehouse has not been published yet. Please check back soon.")
        return
    path_str, stamp = str(path), os.path.getmtime(path)
    status, trend = load_status(path_str, stamp), load_trend(path_str, stamp)
    results = load_results(path_str, stamp)
    monitoring = results.get("monitoring")
    if monitoring is not None and monitoring["status"] == "review":
        st.warning(f"**Review suggested.** {monitoring['banner']} See **Forecast & monitoring**.")

    tabs = st.tabs(
        ["Overview", "Market trend", "Segments", "Forecast & monitoring", "Model results",
         "Ask Claude", "About & limits"]
    )
    with tabs[0]:
        _overview(status, trend, results)
    with tabs[1]:
        _market_trend(trend, results.get("findings"))
    with tabs[2]:
        _segments(path_str, stamp, results.get("findings"))
    with tabs[3]:
        _forecast_monitoring(results)
    with tabs[4]:
        _model_results(path_str, stamp, results)
    with tabs[5]:
        _ask_claude(path_str, stamp)
    with tabs[6]:
        _about()


main()
