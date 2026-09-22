"""Tests for the Pandera validation gate.

Real-data tests confirm every existing loader's output actually passes (no false
positives). Corruption tests build small DataFrames by hand rather than corrupted
Excel files: this gate is meant to catch a bad *value* regardless of where it came
from, including a future loader bug, not just the specific corruptions Steps 2-4
already test at the file-parsing level.
"""

from pathlib import Path

import pandas as pd
import pytest

from oa_market_intelligence.ingestion.nmta_loader import parse_pivot_sheet
from oa_market_intelligence.ingestion.openfda_client import build_approval_date_lookup
from oa_market_intelligence.ingestion.place_of_service_loader import parse_place_of_service
from oa_market_intelligence.ingestion.reference_loader import parse_reference_table
from oa_market_intelligence.ingestion.validation import (
    ExtractValidationError,
    validate_fda_lookup,
    validate_nmta_visits,
    validate_place_of_service,
    validate_reference_table,
)

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

VALID_VISIT_ROW = {
    "month": pd.Timestamp("2019-08-01"),
    "disease_area": "OA",
    "manufacturer": "PACIRA PHARM",
    "product": "ZILRETTA",
    "specialty": "ORTHOPEDIC SURGERY",
    "age_band": "40 TO 59",
    "gender": "FEMALE",
    "patient_visits": 1,
}

VALID_POS_ROW = {
    "month": pd.Timestamp("2019-08-01"),
    "disease_area": "OA",
    "place_of_service": "OFFICE",
    "patient_visits": 1,
}

VALID_REF_ROW = {
    "disease_area": "OA",
    "manufacturer": "PACIRA PHARM",
    "product": "ZILRETTA",
    "brand_generic_tag": "BRANDED GENERIC",
    "icd10_code": "M17",
    "icd10_label": "OSTEOARTHRITIS OF KNEE",
    "patient_visits": 1,
}


def _frame(base: dict, **overrides) -> pd.DataFrame:
    return pd.DataFrame([{**base, **overrides}])


# ---------- real loader output must pass cleanly ----------


def test_real_oa_and_ra_visits_pass():
    oa = validate_nmta_visits(parse_pivot_sheet(RAW / "Team1_M15_19_OA.xlsx"))
    ra = validate_nmta_visits(parse_pivot_sheet(RAW / "Team1_M04_RA.xlsx"))
    assert len(oa) == 240_773
    assert len(ra) == 1_021


def test_real_place_of_service_passes():
    oa = validate_place_of_service(parse_place_of_service(RAW / "Team1_M15_19_OA.xlsx"))
    assert oa["patient_visits"].sum() == 7_189_004


def test_real_reference_tables_pass():
    oa = validate_reference_table(parse_reference_table(RAW / "Branded Generic - OA.xlsx"))
    ra = validate_reference_table(parse_reference_table(RAW / "Branded Generic - RA.xlsx"))
    assert len(oa) == 834
    assert len(ra) == 18


def test_real_openfda_lookup_passes():
    frame = build_approval_date_lookup(["ZILRETTA", "NOT-A-REAL-DRUG-XYZ"])
    by_product = validate_fda_lookup(frame).set_index("product")["fda_approval_date"]
    assert by_product["ZILRETTA"] == pd.Timestamp("2017-10-06")
    assert pd.isna(by_product["NOT-A-REAL-DRUG-XYZ"])


# ---------- nmta visits: OA and RA have different valid value sets ----------


def test_rejects_an_unrecognized_specialty():
    with pytest.raises(ExtractValidationError, match="OA nmta visits"):
        validate_nmta_visits(_frame(VALID_VISIT_ROW, specialty="NOT A REAL SPECIALTY"))


def test_rejects_an_oa_only_specialty_submitted_as_ra():
    # RA's 19 specialties are a confirmed subset of OA's 50 (Step 2), so there is no
    # specialty valid for RA but not OA - only this direction is testable for real.
    # CARDIOLOGY is one of the 31 OA specialties RA never uses.
    row = _frame(VALID_VISIT_ROW, disease_area="RA", specialty="CARDIOLOGY")
    with pytest.raises(ExtractValidationError, match="RA nmta visits"):
        validate_nmta_visits(row)


