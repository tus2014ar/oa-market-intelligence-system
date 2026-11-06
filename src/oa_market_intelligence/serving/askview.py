"""The AI Question Box page, as tested functions and wording (DL-109): the example questions,
what the box would look up for each, the eleven tools in plain words, the page behind each
source, and the plain account of what it can see and what has been tested.

Nothing here calls a model. `lookup_preview` runs the same read-only tools the box gives Claude, so
a person can see the data a question would be answered from even where no key is configured; it
never writes an answer, because only a model can.
"""

from __future__ import annotations

from oa_market_intelligence.serving import overview, qa_tools

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

# the lookups each example question needs: (tool, arguments), the same calls Claude would make
LOOKUPS: dict[str, list[tuple[str, dict]]] = {
    "How has Zilretta's share changed since its peak?": [("get_data_status", {})],
    "What is the forecast for the next three months, and how far can I trust it?": [
        ("get_forecasts", {"view": "national"}),
        ("get_model_results", {}),
    ],
    "Who are Zilretta's competitors in this project?": [("get_competitors", {})],
    "Show me the share trend as a chart.": [("show_chart", {"chart": "trend"})],
    "Which model is used for the national forecast, and did any model beat the simple rule?": [
        ("get_model_results", {})
    ],
    "Do the segment forecasts run high?": [("get_forecasts", {"view": "specialty", "top": 5})],
    "Which segments does the decline alert flag, and how reliable is it?": [
        ("get_forecasts", {"view": "alerts", "top": 5}),
        ("get_model_results", {}),
    ],
    "Where do Zilretta's visits come from?": [("get_segments", {"by": "specialty", "top": 5})],
}

# each tool in plain words, and the page that shows the same thing (None: it draws, not reads)
TOOL_PLAIN: dict[str, tuple[str, str | None]] = {
    "get_data_status": ("The headline numbers, how old the data are and the limits", "Overview"),
    "get_market_trend": ("Zilretta's share of visits by month or year", "Market trend"),
    "get_segments": ("The share by specialty, age band or gender", "Segments"),
    "get_forecasts": ("The forecasts and the decline-alert list", "Forecasts"),
    "get_model_results": ("How each forecast scored, and what to use it for", "Model results"),
    "get_findings": ("The 17 findings with their evidence and impact", "Findings"),
    "get_entity": ("One thing in the knowledge graph (a product, maker, specialty)", "Market map"),
    "get_neighbors": ("What that thing is connected to, with the source", "Market map"),
    "get_competitors": ("How the project groups Zilretta's competition", "Market map"),
    "get_events": ("The event timeline: approvals, payment rules, guidelines", "Market map"),
    "show_chart": ("Draws one of four charts under the answer", None),
}

PER_VISITOR_PER_HOUR = 8  # questions one visitor may ask in an hour
SITE_PER_HOUR = 200  # questions the whole site answers in an hour
DEFAULT_DAILY_TOKENS = 300_000  # the day's budget, unless the owner sets another

HOW_IT_WORKS = (
    "Claude sees only short, read-only summaries of the tables and the knowledge graph on this "
    "site. It cannot see patient-level rows, write a query or look anything up on the internet.",
    "Every answer lists its sources (the pages whose tables it used) and the data it looked up. "
    "A figure in an answer that is not in that data is listed under 'Check these figures'.",
    f"Each visitor can ask {PER_VISITOR_PER_HOUR} questions an hour, and the site has a daily "
    "limit, so one person cannot use up the budget.",
    "It can still be wrong. Check any figure on the page it came from before you quote it.",
    "It is a demonstration. It has been tried with Claude on only a small number of questions, "
    "and the full set of 28 test questions has not been run, so check any figure on the page "
    "it came from before you quote it.",
)


def source_page(source: str) -> str | None:
    """The page behind a source line such as "Market trend page (gold_visit_share_monthly)" or
    "Knowledge graph (...)": its title, or None for one that has no page."""
    for title in overview.TAB_NAMES:
        if source.startswith(f"{title} page"):
            return title
    if source.startswith("Knowledge graph"):
        return "Market map"
    return None


def source_pages(sources: list[str]) -> list[str]:
    """The pages behind a list of sources, each once, in the order the sources came."""
    pages: list[str] = []
    for source in sources:
        page = source_page(source)
        if page and page not in pages:
            pages.append(page)
    return pages


def lookup_preview(question: str, snapshot: qa_tools.Snapshot) -> list[dict]:
    """The data an example question would be answered from: for each lookup its tool, plain name,
    page, arguments and the read-only summary the tool returns. Empty for a question that is not
    one of the examples."""
    out = []
    for tool, arguments in LOOKUPS.get(question, []):
        label, page = TOOL_PLAIN[tool]
        out.append(
            {
                "tool": tool,
                "label": label,
                "page": page,
                "arguments": arguments,
                "output": qa_tools.run_tool(tool, arguments, snapshot=snapshot),
            }
        )
    return out
