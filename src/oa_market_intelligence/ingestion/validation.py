"""Pandera schemas gating each loader's output before it can reach the Silver builder.

Per docs/PROPOSAL.md §3.4: expected column count and naming, valid ranges for visit
counts, expected categorical values, and presence of the expected ICD-10 scope. The
value lists below are the confirmed real ones from docs/data_dictionary.md §2.3, not
assumed — OA and RA have different specialty/age-band/gender sets, so the pivot output
is split and validated per disease area rather than against one merged list.
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa

OA_SPECIALTIES = frozenset(
    {
        "ADDICTION MEDICINE", "ALLERGY", "ANESTHESIOLOGY", "CARDIOLOGY",
        "CLINICAL NEUROPHYSIOL.", "CRITICAL CARE MEDICINE", "DERMATOLOGY",
        "EMERGENCY MEDICINE", "ENDOCRINOLOGY", "FAMILY PRACTICE", "GASTROENTEROLOGY",
        "GEN PREVENTIVE MEDICINE", "GENERAL PRACTICE", "GENERAL SURGERY", "GERIATRICS",
        "HEMATOLOGY", "HOSPICE & PALLIATIVE MED", "INFECTIOUS DISEASE",
        "INTERNAL MED/PEDIATRICS", "INTERNAL MEDICINE", "NATUROPATHIC DOCTOR",
        "NEPHROLOGY", "NEUROLOGICAL SURGERY", "NEUROLOGY", "NUCLEAR MEDICINE",
        "NURSE PRACTITIONER", "OBSTETRICS/GYNECOLOGY", "OCCUPATIONAL MEDICINE",
        "ONCOLOGY", "ORTHO SURG OF SPINE", "ORTHOPEDIC SURGERY", "OSTEOPATHIC MEDICINE",
        "OTHER", "OTHER SURGERY", "PAIN MEDICINE", "PATHOLOGY",
        "PEDIATRIC CRITICAL CARE", "PEDIATRICS", "PHYSICAL MEDICINE & REHAB",
        "PHYSICIAN ASSISTANT", "PLASTIC SURGERY", "PSYCHIATRY",
        "PULMONARY CRITICAL CARE", "PULMONARY DISEASES", "RADIOLOGY", "RHEUMATOLOGY",
        "SLEEP MEDICINE", "SPORTS MEDICINE", "THORACIC SURGERY", "UROLOGY",
    }
)  # 50 distinct values (data_dictionary.md §2.3)

RA_SPECIALTIES = frozenset(
    {
        "ALLERGY", "DERMATOLOGY", "ENDOCRINOLOGY", "FAMILY PRACTICE",
        "GASTROENTEROLOGY", "GERIATRICS", "INFECTIOUS DISEASE",
        "INTERNAL MED/PEDIATRICS", "INTERNAL MEDICINE", "NEPHROLOGY", "NEUROLOGY",
        "NURSE PRACTITIONER", "ONCOLOGY", "ORTHOPEDIC SURGERY", "OSTEOPATHIC MEDICINE",
        "PEDIATRICS", "PHYSICIAN ASSISTANT", "PULMONARY CRITICAL CARE", "RHEUMATOLOGY",
    }
)  # 19 distinct values, all a subset of OA_SPECIALTIES

OA_AGE_BANDS = frozenset(
    {
        "00 TO 02", "03 TO 09", "10 TO 19", "20 TO 39", "40 TO 59",
        "60 TO 64", "65 TO 74", "75 TO 84", "85 +", "UNSPECIFIED",
    }
)
RA_AGE_BANDS = OA_AGE_BANDS - {"UNSPECIFIED"}

OA_GENDERS = frozenset({"FEMALE", "MALE", "UNSPECIFIED"})
RA_GENDERS = frozenset({"FEMALE", "MALE"})

BRAND_GENERIC_TAGS = frozenset({"BRAND", "GENERIC", "BRANDED GENERIC", "OTHER"})
PLACE_OF_SERVICE_SETTINGS = frozenset({"HOSPITAL", "OFFICE", "OTHER", "TELEHEALTH"})
ICD10_SCOPE = frozenset({"M04", "M15", "M16", "M17", "M18", "M19"})  # PROPOSAL.md §3.1


class ExtractValidationError(ValueError):
    """A loader's output fails validation and must not reach the Silver builder."""


def _non_blank_string() -> pa.Column:
    return pa.Column(str, pa.Check.str_length(min_value=1), nullable=False)


