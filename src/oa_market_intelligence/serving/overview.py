"""The Overview and Data status tabs, as tested functions: the tab names, what each tab answers and
the cards of the last run (DL-103).

The tab row of the page is built from `TAB_NAMES`, so the guide on the Overview and the tabs cannot
drift apart (tested). Everything is read from the stored run and the stored tables.
"""

from __future__ import annotations

from oa_market_intelligence.serving import views

PAGES = (
    ("Overview", "views/overview.py", ":material/home:", "overview"),
    ("Market trend", "views/market_trend.py", ":material/show_chart:", "market-trend"),
    ("Segments", "views/segments.py", ":material/pie_chart:", "segments"),
    ("Segment detail", "views/segment_detail.py", ":material/zoom_in:", "segment-detail"),
    ("Forecasts", "views/forecasts.py", ":material/query_stats:", "forecasts"),
    ("Model results", "views/model_results.py", ":material/analytics:", "model-results"),
    ("Data status", "views/data_status.py", ":material/fact_check:", "data-status"),
    ("Findings", "views/findings.py", ":material/lightbulb:", "findings"),
    ("Context", "views/context.py", ":material/public:", "context"),
    ("Market map", "views/market_map.py", ":material/hub:", "market-map"),
    ("Ask Claude", "views/ask_claude.py", ":material/chat:", "ask-claude"),
)  # (title, script, icon, address): the navigation is built from this list
TAB_NAMES = tuple(title for title, *_ in PAGES)
TAB_GUIDE = (
    (
        "Market trend",
        "How has Zilretta's share of visits moved, for any specialty, age band or gender?",
    ),
    ("Segments", "Which groups use it most, and where is the share rising or falling?"),
    (
        "Segment detail",
        "What does one group look like on its own, against the market and its neighbours?",
    ),
    (
        "Forecasts",
        "What is the forecast, how far can it be trusted, and which segments are flagged?",
    ),
    (
        "Model results",
        "Which models were tested, which beat a simple rule, and what did round 2 find?",
    ),
    ("Data status", "How old is the data, and what ran last?"),
    ("Findings", "What did the analyses find, and what does each mean for a decision?"),
    ("Context", "How do company sales, Medicare billing and events compare with the visits?"),
    ("Market map", "How do the products, makers, specialties and events connect?"),
    ("Ask Claude", "Ask a question in plain words (off until a key is configured for the site)."),
)


def guide() -> list[tuple[str, str]]:
    """What each page answers, in the order of the navigation (Overview itself is the guide)."""
    return list(TAB_GUIDE)


def run_cards(status: dict) -> list[tuple[str, str, str]]:
    """Four (label, value, caption) cards for the last publish run: whether it worked, how many
    months of data, the latest month and how fresh the data are."""
    finished = (status.get("finished_at") or "")[:10]
    last = status.get("last_month_id")
    age, limit = status.get("age_days"), status.get("max_age_days")
    freshness = (status.get("freshness") or "n/a").title()
    if age is not None and limit is not None:
        fresh_caption = f"{age} days since the latest month; stale after {limit}."
    else:
        fresh_caption = "No freshness check on record."
    return [
        (
            "Last publish run",
            str(status.get("run_status", "unknown")).title(),
            f"Finished {finished}." if finished else "No run on record.",
        ),
        (
            "Months of data",
            "n/a" if status.get("months") is None else str(status["months"]),
            "Monthly rows in the published warehouse.",
        ),
        (
            "Latest month",
            views.month_label(last) if last else "n/a",
            "The last month with data from IQVIA.",
        ),
        ("Data freshness", freshness, fresh_caption),
    ]


def version_rows(status: dict) -> list[tuple[str, str]]:
    """The data, code and environment versions of the run, as (name, id) rows."""
    return [(str(k).capitalize(), str(v)) for k, v in (status.get("versions") or {}).items()]
