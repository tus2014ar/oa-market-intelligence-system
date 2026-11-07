"""The Forecasts page's "Run the model live" panel (DL-122): pick a segment and the website asks
the model service on AWS to run the saved model now, then shows whether the answer equals the
forecast already on the page. A thin page over `serving.live_client` (the only code that talks to
the service) and `serving.livepanel` (the choices, the comparison and the wording).

The address and key of the service are the host's secrets (`LIVE_API_URL`, `LIVE_API_KEY`), read on
the server: a visitor's browser never receives either. If they are not set, or the service does not
answer, the panel says so and the rest of the page is unchanged.
"""

from __future__ import annotations

import uuid

import common
import streamlit as st

from oa_market_intelligence.serving import forecasts, guard, live_client, livepanel, style, trend

PER_VISITOR_PER_HOUR = 20  # live runs a visitor may start in an hour
SITE_PER_HOUR = 200  # and all visitors together
ANSWER_SECONDS = 600  # an answer for one segment and horizon is reused for ten minutes
HEALTH_SECONDS = 300


def _client() -> live_client.LiveClient | None:
    return live_client.LiveClient.from_settings(
        common.secret("LIVE_API_URL"), common.secret("LIVE_API_KEY")
    )


@st.cache_resource
def _limits() -> dict:
    """Shared by all visitors, so one person cannot use up the service for everyone."""
    return {
        "visitor": guard.RateLimiter(max_per_window=PER_VISITOR_PER_HOUR, window_seconds=3600),
        "site": guard.RateLimiter(max_per_window=SITE_PER_HOUR, window_seconds=3600),
    }


@st.cache_data(ttl=ANSWER_SECONDS, show_spinner=False)
def _forecast(specialty: str, age_band: str, gender: str, horizon: int) -> dict:
    """The service's answer (an error is raised, and so not kept)."""
    client = _client()
    if client is None:
        raise live_client.LiveUnavailable("error")
    return client.forecast(specialty, age_band, gender, horizon)


@st.cache_data(ttl=HEALTH_SECONDS, show_spinner=False)
def _health() -> dict:
    client = _client()
    if client is None:
        raise live_client.LiveUnavailable("error")
    return client.health()


def _live_version() -> str | None:
    try:
        return str(_health().get("data_version") or "") or None
    except live_client.LiveUnavailable:
        return None


def _show(outcome: dict, pred, horizon: int, stored: float | None) -> None:
    if "error" in outcome:
        st.warning(livepanel.unavailable(outcome["error"]))
        return
    live = outcome["live"]
    st.markdown(
        style.stand_rows(livepanel.result_rows(live, forecasts.horizon_label(horizon))),
        unsafe_allow_html=True,
    )
    versions = livepanel.data_versions(livepanel.local_data_version(pred), outcome["live_version"])
    level, text = livepanel.verdict(livepanel.compare(live["forecast_pct"], stored), versions)
    st.markdown(style.callout(text, "up" if level == "ok" else "down"), unsafe_allow_html=True)
    own = live.get("matches_stored")
    st.caption(
        "Data version on the website: "
        f"{livepanel.local_data_version(pred)}. On the live service: "
        f"{outcome['live_version'] or 'not reported'}."
        + (
            ""
            if own is None
            else " The service also checked it against its own stored forecast: "
            + ("same." if own else "different.")
        )
    )
    st.caption(livepanel.BIAS_NOTE)


def render(view, pred, horizon: int) -> None:
    """Draw the panel. `view` is every segment's forecast at the page's horizon (not filtered by
    the shared bar), `pred` the stored predictions."""
    st.markdown("##### Run the model live")
    with st.container(border=True, key="fc_live"):
        if _client() is None:
            st.caption(livepanel.SWITCHED_OFF)
            return
        specialties = livepanel.specialties(view)
        if not specialties:
            st.write("No segment has a forecast at this horizon.")
            return
        st.caption(livepanel.LEAD)
        cols = st.columns(3)
        specialty = cols[0].selectbox(
            "Specialty", specialties, format_func=trend.pretty, key="live_specialty"
        )
        age = cols[1].selectbox(
            "Age band",
            livepanel.age_bands(view, specialty),
            format_func=trend.pretty,
            key="live_age",
        )
        gender = cols[2].selectbox(
            "Gender",
            livepanel.genders(view, specialty, age),
            format_func=trend.pretty,
            key="live_gender",
        )
        choice = (specialty, age, gender, horizon)
        if st.button("Run the model", key="live_run", icon=":material/play_arrow:"):
            visitor = st.session_state.setdefault("live_visitor", uuid.uuid4().hex)
            limits = _limits()
            if not (limits["visitor"].allow(visitor) and limits["site"].allow("all")):
                st.session_state["live_outcome"] = {
                    "choice": choice,
                    "error": "Too many live runs in the last hour.",
                }
            else:
                try:
                    with st.spinner("Asking the model service..."):
                        live = _forecast(*choice)
                        version = _live_version()
                    st.session_state["live_outcome"] = {
                        "choice": choice,
                        "live": live,
                        "live_version": version,
                    }
                except live_client.LiveUnavailable as error:
                    st.session_state["live_outcome"] = {"choice": choice, "error": error.message}
        outcome = st.session_state.get("live_outcome")
        if outcome and outcome["choice"] == choice:  # only while the choice is the one it answered
            _show(outcome, pred, horizon, livepanel.stored_forecast(view, *choice[:3]))
