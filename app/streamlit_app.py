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

DB_CANDIDATES = [
    ROOT / "data" / "published" / "warehouse.db",
    ROOT / "data" / "processed" / "warehouse.db",
]
EXAMPLE_QUESTIONS = [
    "How has Zilretta's visit share changed over time?",
    "Which specialties use Zilretta more than the market-wide share would suggest?",
    "Can the models predict next month's direction?",
]
SEGMENT_LABELS = {"specialty": "Specialty", "age_band": "Age band", "gender": "Gender"}

st.set_page_config(page_title="Zilretta Market Intelligence", page_icon="📈", layout="wide")


def _secret(name: str, default: str | None = None) -> str | None:
    try:
        return st.secrets[name]
    except Exception:  # noqa: BLE001 - no secrets file locally is normal
        return os.environ.get(name, default)


def _month_label(month_id: int) -> str:
    return pd.Timestamp(year=month_id // 100, month=month_id % 100, day=1).strftime("%b %Y")


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


def _overview(status: dict, trend: pd.DataFrame) -> None:
    first, last = _month_label(status["first_month_id"]), _month_label(status["last_month_id"])
    share = status["zilretta_visits"] / status["category_visits"]
    cols = st.columns(4)
    cols[0].metric("Months of data", status["n_months"], f"{first} to {last}")
    cols[1].metric("Zilretta visits", f"{status['zilretta_visits']:,}")
    cols[2].metric("Competitive-set visits", f"{status['category_visits']:,}")
    cols[3].metric("Overall visit share", f"{share:.2%}")
    st.markdown(
        "**Visit share** is Zilretta's share of osteoarthritis visits among Zilretta, generic "
        "corticosteroid injections and NSAIDs, month by month. This site shows where Zilretta "
        "is used more or less than the market-wide share would suggest, and how well simple "
        "models can anticipate the month-to-month direction."
    )
    st.info(
        "Honest headline: on the months held out for testing, the trained direction model does "
        "**not** beat chance or the simple baselines, so the site serves a baseline. See "
        "**Model results**."
    )


def _market_trend(trend: pd.DataFrame) -> None:
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
    st.altair_chart((line + marks).properties(height=380), use_container_width=True)
    st.caption(
        "Green and red points are months whose change was unusually large compared with the "
        "previous 12 months' changes (more than one standard deviation). All other months are "
        "labelled Flat."
    )
    with st.expander("Monthly table"):
        shown = trend[["month_id", "visit_share", "direction_label"]].copy()
        shown["visit_share"] = (shown["visit_share"] * 100).round(2)
        st.dataframe(shown.rename(columns={"visit_share": "visit share (%)"}), hide_index=True)


def _segments(path: str, stamp: float) -> None:
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
        y=alt.Y("group:N", sort="-x", title=SEGMENT_LABELS[by]),
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


def _model_results(path: str, stamp: float) -> None:
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
    st.dataframe(scores.rename_axis("model"))
    band = panel["chance"].loc["balanced_accuracy"]
    st.markdown(
        f"Random guessing (300 simulated runs) reaches a balanced accuracy of "
        f"{band['p05']:.2f} to {band['p95']:.2f} (5th to 95th percentile). A trained model "
        f"is promoted only if it is above both that range **and** every baseline."
    )
    st.caption(
        "Why so few months? The extract covers a short history, so the test set is small and "
        "every score has wide uncertainty. This is a negative result reported as such."
    )


def _ask_claude(path: str) -> None:
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
    stamp = os.path.getmtime(path)
    with st.spinner("Asking Claude..."):
        result = ask(
            question,
            client=client,
            engine=engine,
            panel_loader=lambda: load_panel(path, stamp) or compute_panel(path, stamp),
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
features → walk-forward model evaluation → this site.

**What to trust.** Visit counts and shares are descriptive and reconcile to the source
extract. The direction forecasts are *not* reliable: the trained model does not beat chance
on the held-out months, and the site says so rather than hiding it.

**Limits.**
- Visit counts are distinct-patient-visit counts; a visit that involves several products is
  counted for each, so product rows can sum to slightly more than the overall total.
- Segment comparisons are observational. Intervals ignore dependence between visits.
- The history is short (about six years of months), which limits any model.
- Only aggregate tables are shown here; no patient-level rows leave the warehouse.

**Source code and decision log:**
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

    tabs = st.tabs(
        ["Overview", "Market trend", "Segments", "Model results", "Ask Claude", "About & limits"]
    )
    with tabs[0]:
        _overview(status, trend)
    with tabs[1]:
        _market_trend(trend)
    with tabs[2]:
        _segments(path_str, stamp)
    with tabs[3]:
        _model_results(path_str, stamp)
    with tabs[4]:
        _ask_claude(path_str)
    with tabs[5]:
        _about()


main()
