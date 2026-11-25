"""Tests for the Place-of-Service loader, run against the real committed extracts."""

import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

from oa_market_intelligence.ingestion.place_of_service_loader import (
    OUTPUT_COLUMNS,
    PlaceOfServiceError,
    parse_place_of_service,
)

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
OA_FILE = RAW / "Team1_M15_19_OA.xlsx"
RA_FILE = RAW / "Team1_M04_RA.xlsx"
RA_POS_SHEET = "M04_RA_PAT_VISIT1"
HEADER_ROW = 29


@pytest.fixture(scope="module")
def oa_pos():
    return parse_place_of_service(OA_FILE)


@pytest.fixture(scope="module")
def ra_pos():
    return parse_place_of_service(RA_FILE)


def _corrupted_copy(tmp_path, mutate):
    workbook = load_workbook(RA_FILE)
    mutate(workbook)
    out = tmp_path / "corrupted.xlsx"
    workbook.save(out)
    return out


# ---------- real OA extract ----------


def test_oa_has_all_72_months_and_four_settings(oa_pos):
    assert list(oa_pos.columns) == OUTPUT_COLUMNS
    assert (oa_pos["disease_area"] == "OA").all()
    assert oa_pos["month"].nunique() == 72
    assert oa_pos["month"].min().strftime("%Y-%m") == "2020-10"
    assert oa_pos["month"].max().strftime("%Y-%m") == "2026-09"
    assert set(oa_pos["place_of_service"]) == {"HOSPITAL", "OFFICE", "OTHER", "TELEHEALTH"}


def test_oa_total_matches_the_documented_market_total(oa_pos):
    assert oa_pos["patient_visits"].sum() == 7_658_186


def test_oa_first_month_is_mostly_office_visits_with_a_small_telehealth_share(oa_pos):
    # the April 2020 shock is before the Nov 2026 pull's first month (Oct 2020)
    first = oa_pos[oa_pos["month"] == "2020-10-01"].set_index("place_of_service")
    assert first.loc["OFFICE", "patient_visits"] == 87_360
    assert first.loc["TELEHEALTH", "patient_visits"] == 376


def test_month_column_matches_the_pivot_parser_dtype(oa_pos):
    assert str(oa_pos["month"].dtype) == "datetime64[ns]"


def test_keys_are_unique_and_counts_positive(oa_pos):
    assert not oa_pos.duplicated(["month", "disease_area", "place_of_service"]).any()
    assert (oa_pos["patient_visits"] > 0).all()


# ---------- real RA extract ----------


def test_ra_has_all_four_settings_and_blank_cells_are_absent_not_zero_filled(ra_pos):
    assert (ra_pos["disease_area"] == "RA").all()
    assert set(ra_pos["place_of_service"]) == {"HOSPITAL", "OFFICE", "OTHER", "TELEHEALTH"}
    assert ra_pos["month"].nunique() == 72
    assert len(ra_pos) == 218 < 72 * 4  # a blank cell (no visits) gives no row
    assert (ra_pos["patient_visits"] > 0).all()


def test_disease_area_ignores_the_file_name(tmp_path):
    misleading = tmp_path / "Team1_M15_19_OA.xlsx"
    shutil.copy(RA_FILE, misleading)
    assert (parse_place_of_service(misleading)["disease_area"] == "RA").all()


# ---------- inputs that must be refused ----------


@pytest.mark.parametrize("name", ["Branded Generic - OA.xlsx", "Branded Generic - RA.xlsx"])
def test_refuses_the_reference_workbooks_as_place_of_service_data(name):
    # the Branded Generic workbooks are product tables; they must never be read as visit data
    with pytest.raises(PlaceOfServiceError, match="exactly one Place-of-Service sheet"):
        parse_place_of_service(RAW / name)


def test_rejects_a_negative_visit_count(tmp_path):
    def mutate(wb):
        wb[RA_POS_SHEET].cell(row=HEADER_ROW + 1, column=2).value = -1

    with pytest.raises(PlaceOfServiceError, match="non-negative integer"):
        parse_place_of_service(_corrupted_copy(tmp_path, mutate))


def test_rejects_a_duplicated_month(tmp_path):
    def mutate(wb):
        ws = wb[RA_POS_SHEET]
        ws.cell(row=HEADER_ROW + 2, column=1).value = ws.cell(row=HEADER_ROW + 1, column=1).value

    # the RA sheet's labels carry no year, so a repeated label no longer fits its pivot month
    # (a repeated dated label is tested in test_new_extract_intake.py)
    with pytest.raises(PlaceOfServiceError, match="is 'Oct...' but the pivot month is Nov"):
        parse_place_of_service(_corrupted_copy(tmp_path, mutate))


def test_rejects_an_unknown_setting_header(tmp_path):
    def mutate(wb):
        wb[RA_POS_SHEET].cell(row=HEADER_ROW, column=2).value = "PHARMACY"

    with pytest.raises(PlaceOfServiceError, match="exactly one Place-of-Service sheet"):
        parse_place_of_service(_corrupted_copy(tmp_path, mutate))


def test_rejects_an_unrecognizable_sheet_name(tmp_path):
    def mutate(wb):
        wb[RA_POS_SHEET].title = "Sheet2"

    with pytest.raises(PlaceOfServiceError, match="OA from RA"):
        parse_place_of_service(_corrupted_copy(tmp_path, mutate))


def test_rejects_a_workbook_with_no_place_of_service_sheet(tmp_path):
    def mutate(wb):
        del wb[RA_POS_SHEET]

    with pytest.raises(PlaceOfServiceError, match="exactly one Place-of-Service sheet"):
        parse_place_of_service(_corrupted_copy(tmp_path, mutate))
