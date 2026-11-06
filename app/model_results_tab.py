"""The Model results tab (DL-100): a headline card for each task and for round 2, the scoreboard,
the segment bias, the decline-alert caveat, the round 2 tests in plain words and what the adopted
segment model relies on. A thin page over `serving.results`, `serving.style` and `serving.views`,
which hold (and test) the logic and the wording.
"""

from __future__ import annotations

import common
import streamlit as st

from oa_market_intelligence.serving import links, results, style, views

SHAP_HORIZONS = {1: "1 month", 3: "3 months"}
FROM = "Model results"
# each headline card goes to where its subject is shown
CARD_LINKS = {
    "National share": lambda: links.href("Forecasts", source=FROM),
    "Segment share": lambda: links.href("Forecasts", source=FROM, section="Segment forecasts"),
    "Decline alert": lambda: links.href(
        "Forecasts", source=FROM, section="Decline alert, 1 month ahead"
    ),
    "Round 2": lambda: "#" + links.anchor_id("Round 2: improvements tried after round 1"),
}


def _scoreboard(board) -> None:
    with st.container(border=True):
        task = st.segmented_control(
            "Task",
            list(results.TASKS),
            default="All",
            key="mr_task",
            label_visibility="collapsed",
        )
        shown = results.for_task(board, task or "All")
        order = [
            "task",
            "horizon",
            "verdict",
            "adopted_model",
            "measure",
            "simple_rule",
            "simple_rule_score",
            "chosen_model",
            "chosen_model_score",
        ]
        st.dataframe(
            shown[order].rename(
                columns={
                    "task": "Task",
                    "horizon": "Months ahead",
                    "verdict": "In plain words",
                    "adopted_model": "Used by the site",
                    "measure": "Measure",
                    "simple_rule": "Simple rule",
                    "simple_rule_score": "Simple rule score",
                    "chosen_model": "Model chosen by the rule",
                    "chosen_model_score": "Its score",
                }
            ),
            hide_index=True,
            column_config={
                "Simple rule score": st.column_config.NumberColumn(format="%.4f"),
                "Its score": st.column_config.NumberColumn(format="%.4f"),
            },
        )
        st.caption(
            "Each row compares the models with a simple rule under tests fixed before anything "
            'ran. "Used by the site" is what the project adopted (decision DL-79). Lower is '
            "better for the first two measures; higher is better for the ranking score."
        )


def _bias(bias) -> None:
    with st.container(border=True):
        st.markdown("**Do the segment forecasts run high?**")
        rows = [
            (
                f"{int(r.horizon)} month{'s' if r.horizon != 1 else ''}",
                float(r.forecast_above_actual_pct),
                f"+{r.forecast_above_actual_pct:.1f}%",
            )
            for r in bias.itertuples()
        ]
        st.markdown(style.bar_rows(rows, scale_to=20.0), unsafe_allow_html=True)
        st.caption(
            "How far the segment model's forecasts sat above what happened, in testing. At 6 "
            "months the site serves the simple rule instead, so this bias does not apply there."
        )


def _alert(posthoc_table) -> None:
    st.markdown("**The caveat on the decline alert**")
    st.write(
        "The alert passed its registered test, but one of the simple rules it was tested "
        "against was written the wrong way round. With only that corrected (a check made after "
        'the results were seen), a one-line rule "this segment just spiked" does almost as '
        "well as the model, and the model's gain is reliable only at 1 month."
    )
    cols = st.columns(3)
    for col, (label, registered, corrected, model, reliable) in zip(
        cols, results.alert_cards(posthoc_table), strict=False
    ):
        with col.container(border=True, height="stretch"):
            st.markdown(f"**{label}**")
            st.markdown(
                style.bar_rows(
                    [
                        ("Rule as registered", registered, f"{registered:.2f}"),
                        ("Rule, corrected", corrected, f"{corrected:.2f}"),
                        ("The alert", model, f"{model:.2f}"),
                    ],
                    scale_to=0.6,
                ),
                unsafe_allow_html=True,
            )
            st.markdown(
                style.callout(
                    "A reliable gain over the corrected rule"
                    if reliable
                    else "Not reliably better than the corrected rule",
                    "up" if reliable else "down",
                ),
                unsafe_allow_html=True,
            )
    low, high = posthoc_table["decline_share"].min(), posthoc_table["decline_share"].max()
    st.caption(
        "Ranking score (PR-AUC), higher is better. A rule that guessed would score about "
        f"{low:.2f} to {high:.2f}, the share of segments that truly fall."
    )


