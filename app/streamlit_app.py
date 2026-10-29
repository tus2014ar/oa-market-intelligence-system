"""The public site: Zilretta visit-share intelligence from IQVIA NMTA data (DL-85).

Run locally:  streamlit run app/streamlit_app.py

A thin page over `oa_market_intelligence.serving`, which holds (and tests) the logic and the wording.
It reads the published warehouse read-only, shows aggregates only and fits nothing.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from oa_market_intelligence import knowledge_graph  # noqa: E402
from oa_market_intelligence.serving import (  # noqa: E402
    charts,
    data,
    guard,
    qa,
    qa_tools,
    views,
)

DB = Path(os.environ.get("OA_DB_PATH", data.DB_PATH))
RUN_LOG = Path(os.environ.get("OA_RUN_LOG", data.RUN_LOG))
NAVY, TEAL, AMBER, RUST, GREY = "#1B2A41", "#0F8B8D", "#E8A33D", "#C8553D", "#7A8794"

st.set_page_config(page_title="OA and RA Market Intelligence", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def load(db: str, run_log: str) -> dict:
    engine = data.open_engine(db)
    try:
        return {
            "monthly": data.market_monthly(engine),
            "pred": data.predictions(engine),
            "scores": data.model_scores(engine),
            "by_specialty": data.segments(engine, "specialty"),
            "by_age": data.segments(engine, "age_band"),
            "by_gender": data.segments(engine, "gender"),
            "by_year": data.specialty_by_year(engine),
            "run": data.last_run(run_log),
        }
    finally:
        engine.dispose()


D = load(str(DB), str(RUN_LOG))
ADOPTION, POSTHOC, FINDINGS = data.serving_map(), data.posthoc(), data.findings()
STATUS = views.status_block(D["run"])
HEAD = views.headline(D["monthly"], D["pred"])
SNAPSHOT = qa_tools.Snapshot(
    monthly=D["monthly"],
    pred=D["pred"],
    scores=D["scores"],
    by_specialty=D["by_specialty"],
    by_age=D["by_age"],
    by_gender=D["by_gender"],
    run=D["run"],
    adoption=ADOPTION,
    posthoc=POSTHOC,
    findings=FINDINGS,
    graph=knowledge_graph.Graph.load(),
)
EXAMPLE_QUESTIONS = {
    "For a brand manager": [
        "How has Zilretta's share changed since its peak?",
        "What is the forecast for the next three months, and how far can I trust it?",
        "Who are Zilretta's competitors in this project?",
        "Show me the share trend as a chart.",
    ],
    "For an analyst": [
        "Which model is used for the national forecast, and did any model beat the simple rule?",
        "Do the segment forecasts run high?",
        "Which segments does the decline alert flag, and how reliable is it?",
        "Where do Zilretta's visits come from?",
    ],
}
TOOL_LABELS = {
    "get_data_status": "the headline numbers",
    "get_market_trend": "the market trend",
    "get_segments": "the segments",
    "get_forecasts": "the forecasts",
    "get_model_results": "how the models scored",
    "get_findings": "the findings",
    "get_entity": "the knowledge graph",
    "get_neighbors": "the knowledge graph",
    "get_competitors": "the competitor groups",
    "get_events": "the event timeline",
    "show_chart": "a chart",
}


def _secret(name: str, default: str | None = None) -> str | None:
    """A secret from the host's secrets store or the environment; never from the repository."""
    try:
        return st.secrets[name]
    except Exception:  # noqa: BLE001 - no secrets file locally is normal
        return os.environ.get(name, default)


@st.cache_resource
def get_limits() -> dict:
    """Shared by all visitors, so one person cannot spend the whole day's budget."""
    return {
        "per_visitor": guard.RateLimiter(max_per_window=8, window_seconds=3600),
        "site": guard.RateLimiter(max_per_window=200, window_seconds=3600),
        "tokens": guard.TokenBudget(daily_tokens=int(_secret("DAILY_TOKEN_BUDGET", "300000"))),
    }


