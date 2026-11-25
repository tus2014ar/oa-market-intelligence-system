"""IQVIA's Nov 2026 re-pull of the extracts differs from the earlier files in two ways the
loaders must handle without guessing: the pivot sheet's first header cell reads "Month" followed
by line breaks, and the Place-of-Service sheet writes months as "Oct...", with no year.

Both cases are tested on small corrupted copies of the committed RA workbook, so no new raw file
is needed. A year is taken only from the same workbook's pivot sheet, and only when every check
passes (docs/data_dictionary.md §3).
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook

from oa_market_intelligence.ingestion.nmta_loader import PivotStructureError, parse_pivot_sheet
from oa_market_intelligence.ingestion.place_of_service_loader import (
    PlaceOfServiceError,
    parse_place_of_service,
)

RA_FILE = Path(__file__).resolve().parent.parent / "data" / "raw" / "Team1_M04_RA.xlsx"
PIVOT_SHEET = "M04_RA_PAT_VISIT"
POS_SHEET = "M04_RA_PAT_VISIT1"
HEADER_ROW = 29


@pytest.fixture(scope="module")
def ra_visits():
    return parse_pivot_sheet(RA_FILE)


def _save(tmp_path, workbook):
    out = tmp_path / "copy.xlsx"
    workbook.save(out)
    return out


# ---------- the pivot sheet's header cell ----------


@pytest.mark.parametrize("label", ["Month\n\n\n\n", " Month ", "Month\n"])
def test_pivot_header_cell_may_carry_line_breaks_or_spaces(tmp_path, ra_visits, label):
    workbook = load_workbook(RA_FILE)
    workbook[PIVOT_SHEET]["A1"] = label
    parsed = parse_pivot_sheet(_save(tmp_path, workbook))
    assert parsed.equals(ra_visits)


def test_pivot_header_cell_that_is_not_month_is_still_rejected(tmp_path):
    workbook = load_workbook(RA_FILE)
    workbook[PIVOT_SHEET]["A1"] = "Months"
    with pytest.raises(PivotStructureError, match="exactly one patient-visit pivot sheet"):
        parse_pivot_sheet(_save(tmp_path, workbook))


# ---------- month labels without a year ----------

MONTHS = [datetime(2020, 10, 1), datetime(2020, 11, 1), datetime(2020, 12, 1), datetime(2021, 1, 1)]


def _synthetic(tmp_path, labels, pivot_months=MONTHS, keep_pivot=True):
    """A tiny OA-style workbook: a pivot sheet (dates in column A) and a Place-of-Service sheet."""
    workbook = Workbook()
    pivot = workbook.active
    pivot.title = "M15_19_OA_PAT_VISIT"
    pivot["A1"] = "Month\n\n\n"
    pivot["B1"] = "ORTHOPEDIC SURGERY\n40 TO 59\nFEMALE\nPatient Visits"
    for number, month in enumerate(pivot_months, start=2):
        pivot.cell(number, 1).value = month
    pos = workbook.create_sheet("M15_19_OA_PAT_VISIT1")
    for column, name in enumerate(["Month", "HOSPITAL", "OFFICE", "OTHER", "TELEHEALTH"], start=1):
        pos.cell(HEADER_ROW, column).value = name
    for number, label in enumerate(labels, start=HEADER_ROW + 1):
        pos.cell(number, 1).value = label
        for column, value in enumerate([10, 20, 30, 40], start=2):
            pos.cell(number, column).value = value + number
    if not keep_pivot:
        workbook.remove(pivot)
    out = tmp_path / "synthetic.xlsx"
    workbook.save(out)
    return out


def test_labels_without_a_year_take_their_years_from_the_pivot_sheet(tmp_path):
    parsed = parse_place_of_service(_synthetic(tmp_path, ["Oct...", "Nov...", "Dec...", "Jan..."]))
    assert sorted(parsed["month"].unique()) == [pd.Timestamp(m) for m in MONTHS]
    assert set(parsed["disease_area"]) == {"OA"}
    assert len(parsed) == 4 * 4


def test_labels_with_a_year_are_unchanged(tmp_path):
    labels = ["Oct 2020", "Nov 2020", "Dec 2020", "Jan 2021"]
    parsed = parse_place_of_service(_synthetic(tmp_path, labels))
    assert sorted(parsed["month"].unique()) == [pd.Timestamp(m) for m in MONTHS]


def test_a_label_whose_month_name_disagrees_with_the_pivot_is_rejected(tmp_path):
    path = _synthetic(tmp_path, ["Oct...", "Nov...", "Feb...", "Jan..."])
    with pytest.raises(PlaceOfServiceError, match="label 3 is 'Feb...' but the pivot month is Dec"):
        parse_place_of_service(path)


def test_a_different_number_of_labels_than_pivot_months_is_rejected(tmp_path):
    path = _synthetic(tmp_path, ["Oct...", "Nov...", "Dec..."])
    with pytest.raises(PlaceOfServiceError, match="3 labels but the pivot sheet has 4 months"):
        parse_place_of_service(path)


def test_pivot_months_that_are_not_consecutive_are_rejected(tmp_path):
    months = [datetime(2020, 10, 1), datetime(2020, 11, 1)]
    months += [datetime(2021, 1, 1), datetime(2021, 2, 1)]
    path = _synthetic(tmp_path, ["Oct...", "Nov...", "Jan...", "Feb..."], pivot_months=months)
    with pytest.raises(PlaceOfServiceError, match="not consecutive"):
        parse_place_of_service(path)


def test_labels_without_a_year_need_a_pivot_sheet_in_the_same_workbook(tmp_path):
    path = _synthetic(tmp_path, ["Oct...", "Nov...", "Dec...", "Jan..."], keep_pivot=False)
    with pytest.raises(PlaceOfServiceError, match="no pivot sheet to take the years from"):
        parse_place_of_service(path)


def test_a_mix_of_dated_and_undated_labels_is_rejected(tmp_path):
    path = _synthetic(tmp_path, ["Oct 2020", "Nov...", "Dec...", "Jan..."])
    with pytest.raises(PlaceOfServiceError, match="is not 'Mon YYYY'"):
        parse_place_of_service(path)


def test_a_sparse_sheet_without_years_is_rejected_not_guessed(tmp_path):
    # the committed RA workbook lists only the 58 months that had RA visits, so with the years
    # removed there is no telling which of the 72 pivot months are missing
    workbook = load_workbook(RA_FILE)
    ws = workbook[POS_SHEET]
    for row in range(HEADER_ROW + 1, ws.max_row + 1):
        label = ws.cell(row, 1).value
        if isinstance(label, str):
            ws.cell(row, 1).value = datetime.strptime(label, "%b %Y").strftime("%b") + "..."
    with pytest.raises(PlaceOfServiceError, match="58 labels but the pivot sheet has 72 months"):
        parse_place_of_service(_save(tmp_path, workbook))
