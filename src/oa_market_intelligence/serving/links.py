"""Links between the site's pages, as tested functions (DL-105): the address of a page with the
filters already set, the filters read back from an address, the "where did you come from" back link
and which page and section shows the evidence for each finding.

A link carries what was clicked in the address, for example
`?specialty=ORTHOPEDIC%20SURGERY&period=3Y&from=segments`, so the page it lands on opens already
narrowed, the address can be shared and the browser's back button works. Addresses are relative
(`segment-detail?...`), so they work wherever the site is hosted.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from oa_market_intelligence.serving import overview

SLUGS = {title: slug for title, _, _, slug in overview.PAGES}
TITLES = {slug: title for title, slug in SLUGS.items()}
PERIODS = ("1Y", "3Y", "5Y", "All")
FILTER_KEYS = ("specialty", "age", "gender", "period")
# the page and section that show the evidence for each finding (None: no page shows it yet)
FINDING_LINKS: dict[int, tuple[str, str | None] | None] = {
    1: ("Context", "Company sales against IQVIA visits"),
    2: ("Context", "What was tested to explain the gap"),
    3: ("Segments", None),
    4: ("Context", "Medicare adoption by state"),
    5: ("Context", "Medicare adoption by state"),
    6: ("Context", "Medicare adoption by state"),
    7: ("Context", "Events, oldest first"),
    8: ("Context", "Events, oldest first"),
    9: ("Market trend", None),
    10: None,
    11: None,
    12: ("Segments", None),
    13: ("Model results", "How the models scored"),
    14: ("Forecasts", None),
    15: ("Model results", "How the models scored"),
    16: ("Forecasts", "Decline alert, 1 month ahead"),
    17: ("Data status", None),
}


def anchor_id(heading: str) -> str:
    """The address fragment Streamlit gives a heading: lower case, punctuation dropped, spaces to
    hyphens ("Decline alert, 1 month ahead" becomes "decline-alert-1-month-ahead")."""
    text = re.sub(r"[^a-z0-9\s-]", "", heading.lower())
    return re.sub(r"\s+", "-", text.strip())


def href(
    title: str,
    *,
    specialty: str | None = None,
    age: str | None = None,
    gender: str | None = None,
    period: str | None = None,
    source: str | None = None,
    section: str | None = None,
) -> str:
    """The relative address of a page by its title, with the chosen filters in the address, the
    page the click came from (`source`, a page title) and a section heading to scroll to."""
    if title not in SLUGS:
        raise ValueError(f"no page called {title!r}")
    pairs = [
        ("specialty", specialty),
        ("age", age),
        ("gender", gender),
        ("period", period if period in PERIODS and period != "All" else None),
        ("from", SLUGS[source] if source else None),
    ]
    query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in pairs if value)
    out = "./" if title == "Overview" else SLUGS[title]
    if query:
        out += "?" + query
    if section:
        out += "#" + anchor_id(section)
    return out


def parse(params: dict, valid: dict[str, set[str]]) -> tuple[dict, str | None]:
    """The filters and the source page read back from an address's parameters. A value that is not
    one the data has is dropped (an address can be typed or edited), so a bad link opens the page
    unfiltered and never fails. `valid` maps "specialty", "age" and "gender" to the allowed
    values."""
    filters = {"specialty": None, "age": None, "gender": None, "period": "All"}
    for key in ("specialty", "age", "gender"):
        value = params.get(key)
        if isinstance(value, str) and value in valid.get(key, set()):
            filters[key] = value
    period = params.get("period")
    if isinstance(period, str) and period in PERIODS:
        filters["period"] = period
    source = params.get("from")
    return filters, TITLES.get(source) if isinstance(source, str) else None


def to_params(filters: dict, source: str | None = None) -> dict[str, str]:
    """The address parameters for the filters now chosen (a filter left at its default is not
    written, so an unfiltered page has a clean address)."""
    out = {key: filters[key] for key in ("specialty", "age", "gender") if filters.get(key)}
    if filters.get("period") and filters["period"] != "All":
        out["period"] = filters["period"]
    if source in SLUGS:
        out["from"] = SLUGS[source]
    return out


def finding_link(finding_id: int, source: str = "Findings") -> tuple[str, str] | None:
    """(label, address) for the page that shows a finding's evidence, or None if no page does."""
    target = FINDING_LINKS.get(int(finding_id))
    if target is None:
        return None
    title, section = target
    return (f"See it on {title}", href(title, source=source, section=section))