def _round2(round2, round2_posthoc) -> None:
    summary = results.round2_summary(round2)
    st.markdown(style.callout(results.PROVISIONAL, "down"), unsafe_allow_html=True)
    rows = results.round2_rows(round2, round2_posthoc)
    with st.container(border=True):
        st.markdown(
            style.test_rows(
                [
                    (
                        r["name"],
                        f"{r['test']} · {r['horizon_label']}",
                        "Passes (provisional)" if r["passes"] else "No pass",
                        r["passes"],
                        r["result"],
                        r["plain"],
                    )
                    for r in rows
                ]
            ),
            unsafe_allow_html=True,
        )
        st.caption(
            f"{summary['passed']} of {summary['total']} tests passed ({', '.join(summary['which'])}). "
            "All seven used one strict bar (a 0.71th percentile, because seven were counted), and "
            "the plan was written before any of them ran. The details are in "
            "docs/modelling_round2_results.md."
        )


@st.fragment
def _shap(shap) -> None:
    # the months-ahead switch moves only this part of the page
    with st.container(border=True):
        horizon = st.segmented_control(
            "Months ahead",
            list(SHAP_HORIZONS),
            default=1,
            format_func=SHAP_HORIZONS.get,
            key="mr_shap_h",
            label_visibility="collapsed",
        )
        info = results.shap_rows(shap, horizon or 1)
        top = info["groups"][0][1]
        st.markdown(
            style.bar_rows(
                [(name, pct, f"{pct:.0f}%") for name, pct in info["groups"]],
                scale_to=max(top, 1.0),
            ),
            unsafe_allow_html=True,
        )
        ex = info["example"]
        st.caption(
            f"Each kind of information's share of how much it moves the forecasts across the "
            f"{info['segments']} segments (a typical segment's forecast is about "
            f"{info['typical_pct']:.2f}%). Example: {ex['name']} ({ex['visits']:,.0f} visits last "
            f"month) was at {ex['last_pct']:.2f}% and is forecast at {ex['forecast_pct']:.2f}%."
        )
        st.caption(
            "This explains the model; it does not say why a segment's share moves, and it did "
            "not influence which model was chosen."
        )


def render(D: dict, adoption: dict, posthoc: dict, extras: dict, data_chip) -> None:
    """Draw the tab. `extras` holds `round2`, `round2_posthoc` and `shap` (stored results)."""
    data_chip()
    scores = D["scores"]
    board = views.scoreboard(scores, adoption, posthoc)
    bias = views.bias_table(scores)
    alert = views.posthoc_table(posthoc)
    round2, round2_posthoc, shap = extras["round2"], extras["round2_posthoc"], extras["shap"]

    cards = st.columns(4)
    for col, (label, value, caption) in zip(
        cards, results.headline_cards(board, bias, alert, round2), strict=True
    ):
        col.markdown(
            style.kpi_card(label, value, caption, href=CARD_LINKS[label]()), unsafe_allow_html=True
        )

    st.markdown("##### How the models scored")
    _scoreboard(board)
    common.explain("mr_scoreboard")
    _bias(bias)
    _alert(alert)

    st.markdown("##### Round 2: improvements tried after round 1")
    _round2(round2, round2_posthoc)
    common.explain("mr_round2")

    st.markdown("##### What the segment model relies on")
    _shap(shap)
    common.explain("mr_shap")
    st.caption(
        "Everything on this page is stored output of the modelling runs, not fitted while the "
        "page loads. Visits are in IQVIA's panel, not company sales."
    )
