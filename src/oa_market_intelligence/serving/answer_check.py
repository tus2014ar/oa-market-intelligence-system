"""The number check: which figures in an answer were not in the data Claude looked up.

Claude can misquote or invent a number. After each answer the page lists any figure that appears
in the answer but nowhere in the tool results (or in the question), so a reader knows which to
check. A figure that Claude calculated itself (a difference, a ratio) will also be listed: the
wording says "not found in the data it looked up", not "wrong".

Ignored: years, month and day numbers, small whole numbers up to 31 (horizons, list positions,
"3 months"), and anything inside an id such as event:11.
"""

from __future__ import annotations

import re
from typing import Any

NUMBER = re.compile(r"(?<![\w.:])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
YEAR_RANGE = range(1900, 2101)
SMALL_INT = 31
DESIGN_CONSTANTS = (90.0, 100.0)  # the 90% range and "100%": named in the site's own wording


def _numbers(text: str) -> list[tuple[str, float, int]]:
    """(the text as written, its value, its decimals) for every figure worth checking."""
    found = []
    for match in NUMBER.finditer(text):
        raw = match.group(0)
        clean = raw.rstrip("%").replace(",", "")
        try:
            value = float(clean)
        except ValueError:
            continue
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        whole = decimals == 0 and "," not in raw
        if whole and (abs(value) <= SMALL_INT or int(abs(value)) in YEAR_RANGE):
            continue
        if whole and abs(value) in DESIGN_CONSTANTS:
            continue
        found.append((raw, value, decimals))
    return found


def _walk(value: Any, out: list[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, dict):
        for inner in value.values():
            _walk(inner, out)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _walk(inner, out)
    elif isinstance(value, str):
        out.extend(v for _, v, _ in _numbers(value))


def _matches(value: float, decimals: int, known: list[float]) -> bool:
    shown = round(abs(value), decimals)
    for candidate in known:
        # the figure as shown, as a percent of a fraction, or as a fraction of a percent; the sign
        # is ignored because "44% below the peak" and -44 are the same fact. A figure that rounds
        # to zero only matches an exact zero: scaling a small value down would match it by chance.
        scales = (candidate,) if shown == 0 else (candidate, 100 * candidate, candidate / 100)
        if any(round(abs(scaled), decimals) == shown for scaled in scales):
            return True
    return False


def unchecked_numbers(answer: str, outputs: list[Any], question: str = "") -> list[str]:
    """The figures in `answer` that no tool result (or the question) contains, as written."""
    known: list[float] = []
    for output in outputs:
        _walk(output, known)
    known.extend(v for _, v, _ in _numbers(question))
    missing = []
    for raw, value, decimals in _numbers(answer):
        if not _matches(value, decimals, known) and raw not in missing:
            missing.append(raw)
    return missing
