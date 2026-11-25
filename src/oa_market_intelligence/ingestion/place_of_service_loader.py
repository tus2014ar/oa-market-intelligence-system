"""Loader for the market-level Place-of-Service-by-month summary sheet.

Every NMTA workbook carries this as a separate flat table on its secondary sheet
(docs/data_dictionary.md §3). It is not tied to any product, so it has its own grain:
one row per (month, disease area, setting).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

SETTINGS = ("HOSPITAL", "OFFICE", "OTHER", "TELEHEALTH")
OUTPUT_COLUMNS = ["month", "disease_area", "place_of_service", "patient_visits"]

# The header row is at row 29 in every extract seen so far, but scan for it rather than
# hard-code that; the cap keeps the scan from reading the 14,000-row pivot sheet.
_HEADER_SCAN_ROWS = 100


class PlaceOfServiceError(ValueError):
    """The workbook's Place-of-Service sheet is missing or not in the expected shape."""


def _find_header(rows: list[tuple]) -> int | None:
    for index, row in enumerate(rows):
        if not row or row[0] != "Month":
            continue
        headers = [h for h in row[1:] if h is not None]
        if headers and all(h in SETTINGS for h in headers):
            return index
    return None


def _disease_area(sheet_name: str) -> str:
    if sheet_name.startswith("M15_19_OA"):
        return "OA"
    if sheet_name.startswith("M04_RA"):
        return "RA"
    raise PlaceOfServiceError(f"Cannot tell OA from RA using sheet name {sheet_name!r}")


def _parse_month(label, row_no: int) -> datetime:
    if isinstance(label, datetime):
        return label
    try:
        return datetime.strptime(str(label).strip(), "%b %Y")
    except ValueError:
        raise PlaceOfServiceError(
            f"Row {row_no}: month label {label!r} is not 'Mon YYYY'. Truncated labels such "
            "as 'Sep...' (the Branded Generic workbooks) carry no year and are not loaded."
        ) from None


_TRUNCATED_LABEL = re.compile(r"^[A-Za-z]{3}\.\.\.$")


def _pivot_months(workbook) -> list[datetime]:
    """The months of the workbook's own pivot sheet (the dates in its first column), in order."""
    for ws in workbook.worksheets:
        first = next(ws.iter_rows(min_row=1, max_row=1, max_col=2, values_only=True), ())
        if (
            len(first) > 1
            and isinstance(first[0], str)
            and first[0].strip() == "Month"
            and isinstance(first[1], str)
            and first[1].endswith("Patient Visits")
        ):
            return [
                row[0]
                for row in ws.iter_rows(min_col=1, max_col=1, values_only=True)
                if isinstance(row[0], datetime)
            ]
    return []


def _months_from_pivot(labels: list[str], pivot_months: list[datetime]) -> list[datetime]:
    """Years for labels such as 'Oct...' (IQVIA's Nov 2026 re-pull writes the month name only).

    A year is never guessed. The labels are accepted only when the same workbook's pivot sheet
    gives the months, as consecutive months, there are exactly as many labels as pivot months,
    and every label's month name matches the pivot month in the same position.
    """
    reason = None
    if not pivot_months:
        reason = "the workbook has no pivot sheet to take the years from"
    elif len(labels) != len(pivot_months):
        reason = f"{len(labels)} labels but the pivot sheet has {len(pivot_months)} months"
    elif any(
        (b.year * 12 + b.month) - (a.year * 12 + a.month) != 1
        for a, b in zip(pivot_months, pivot_months[1:], strict=False)
    ):
        reason = "the pivot sheet's months are not consecutive"
    else:
        for number, (label, month) in enumerate(zip(labels, pivot_months, strict=True), start=1):
            if label[:3].lower() != month.strftime("%b").lower():
                reason = f"label {number} is {label!r} but the pivot month is {month:%b %Y}"
                break
    if reason:
        raise PlaceOfServiceError(
            "Month labels without a year (such as 'Sep...') carry no year and are not loaded "
            "unless the workbook's own pivot sheet gives it: " + reason + "."
        )
    return list(pivot_months)


def parse_place_of_service(path: str | Path) -> pd.DataFrame:
    """Parse a workbook's Place-of-Service sheet into a tidy long-format table.

    Blank cells mean zero visits and are dropped. A setting with no column at all
    (the RA sheet has no HOSPITAL or TELEHEALTH) is simply absent, not an error.
    """
    workbook = load_workbook(Path(path), read_only=True)
    try:
        candidates = []
        for ws in workbook.worksheets:
            rows = list(ws.iter_rows(max_row=_HEADER_SCAN_ROWS, values_only=True))
            header_index = _find_header(rows)
            if header_index is not None:
                candidates.append(ws)
        if len(candidates) != 1:
            raise PlaceOfServiceError(
                f"Expected exactly one Place-of-Service sheet, found {len(candidates)}"
            )
        ws = candidates[0]
        all_rows = list(ws.iter_rows(values_only=True))
        sheet_name = ws.title
        pivot_months = _pivot_months(workbook)
    finally:
        workbook.close()

    disease_area = _disease_area(sheet_name)
    header_index = _find_header(all_rows)
    headers = all_rows[header_index]
    settings = [(col, h) for col, h in enumerate(headers) if col > 0 and h is not None]

    data_rows = [
        (header_index + offset + 2, row)
        for offset, row in enumerate(all_rows[header_index + 1 :])
        if not all(v is None for v in row)
    ]
    labels = [str(row[0]).strip() for _, row in data_rows]
    from_pivot = None
    if labels and all(_TRUNCATED_LABEL.match(label) for label in labels):
        from_pivot = _months_from_pivot(labels, pivot_months)

    records = []
    seen_months = set()
    for position, (row_no, row) in enumerate(data_rows):
        month = from_pivot[position] if from_pivot else _parse_month(row[0], row_no)
        if month in seen_months:
            raise PlaceOfServiceError(f"Row {row_no}: month {month:%Y-%m} appears twice")
        seen_months.add(month)
        for col, setting in settings:
            value = row[col]
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PlaceOfServiceError(
                    f"Row {row_no}: {setting} value {value!r} is not a non-negative integer"
                )
            records.append((month, disease_area, setting, value))

    if not seen_months:
        raise PlaceOfServiceError("Place-of-Service sheet has no data rows")

    frame = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    frame["month"] = pd.to_datetime(frame["month"]).astype("datetime64[ns]")
    return frame