def _show_answer(message: dict) -> None:
    """One assistant turn: the answer, any chart it asked for, its sources and its caveats."""
    st.write(message["content"])
    for spec in message.get("charts", []):
        st.altair_chart(charts.build(spec["chart"], spec["params"], SNAPSHOT), width="stretch")
    if message.get("sources"):
        st.caption("Sources: " + "; ".join(message["sources"]))
    if message.get("unchecked"):
        st.caption(
            "Check these figures: " + ", ".join(message["unchecked"]) + " (not found in the "
            "data it looked up; it may be a calculation)."
        )
    if message.get("tool_calls"):
        with st.expander("Data the answer used"):
            for name, arguments in message["tool_calls"]:
                st.code(f"{name}({arguments})")


def ask_box() -> None:
    """The question box: a conversation with streamed answers, charts, sources and a number
    check. It works only where an API key is configured, and then behind an optional access code,
    a per-visitor limit and a daily token budget."""
    api_key = _secret("ANTHROPIC_API_KEY")
    if not api_key:
        st.info("The question box is not switched on for this copy of the site (no API key).")
        return
    gate = guard.AccessGate(_secret("ACCESS_CODE"))
    if "visitor" not in st.session_state:
        st.session_state["visitor"] = uuid.uuid4().hex
    if not gate.allows(None):
        attempt = st.text_input("Access code", type="password")
        if not gate.allows(attempt):
            st.caption("Enter the access code to use the question box.")
            return

    chat = st.session_state.setdefault("chat", [])
    st.caption(
        "Claude answers from read-only summaries of the tables and the knowledge graph on this "
        "site. It cannot see patient-level rows or run its own queries, and it can be wrong: "
        "check the numbers against the tabs."
    )
    pending = st.session_state.pop("pending", None)
    if not chat and not pending:
        for audience, questions in EXAMPLE_QUESTIONS.items():
            st.write(f"**{audience}**")
            for question in questions:
                if st.button(question, key=f"ex-{question}"):
                    st.session_state["pending"] = question
                    st.rerun()
    for message in chat:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                _show_answer(message)
            else:
                st.write(message["content"])
    if chat and chat[-1]["role"] == "assistant" and chat[-1].get("followups") and not pending:
        st.write("**Ask next**")
        for i, text in enumerate(chat[-1]["followups"]):
            if st.button(text, key=f"fu-{len(chat)}-{i}"):
                st.session_state["pending"] = text
                st.rerun()
    if chat and st.button("Clear the conversation"):
        st.session_state["chat"] = []
        st.rerun()

    typed = st.chat_input("Ask about the market, the forecasts or the data", max_chars=500)
    question = typed or pending
    if not question:
        return
    try:
        question = guard.validate_question(question)
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
    history = [{"role": m["role"], "content": m["content"]} for m in chat if m.get("ok", True)]
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        status, box, text = st.empty(), st.empty(), [""]

        def on_round() -> None:
            text[0] = ""
            box.empty()

        def on_text(piece: str) -> None:
            text[0] += piece
            box.markdown(text[0])

        def on_tool(name: str, arguments: dict) -> None:
            status.caption(f"Looking up {TOOL_LABELS.get(name, 'the data')}...")

        result = qa.ask(
            question,
            client=client,
            snapshot=SNAPSHOT,
            history=history,
            model=_secret("CLAUDE_MODEL", qa.DEFAULT_MODEL),
            on_text=on_text,
            on_tool=on_tool,
            on_round=on_round,
        )
    limits["tokens"].charge(result["input_tokens"] + result["output_tokens"])
    chat.append({"role": "user", "content": question})
    chat.append(
        {
            "role": "assistant",
            "content": result["answer"],
            "ok": result["ok"],
            "tool_calls": result["tool_calls"],
            "sources": result["sources"],
            "charts": result["charts"],
            "unchecked": result["unchecked_numbers"],
            "followups": result["followups"],
        }
    )
    st.rerun()


st.title("OA and RA Market Intelligence")
st.caption(
    "Zilretta's share of visits against generic steroid injections and NSAIDs, from IQVIA NMTA "
    "visit counts (aggregated; no patient-level data). Built for an OA and RA injectable brand manager."
)
if STATUS["banner"]:
    st.warning(STATUS["banner"])

tabs = st.tabs(
    [
        "Overview",
        "Market trend",
        "Segments",
        "Forecasts",
        "Model results",
        "Data status",
        "Findings",
        "Ask Claude",
    ]
)

