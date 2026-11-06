"""The Ask Claude page (DL-88, moved here in DL-104): a conversation that answers from read-only
tools. It works only where an API key is configured, and then behind an optional access code, a
per-visitor limit and a daily token budget. Nothing about its behaviour changed when the site
moved to separate pages; it is still off without a key and still tested only with a scripted client.
"""

from __future__ import annotations

import os
import uuid

import streamlit as st

from oa_market_intelligence.serving import charts, guard, qa

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


def _show_answer(message: dict, snapshot) -> None:
    """One assistant turn: the answer, any chart it asked for, its sources and its caveats."""
    st.write(message["content"])
    for spec in message.get("charts", []):
        st.altair_chart(charts.build(spec["chart"], spec["params"], snapshot), width="stretch")
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


def ask_box(snapshot) -> None:
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
        "check the numbers against the pages."
    )
    pending = st.session_state.pop("pending", None)
    if not chat and not pending:
        for col, (audience, questions) in zip(
            st.columns(len(EXAMPLE_QUESTIONS)), EXAMPLE_QUESTIONS.items(), strict=True
        ):
            with col.container(border=True, height="stretch"):
                st.write(f"**{audience}**")
                for question in questions:
                    if st.button(question, key=f"ex-{question}", width="stretch"):
                        st.session_state["pending"] = question
                        st.rerun()
    for message in chat:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                _show_answer(message, snapshot)
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
            snapshot=snapshot,
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


def render(snapshot, data_chip) -> None:
    """Draw the page: a short introduction, then the question box."""
    data_chip()
    with st.container(border=True):
        st.write(
            "Ask a question about the market trend, the segments, the forecasts or how the models "
            "scored. The answer comes from the same tables as the other pages."
        )
    ask_box(snapshot)
