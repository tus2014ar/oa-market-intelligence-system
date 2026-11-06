"""The Findings tab, as tested functions: the findings grouped by theme, the four to read first and
the CSV (DL-101).

The findings themselves are `data/reference/findings.json` (equal to the README table, tested
elsewhere); this module only arranges them, and changes no wording. Each of the 17 findings belongs
to exactly one theme (tested), so none can be dropped from the page by a grouping mistake.
"""

from __future__ import annotations

import pandas as pd

ALL = "All"
THEMES = {
    "The gap": {
        "title": "The gap between sales and visits",
        "blurb": "What the company's sales and IQVIA's visits show, and where the fall sits.",
        "ids": (1, 2, 3),
    },
    "Outside checks": {
        "title": "Checked against outside data",
        "blurb": "Medicare billing and public lists set beside the IQVIA visits.",
        "ids": (4, 5, 6),
    },
    "Possible causes": {
        "title": "Things that might explain it",
        "blurb": "Promotion, status, price and the 2020 pattern, tested one at a time.",
        "ids": (7, 8, 10, 11),
    },
    "Data limits": {
        "title": "What limits the data",
        "blurb": "A capture dip, thin segments and the age of the last extract.",
        "ids": (9, 12, 17),
    },
    "The models": {
        "title": "What the models showed",
        "blurb": "Which inputs and which models helped, and how far to trust the forecasts.",
        "ids": (13, 14, 15, 16),
    },
}
START_HERE = (1, 2, 14, 16)  # the four a brand manager should read first
ROUND2_NOTE = (
    "These findings are from the first modelling round. A second round, tried after seeing it, "
    "found a provisional fix for the segment forecast's upward bias; it is not adopted and is "
    "shown on the Model results page."
)


def theme_of(finding_id: int) -> str | None:
    """The name of the theme a finding belongs to."""
    for name, theme in THEMES.items():
        if finding_id in theme["ids"]:
            return name
    return None


def themed(items: list[dict], theme: str = ALL) -> list[tuple[str, list[dict]]]:
    """The findings by theme, in the order of THEMES and, within a theme, of the ids listed there.
    `theme` keeps one theme ("All" keeps every one). A finding with no theme is not lost: it goes
    in a last group called "Other"."""
    by_id = {int(x["id"]): x for x in items}
    out = []
    for name, spec in THEMES.items():
        if theme in (ALL, name):
            out.append((name, [by_id[i] for i in spec["ids"] if i in by_id]))
    leftover = [x for i, x in by_id.items() if theme_of(i) is None]
    if leftover and theme in (ALL, "Other"):
        out.append(("Other", leftover))
    return out


def start_here(items: list[dict]) -> list[dict]:
    """The findings to read first, in the order of START_HERE."""
    by_id = {int(x["id"]): x for x in items}
    return [by_id[i] for i in START_HERE if i in by_id]


def counts(items: list[dict]) -> dict[str, int]:
    """How many findings each source has (the analyses, the models, monitoring)."""
    return pd.Series([x["source"] for x in items]).value_counts().to_dict()


def csv(items: list[dict]) -> bytes:
    """The findings as a CSV, with their theme."""
    frame = pd.DataFrame(items)[["id", "finding", "evidence", "impact", "source"]]
    frame.insert(
        1, "theme", [THEMES.get(theme_of(int(i)), {}).get("title", "") for i in frame["id"]]
    )
    return frame.to_csv(index=False).encode("utf-8")