# ------------------------------------------------------------------ overview
with tabs[0]:
    c = st.columns(4)
    c[0].metric(f"Share in {HEAD['latest_month']}", views.pct(HEAD["latest_share"]))
    c[1].metric(f"Peak ({HEAD['peak_month']})", views.pct(HEAD["peak_share"]))
    c[2].metric("Change since the peak", f"{100 * HEAD['change_since_peak']:.0f}%")
    low, high = HEAD["next_month_range90"]
    c[3].metric(
        f"Forecast for {HEAD['next_month']}",
        views.pct(HEAD["next_month_forecast"]),
        help=f"Likely range (90%): {views.pct(low)} to {views.pct(high)}",
    )
    st.subheader("What to use, and what not to trust")
    st.table(
        pd.DataFrame(views.use_and_dont(ADOPTION))
        .rename(columns={"what": "Forecast", "use": "Use it for", "dont": "Do not"})
        .set_index("Forecast")
    )
    st.subheader("Read this first")
    st.markdown(
        "- The numbers describe **visits in IQVIA's panel**, not company sales, and they do not explain "
        "*why* the share moves.\n"
        f"- {views.bias_sentence(D['pred'])}\n"
        "- Everything here was tested under rules written before any model ran; where nothing beat a "
        "simple rule, the site says so.\n"
        "- Payer, regional and prescription-volume data are not part of this analysis."
    )

# ------------------------------------------------------------------ market trend
with tabs[1]:
    trend = views.trend_frame(D["monthly"])
    events = views.event_frame(D["monthly"])
    dip = pd.DataFrame({"start": [pd.Timestamp("2024-03-01")], "end": [pd.Timestamp("2024-07-31")]})
    line = (
        alt.Chart(trend)
        .mark_line(color=TEAL, strokeWidth=3)
        .encode(
            x=alt.X("month:T", title=None),
            y=alt.Y(
                "share_pct:Q", title="Zilretta share of visits (%)", scale=alt.Scale(zero=False)
            ),
            tooltip=[
                alt.Tooltip("month:T", format="%b %Y"),
                alt.Tooltip("share_pct:Q", format=".2f", title="share %"),
            ],
        )
    )
    band = alt.Chart(dip).mark_rect(color=AMBER, opacity=0.2).encode(x="start:T", x2="end:T")
    rules = (
        alt.Chart(events)
        .mark_rule(color=GREY, strokeDash=[4, 4])
        .encode(x="month:T", tooltip=alt.Tooltip("label:N", title="Event"))
    )
    st.altair_chart((band + line + rules).properties(height=380), width="stretch")
    st.caption(
        "Dotted lines: the peak, the turn and the start of the 2024 dip. Shaded: the March to July 2024 data-capture dip."
    )
    st.write(views.trend_note(D["monthly"]))
    yearly = views.yearly_table(D["monthly"])
    st.dataframe(
        yearly[["year", "share_pct", "zilretta", "months", "partial_year"]].rename(
            columns={
                "year": "Year",
                "share_pct": "Share (%)",
                "zilretta": "Zilretta visits",
                "months": "Months of data",
                "partial_year": "Part year",
            }
        ),
        hide_index=True,
        column_config={"Share (%)": st.column_config.NumberColumn(format="%.2f")},
    )