def test_ra_rejects_unspecified_age_band_and_gender_oa_allows():
    oa_row = _frame(VALID_VISIT_ROW, age_band="UNSPECIFIED", gender="UNSPECIFIED")
    assert len(validate_nmta_visits(oa_row)) == 1

    ra_row = _frame(
        VALID_VISIT_ROW, disease_area="RA", specialty="RHEUMATOLOGY", age_band="UNSPECIFIED"
    )
    with pytest.raises(ExtractValidationError, match="RA nmta visits"):
        validate_nmta_visits(ra_row)


def test_rejects_a_zero_visit_count():
    with pytest.raises(ExtractValidationError):
        validate_nmta_visits(_frame(VALID_VISIT_ROW, patient_visits=0))


def test_rejects_a_negative_visit_count():
    with pytest.raises(ExtractValidationError):
        validate_nmta_visits(_frame(VALID_VISIT_ROW, patient_visits=-1))


def test_rejects_an_unrecognized_disease_area_without_guessing_which_schema_to_use():
    with pytest.raises(ExtractValidationError, match="unrecognized disease_area"):
        validate_nmta_visits(_frame(VALID_VISIT_ROW, disease_area="XX"))


def test_rejects_an_unexpected_extra_column():
    frame = _frame(VALID_VISIT_ROW)
    frame["unexpected_column"] = "surprise"
    with pytest.raises(ExtractValidationError):
        validate_nmta_visits(frame)


def test_a_mix_of_oa_and_ra_rows_is_validated_against_the_correct_schema_each():
    ra_row = {**VALID_VISIT_ROW, "disease_area": "RA", "specialty": "RHEUMATOLOGY"}
    mixed = pd.concat([_frame(VALID_VISIT_ROW), _frame(ra_row)], ignore_index=True)
    assert len(validate_nmta_visits(mixed)) == 2


# ---------- place of service ----------


def test_rejects_an_unrecognized_setting():
    with pytest.raises(ExtractValidationError):
        validate_place_of_service(_frame(VALID_POS_ROW, place_of_service="PHARMACY"))


def test_place_of_service_rejects_zero_visits_too():
    # Blank cells are dropped upstream (place_of_service_loader.py), so a stored zero
    # would mean something went wrong before this gate, not that zero is meaningful here.
    with pytest.raises(ExtractValidationError):
        validate_place_of_service(_frame(VALID_POS_ROW, patient_visits=0))


# ---------- reference table ----------


def test_rejects_an_unrecognized_brand_generic_tag():
    with pytest.raises(ExtractValidationError):
        validate_reference_table(_frame(VALID_REF_ROW, brand_generic_tag="WEIRD"))


def test_rejects_an_icd10_code_outside_the_oa_ra_scope():
    with pytest.raises(ExtractValidationError):
        validate_reference_table(_frame(VALID_REF_ROW, icd10_code="M05"))


def test_reference_table_allows_zero_visits_unlike_the_pivot_and_pos_schemas():
    # A blank ICD cell is loaded as 0 by reference_loader.py, and that must pass here.
    assert len(validate_reference_table(_frame(VALID_REF_ROW, patient_visits=0))) == 1


def test_rejects_a_blank_manufacturer():
    with pytest.raises(ExtractValidationError):
        validate_reference_table(_frame(VALID_REF_ROW, manufacturer=""))


# ---------- openFDA lookup ----------


def test_fda_lookup_allows_a_null_approval_date_for_an_unmatched_product():
    frame = pd.DataFrame({"product": ["SOME DRUG"], "fda_approval_date": [pd.NaT]})
    assert pd.isna(validate_fda_lookup(frame)["fda_approval_date"].iloc[0])


def test_fda_lookup_rejects_a_blank_product_name():
    frame = pd.DataFrame({"product": [""], "fda_approval_date": [pd.Timestamp("2017-10-06")]})
    with pytest.raises(ExtractValidationError):
        validate_fda_lookup(frame)