def _visits_column(*, allow_zero: bool) -> pa.Column:
    minimum = 0 if allow_zero else 1
    return pa.Column(int, pa.Check.ge(minimum), nullable=False, coerce=False)


def _nmta_visits_schema(
    specialties: frozenset, age_bands: frozenset, genders: frozenset
) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "month": pa.Column(pa.Timestamp, nullable=False),
            "disease_area": _non_blank_string(),
            "manufacturer": _non_blank_string(),
            "product": _non_blank_string(),
            "specialty": pa.Column(str, pa.Check.isin(specialties), nullable=False),
            "age_band": pa.Column(str, pa.Check.isin(age_bands), nullable=False),
            "gender": pa.Column(str, pa.Check.isin(genders), nullable=False),
            "patient_visits": _visits_column(allow_zero=False),
        },
        strict=True,
        ordered=False,
    )


_OA_VISITS_SCHEMA = _nmta_visits_schema(OA_SPECIALTIES, OA_AGE_BANDS, OA_GENDERS)
_RA_VISITS_SCHEMA = _nmta_visits_schema(RA_SPECIALTIES, RA_AGE_BANDS, RA_GENDERS)

PLACE_OF_SERVICE_SCHEMA = pa.DataFrameSchema(
    {
        "month": pa.Column(pa.Timestamp, nullable=False),
        "disease_area": pa.Column(str, pa.Check.isin({"OA", "RA"}), nullable=False),
        "place_of_service": pa.Column(
            str, pa.Check.isin(PLACE_OF_SERVICE_SETTINGS), nullable=False
        ),
        "patient_visits": _visits_column(allow_zero=False),
    },
    strict=True,
)

REFERENCE_TABLE_SCHEMA = pa.DataFrameSchema(
    {
        "disease_area": pa.Column(str, pa.Check.isin({"OA", "RA"}), nullable=False),
        "manufacturer": _non_blank_string(),
        "product": _non_blank_string(),
        "brand_generic_tag": pa.Column(str, pa.Check.isin(BRAND_GENERIC_TAGS), nullable=False),
        "icd10_code": pa.Column(str, pa.Check.isin(ICD10_SCOPE), nullable=False),
        "icd10_label": _non_blank_string(),
        # blank ICD cells are loaded as 0 (reference_loader.py), so 0 is expected here
        "patient_visits": _visits_column(allow_zero=True),
    },
    strict=True,
)

FDA_LOOKUP_SCHEMA = pa.DataFrameSchema(
    {
        "product": _non_blank_string(),
        "fda_approval_date": pa.Column(pa.Timestamp, nullable=True),
    },
    strict=True,
)


def _raise_on_failure(schema: pa.DataFrameSchema, frame: pd.DataFrame, label: str) -> pd.DataFrame:
    try:
        return schema.validate(frame, lazy=True)
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases.to_string(index=False)
        raise ExtractValidationError(f"{label} failed validation:\n{cases}") from exc


def validate_nmta_visits(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate a parsed pivot extract, checked per disease area (see module docstring).

    An unrecognized disease_area value fails outright rather than being routed to
    either schema, since neither OA's nor RA's value ranges apply to it.
    """
    unknown = sorted(set(frame["disease_area"].unique()) - {"OA", "RA"})
    if unknown:
        raise ExtractValidationError(f"nmta visits: unrecognized disease_area value(s) {unknown}")

    parts = []
    is_oa = frame["disease_area"] == "OA"
    is_ra = frame["disease_area"] == "RA"
    if is_oa.any():
        parts.append(_raise_on_failure(_OA_VISITS_SCHEMA, frame[is_oa], "OA nmta visits"))
    if is_ra.any():
        parts.append(_raise_on_failure(_RA_VISITS_SCHEMA, frame[is_ra], "RA nmta visits"))
    return pd.concat(parts, ignore_index=True)


def validate_place_of_service(frame: pd.DataFrame) -> pd.DataFrame:
    return _raise_on_failure(PLACE_OF_SERVICE_SCHEMA, frame, "place-of-service extract")


def validate_reference_table(frame: pd.DataFrame) -> pd.DataFrame:
    return _raise_on_failure(REFERENCE_TABLE_SCHEMA, frame, "reference table")


def validate_fda_lookup(frame: pd.DataFrame) -> pd.DataFrame:
    return _raise_on_failure(FDA_LOOKUP_SCHEMA, frame, "openFDA approval-date lookup")