# ------------------------------------------------------------------ segments
with tabs[2]:
    by = st.radio("Group visits by", ["Specialty", "Age band", "Gender"], horizontal=True)
    frame = views.specialty_frame(
        {"Specialty": D["by_specialty"], "Age band": D["by_age"], "Gender": D["by_gender"]}[by]
    )
    bars = (
        alt.Chart(frame)
        .mark_bar(color=TEAL)
        .encode(
            y=alt.Y("group:N", sort="-x", title=None),
            x=alt.X("share_pct:Q", title="Zilretta share of visits (%)"),
            tooltip=["group", alt.Tooltip("share_pct:Q", format=".2f"), "category_visits"],
        )
    )
    st.altair_chart(bars.properties(height=max(120, 28 * len(frame))), width="stretch")
    st.dataframe(
        frame[["group", "share_pct", "zilretta_visits", "category_visits"]].rename(
            columns={
                "group": by,
                "share_pct": "Share (%)",
                "zilretta_visits": "Zilretta visits",
                "category_visits": "All three classes",
            }
        ),
        hide_index=True,
        column_config={"Share (%)": st.column_config.NumberColumn(format="%.2f")},
    )
    st.caption(
        "Over all 72 months; groups under 1,000 visits are left out. Small groups are noisy: "
        "one prescription can move a small group's share a lot."
    )
    if by == "Specialty":
        top = list(D["by_specialty"].head(5)["group"])
        pick = st.multiselect(
            "Specialties over time", list(D["by_specialty"]["group"]), default=top
        )
        tr = views.specialty_trend(D["by_year"], pick)
        if len(tr):
            st.altair_chart(
                alt.Chart(tr)
                .mark_line(point=True)
                .encode(
                    x=alt.X("year:O", title=None),
                    y=alt.Y("share_pct:Q", title="Share (%)"),
                    color="specialty:N",
                    tooltip=["specialty", "year", alt.Tooltip("share_pct:Q", format=".2f")],
                )
                .properties(height=340),
                width="stretch",
            )
            st.caption("2019 and 2025 are part years.")

# ------------------------------------------------------------------ forecasts
with tabs[3]:
    pred = D["pred"]
    bias = views.known_bias(pred)
    st.subheader("National share")
    nat = views.national_forecast(pred)
    base = alt.Chart(nat).encode(x=alt.X("month:N", sort=list(nat["month"]), title=None))
    st.altair_chart(
        (
            base.mark_area(opacity=0.2, color=TEAL).encode(
                y=alt.Y("low_90_pct:Q", title="Share (%)", scale=alt.Scale(zero=False)),
                y2="high_90_pct:Q",
            )
            + base.mark_line(point=True, color=TEAL).encode(y="forecast_pct:Q")
        ).properties(height=300),
        width="stretch",
    )
    st.dataframe(
        nat[["months_ahead", "month", "forecast_pct", "low_90_pct", "high_90_pct", "model"]].rename(
            columns={
                "months_ahead": "Months ahead",
                "month": "Target month",
                "forecast_pct": "Forecast (%)",
                "low_90_pct": "Likely low (%)",
                "high_90_pct": "Likely high (%)",
                "model": "Method",
            }
        ),
        hide_index=True,
        column_config={
            c: st.column_config.NumberColumn(format="%.2f")
            for c in ("Forecast (%)", "Likely low (%)", "Likely high (%)")
        },
    )
    st.caption(
        'The method is "same as last month": the forecast is the latest value with a likely range. '
        f"No model beat it. {views.coverage_sentence(D['scores'])}"
    )
    st.subheader("Segments, by specialty")
    shown = ", ".join(
        f"{h} month{'s' if h != 1 else ''}: "
        + (f"+{100 * b:.1f}% too high" if b is not None else "no tested bias (simple rule)")
        for h, b in bias.items()
    )
    st.info(f"Known bias in testing: {shown}.")
    sf = views.specialty_forecast(pred)
    st.dataframe(
        sf.rename(
            columns={
                "specialty": "Specialty",
                "1_month_pct": "1 month (%)",
                "3_month_pct": "3 months (%)",
                "6_month_pct": "6 months (%)",
                "visits_at_origin": "Visits in latest month",
            }
        ),
        hide_index=True,
        column_config={
            c: st.column_config.NumberColumn(format="%.2f")
            for c in ("1 month (%)", "3 months (%)", "6 months (%)")
        },
    )
    one = st.selectbox("See the segments in one specialty", ["(none)"] + list(sf["specialty"]))
    if one != "(none)":
        st.dataframe(views.segment_forecast(pred, one), hide_index=True)
    st.subheader("Decline alert (1 month)")
    alerts = views.alert_frame(pred)
    summ = views.alert_summary(alerts, D["scores"])
    st.write(
        f"{summ['flagged']} of {summ['scored']} scored segments are flagged as likely to fall by 30% or more. "
        f"About {round(100 * summ['precision'])}% of alerts were real declines in testing, so treat the list as a "
        "starting point for a look, never as a reason to act on its own."
    )
    st.dataframe(
        alerts[alerts["alert_flag"] == 1]
        .drop(columns="alert_flag")
        .rename(columns={"alert_score": "Alert score", "origin_visits": "Visits in latest month"}),
        hide_index=True,
    )

