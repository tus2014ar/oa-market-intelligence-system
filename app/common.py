"""What every page of the site shares (DL-104): the loaded data, the data-age note and the one
filter bar that follows you from page to page.

The data are loaded once (read-only) and shared by every visitor. The filters live in
`st.session_state["flt"]`, a plain dict that survives moving between pages (a widget's own state
does not, because Streamlit drops a widget's state on a page that does not draw it); each page
reads them with `filters()`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import streamlit as st  # noqa: E402

from oa_market_intelligence import knowledge_graph  # noqa: E402
from oa_market_intelligence.serving import (  # noqa: E402
    data,
    links,
    overview,
    qa_tools,
    trend,
    views,
)

ALL = "All"
DEFAULTS = {"specialty": None, "age": None, "gender": None, "period": "All"}
PERIODS = ["1Y", "3Y", "5Y", "All"]
# which filters each page uses; a page not listed here shows no filter bar
FILTERED_PAGES = {
    "Market trend": ("specialty", "age", "gender", "period"),
    "Segments": ("specialty", "age", "gender", "period"),
    "Segment detail": ("specialty", "age", "gender", "period"),
    "Forecasts": ("specialty", "age", "gender"),
}
NAMES = {"specialty": "specialty", "age": "age band", "gender": "gender", "period": "period"}


@st.cache_resource(show_spinner="Loading the data...")
def context() -> SimpleNamespace:
    """Every table and stored result the pages draw, loaded once and read-only."""
    db = Path(os.environ.get("OA_DB_PATH", data.DB_PATH))
    run_log = Path(os.environ.get("OA_RUN_LOG", data.RUN_LOG))
    external_db = Path(os.environ.get("OA_EXTERNAL_DB_PATH", data.EXTERNAL_DB_PATH))
    engine = data.open_engine(db)
    try:
        D = {
            "monthly": data.market_monthly(engine),
            "seg": data.segment_monthly(engine),
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
    external = data.open_engine(external_db)
    try:
        EXTERNAL = data.external_context(external)
    finally:
        external.dispose()
    adoption, posthoc, findings = data.serving_map(), data.posthoc(), data.findings()
    snapshot = qa_tools.Snapshot(
        monthly=D["monthly"],
        pred=D["pred"],
        scores=D["scores"],
        by_specialty=D["by_specialty"],
        by_age=D["by_age"],
        by_gender=D["by_gender"],
        run=D["run"],
        adoption=adoption,
        posthoc=posthoc,
        findings=findings,
        graph=knowledge_graph.Graph.load(),
    )
    return SimpleNamespace(
        D=D,
        EXTERNAL=EXTERNAL,
        ADOPTION=adoption,
        POSTHOC=posthoc,
        FINDINGS=findings,
        ROUND2={
            "round2": data.round2(),
            "round2_posthoc": data.round2_posthoc(),
            "shap": data.shap_summary(),
        },
        STATUS=views.status_block(D["run"]),
        HEAD=views.headline(D["monthly"], D["pred"]),
        SNAPSHOT=snapshot,
    )


def data_chip() -> None:
    """A one-line note at the top of every page but the Overview: how old the data are."""
    status = context().STATUS
    if not status["last_month_id"]:
        return
    stale = status["freshness"] == "stale"
    text = f"Data to {views.month_label(status['last_month_id'])}"
    if stale:
        text += f" · {status['age_days']} days old, no newer extract"
    st.markdown(f":{'orange' if stale else 'green'}-badge[{text}]")


def dark_page() -> bool:
    """True when the page is showing the dark theme (the map is drawn to match)."""
    try:
        return st.context.theme.type == "dark"
    except Exception:  # noqa: BLE001 - older Streamlit or no theme information
        return False


# ------------------------------------------------------------------ the shared filters


def filters() -> dict:
    """The filters now chosen: `specialty`, `age` and `gender` (None means all) and `period`."""
    return {**DEFAULTS, **st.session_state.get("flt", {})}


def _reset() -> None:
    """Back to all specialties, ages and genders and the whole period (a button's callback)."""
    st.session_state["flt"] = dict(DEFAULTS)
    for key, value in (
        ("w_specialty", ALL),
        ("w_age", ALL),
        ("w_gender", ALL),
        ("w_period", "All"),
    ):
        st.session_state[key] = value


def filter_bar(page: str) -> None:
    """The one filter bar, drawn above a page that uses it. A filter a page does not use is greyed
    out (it keeps its value), and a line says which ones this page follows."""
    uses = FILTERED_PAGES[page]
    seg = context().D["seg"]
    saved = st.session_state.setdefault("flt", dict(DEFAULTS))
    for key, value in (
        ("w_specialty", saved["specialty"] or ALL),
        ("w_age", saved["age"] or ALL),
        ("w_gender", saved["gender"] or ALL),
        ("w_period", saved["period"]),
    ):
        st.session_state.setdefault(key, value)
    with st.container(border=True, key="filter_bar"):
        cols = st.columns([2.3, 1.4, 1.3, 2.0, 0.9])
        by_visits = seg.groupby("specialty")["category"].sum().sort_values(ascending=False)
        specialty = cols[0].selectbox(
            "Specialty",
            [ALL, *by_visits.index],
            format_func=lambda v: "All specialties" if v == ALL else trend.pretty(v),
            key="w_specialty",
            disabled="specialty" not in uses,
        )
        age = cols[1].selectbox(
            "Age band",
            [ALL, *sorted(seg["age_band"].unique())],
            format_func=lambda v: "All ages" if v == ALL else trend.pretty(v),
            key="w_age",
            disabled="age" not in uses,
        )
        gender = cols[2].selectbox(
            "Gender",
            [ALL, *sorted(seg["gender"].unique())],
            format_func=lambda v: "All genders" if v == ALL else trend.pretty(v),
            key="w_gender",
            disabled="gender" not in uses,
        )
        period = cols[3].segmented_control(
            "Period",
            PERIODS,
            key="w_period",
            disabled="period" not in uses,
        )
        cols[4].markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
        cols[4].button("Reset", key="reset_filters", on_click=_reset, width="stretch")
        chosen = [NAMES[k] for k in uses]
        unused = [NAMES[k] for k in DEFAULTS if k not in uses]
        note = f"This page follows the {', '.join(chosen[:-1])} and {chosen[-1]} filters."
        if unused:
            note += f" The {' and '.join(unused)} {'filter does' if len(unused) == 1 else 'filters do'} not apply here."
        st.caption(note)
    saved.update(
        specialty=None if specialty == ALL else specialty,
        age=None if age == ALL else age,
        gender=None if gender == ALL else gender,
        period=period or "All",
    )


# ------------------------------------------------------------------ addresses and back links

SCRIPTS = {title: script for title, script, _, _ in overview.PAGES}


def init_from_url(page_title: str) -> None:
    """On the first run of a session, read the filters and the page the click came from out of the
    address (a link carries what was clicked). A value the data does not have is dropped, so a bad
    or edited address opens the page unfiltered. Later runs keep the session's own filters."""
    if "flt" in st.session_state:
        return
    seg = context().D["seg"]
    valid = {
        "specialty": set(seg["specialty"].unique()),
        "age": set(seg["age_band"].unique()),
        "gender": set(seg["gender"].unique()),
    }
    params = {key: st.query_params.get(key) for key in (*DEFAULTS, "from")}
    filters, source = links.parse(params, valid)
    st.session_state["flt"] = filters
    if source:
        st.session_state["from"] = (source, page_title)  # came from `source`, to this page


def sync_url(page_title: str) -> None:
    """Write the filters now chosen (and where you came from) into the address, so the page can be
    shared or reloaded as it is."""
    came = st.session_state.get("from")
    source = came[0] if came and came[1] == page_title else None
    st.query_params.from_dict(links.to_params(filters(), source))


def go(target: str, source: str, **chosen) -> None:
    """Move to another page inside the app (from a button or a chart click) with the filters set,
    remembering the page it came from for the back link."""
    st.session_state["flt"] = {**DEFAULTS, **{k: v for k, v in chosen.items() if v}}
    st.session_state["from"] = (source, target)
    for key, saved in (("w_specialty", "specialty"), ("w_age", "age"), ("w_gender", "gender")):
        st.session_state[key] = st.session_state["flt"][saved] or ALL
    st.session_state["w_period"] = st.session_state["flt"]["period"]
    st.switch_page(SCRIPTS[target])


def back_link(page_title: str) -> None:
    """A "back to ..." link, shown only on the page a link brought you to."""
    came = st.session_state.get("from")
    if came and came[1] == page_title and came[0] in SCRIPTS and came[0] != page_title:
        st.page_link(SCRIPTS[came[0]], label=f"Back to {came[0]}", icon=":material/arrow_back:")


def detail_href(column: str, group: str | None, flt: dict, source: str) -> str | None:
    """The address of the Segment detail page for a group (a specialty, an age band or a gender, by
    its raw key), keeping the filters now chosen for the other two and the period. None for no group."""
    if group is None:
        return None
    chosen = {"specialty": flt.get("specialty"), "age": flt.get("age"), "gender": flt.get("gender")}
    chosen[{"specialty": "specialty", "age_band": "age", "gender": "gender"}[column]] = group
    return links.href("Segment detail", period=flt.get("period"), source=source, **chosen)