# ------------------------------------------------------------------ model results
with tabs[4]:
    board = views.scoreboard(D["scores"], ADOPTION, POSTHOC)
    st.write(
        "Each row compares the models with a simple rule, under tests fixed before anything ran. "
        '"Adopted" is what the project uses (decision DL-79).'
    )
    order = [
        "task",
        "horizon",
        "verdict",
        "adopted",
        "adopted_model",
        "measure",
        "simple_rule",
        "simple_rule_score",
        "chosen_model",
        "chosen_model_score",
    ]
    st.dataframe(
        board[order].rename(
            columns={
                "task": "Task",
                "horizon": "Months ahead",
                "measure": "Measure",
                "simple_rule": "Simple rule",
                "simple_rule_score": "Simple rule score",
                "chosen_model": "Model chosen by the rule",
                "chosen_model_score": "Its score",
                "adopted": "Adopted",
                "adopted_model": "Adopted model",
                "verdict": "In plain words",
            }
        ),
        hide_index=True,
        column_config={
            "Simple rule score": st.column_config.NumberColumn(format="%.4f"),
            "Its score": st.column_config.NumberColumn(format="%.4f"),
        },
    )
    st.caption(
        "Lower is better for the first two measures; higher is better for the ranking score."
    )
    st.subheader("Do the segment forecasts run high?")
    st.dataframe(
        views.bias_table(D["scores"]).rename(
            columns={
                "horizon": "Months ahead",
                "forecast_above_actual_pct": "Forecast above what happened (%)",
            }
        ),
        hide_index=True,
    )
    st.subheader("The caveat on the decline alert")
    st.write(
        "The alert passed its registered test, but one of the simple rules it was tested against was written "
        "the wrong way round. With only that corrected (a check made after the results were seen), a one-line "
        'rule "this segment just spiked" does almost as well as the model, and the model\'s gain is reliable only at 1 month.'
    )
    ph = views.posthoc_table(POSTHOC)
    st.dataframe(
        ph.rename(
            columns={
                "horizon": "Months ahead",
                "decline_share": "A guess",
                "registered_simple_rule": "Simple rule as registered",
                "corrected_simple_rule": "Same rule, corrected",
                "model": "The alert",
                "gain_over_corrected": "Gain over corrected rule",
                "reliable": "Reliable",
            }
        ),
        hide_index=True,
    )

# ------------------------------------------------------------------ data status
with tabs[5]:
    c = st.columns(4)
    c[0].metric("Last publish run", STATUS["run_status"])
    c[1].metric("Months of data", STATUS["months"])
    c[2].metric(
        "Latest month",
        views.month_label(STATUS["last_month_id"]) if STATUS["last_month_id"] else "n/a",
    )
    c[3].metric(
        "Data freshness",
        (STATUS["freshness"] or "n/a").title(),
        help=f"Stale if the latest month is more than {STATUS['max_age_days']} days old",
    )
    st.write(f"**Earlier forecasts:** {STATUS['drift_text']}")
    st.write(
        "**Versions of this run:** "
        + ", ".join(f"{k} `{v}`" for k, v in STATUS["versions"].items())
    )
    st.subheader("Stages of the last run")
    st.dataframe(views.stages_frame(D["run"]), hide_index=True)
    st.subheader("Input extracts")
    st.dataframe(views.extracts_frame(D["run"]), hide_index=True)

# ------------------------------------------------------------------ findings
with tabs[6]:
    st.write("What the analyses found, with the evidence and what it means for decisions.")
    for _, row in views.findings_frame(FINDINGS).iterrows():
        with st.expander(f"{row['id']}. {row['finding']}"):
            st.write(f"**Evidence:** {row['evidence']}")
            st.write(f"**Business impact:** {row['impact']}")
            st.caption(row["source"])

# ------------------------------------------------------------------ ask claude
with tabs[7]:
    st.write(
        "Ask a question about the market trend, the segments, the forecasts or how the models "
        "scored. The answer comes from the same tables as the other tabs."
    )
    ask_box()
