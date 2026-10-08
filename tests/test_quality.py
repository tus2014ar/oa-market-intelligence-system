"""Tests for the data-quality stage (R3).

Dataset-level checks that go beyond the per-row schema validation: month continuity, both disease
areas present, new categories against a stored baseline, monthly volumes inside the baseline range,
place-of-service months matching the visit months, and restated history against the previous
published database. Errors stop the run; warnings are recorded. Synthetic frames throughout.
"""

import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.quality import (
    BASELINE_JSON,
    DataQualityError,
    build_baseline,
    load_baseline,
    raise_on_errors,
    refresh_dq_report,
    revision_check,
    run_quality_checks,
    summarise_quality,
)
from oa_market_intelligence.warehouse.schema import create_schema

MONTHS = pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01", "2020-05-01"])


def _visits(months=MONTHS, specialty="ORTHOPEDIC SURGERY", per_month=100, areas=("OA", "RA")):
    rows = []
    for area in areas:
        for month in months:
            for i in range(3):
                rows.append(
                    {
                        "month": month,
                        "disease_area": area,
                        "manufacturer": "PACIRA",
                        "product": f"P{i}",
                        "specialty": specialty,
                        "age_band": "65 TO 74",
                        "gender": "FEMALE",
                        "patient_visits": per_month,
                    }
                )
    return pd.DataFrame(rows)


def _pos(months=MONTHS, areas=("OA", "RA")):
    return pd.DataFrame(
        [
            {"month": m, "disease_area": a, "place_of_service": "OFFICE", "patient_visits": 1000}
            for a in areas
            for m in months
        ]
    )


def _reference(areas=("OA", "RA")):
    return pd.DataFrame(
        [
            {
                "disease_area": a,
                "manufacturer": "PACIRA",
                "product": "P0",
                "brand_generic_tag": "BRAND",
                "icd10_code": "M17",
                "icd10_label": "x",
                "patient_visits": 5,
            }
            for a in areas
        ]
    )


@pytest.fixture
def frames():
    return _visits(), _pos(), _reference()


@pytest.fixture
def baseline(frames):
    return build_baseline(*frames)


def _by_id(report):
    return {row["check_id"]: row for row in report}


# ---------- the baseline ----------


def test_the_baseline_records_categories_and_monthly_ranges_and_is_json(frames, baseline):
    json.dumps(baseline)  # must be plain JSON
    assert baseline["sets"]["specialty"] == ["ORTHOPEDIC SURGERY"]
    assert baseline["sets"]["place_of_service"] == ["OFFICE"]
    assert baseline["monthly"]["OA"]["rows"] == {"min": 3, "median": 3.0, "max": 3}
    assert baseline["monthly"]["OA"]["visits_sum"]["max"] == 300
    assert baseline["months"] == [202001, 202005]


def test_data_that_built_the_baseline_passes_every_check(frames, baseline):
    report = run_quality_checks(*frames, baseline=baseline)
    assert [r["check_id"] for r in report if r["status"] == "fail"] == []
    assert summarise_quality(report)["n_errors"] == 0


# ---------- errors (stop the run) ----------


def test_a_gap_in_the_months_is_an_error_that_names_the_missing_month(baseline):
    visits = _visits(months=MONTHS.delete(2))  # no March
    report = _by_id(run_quality_checks(visits, _pos(), _reference(), baseline=baseline))
    row = report["month_continuity"]
    assert row["severity"] == "error" and row["status"] == "fail" and "202003" in row["note"]
    with pytest.raises(DataQualityError, match="month_continuity"):
        raise_on_errors(list(report.values()))


def test_a_missing_disease_area_is_an_error(baseline):
    report = _by_id(
        run_quality_checks(_visits(areas=("OA",)), _pos(), _reference(), baseline=baseline)
    )
    assert report["disease_areas_present"]["status"] == "fail"
    assert report["disease_areas_present"]["severity"] == "error"


def test_warnings_alone_do_not_stop_the_run(baseline):
    visits = _visits(specialty="A NEW SPECIALTY")
    report = run_quality_checks(visits, _pos(), _reference(), baseline=baseline)
    raise_on_errors(report)  # no exception
    assert summarise_quality(report)["n_warnings"] >= 1


# ---------- warnings (recorded) ----------


@pytest.mark.parametrize(
    ("column", "value", "check"),
    [
        ("specialty", "A NEW SPECIALTY", "new_specialties"),
        ("product", "A NEW PRODUCT", "new_products"),
        ("age_band", "90 TO 99", "new_age_bands"),
        ("gender", "OTHER", "new_genders"),
    ],
)
def test_a_category_not_in_the_baseline_is_a_warning_naming_it(baseline, column, value, check):
    visits = _visits()
    visits.loc[0, column] = value
    row = _by_id(run_quality_checks(visits, _pos(), _reference(), baseline=baseline))[check]
    assert row["severity"] == "warning" and row["status"] == "fail" and value in row["note"]


def test_a_new_place_of_service_is_a_warning(baseline):
    pos = _pos()
    pos.loc[0, "place_of_service"] = "MOBILE CLINIC"
    row = _by_id(run_quality_checks(_visits(), pos, _reference(), baseline=baseline))
    assert row["new_place_of_service"]["status"] == "fail"
    assert "MOBILE CLINIC" in row["new_place_of_service"]["note"]


def test_a_month_far_above_the_baseline_range_is_a_warning(baseline):
    months = list(MONTHS) + [pd.Timestamp("2020-06-01")]
    visits = _visits(months=pd.DatetimeIndex(months))
    visits.loc[visits["month"] == "2020-06-01", "patient_visits"] = 1000  # 10x the baseline
    row = _by_id(
        run_quality_checks(visits, _pos(pd.DatetimeIndex(months)), _reference(), baseline=baseline)
    )
    assert row["visits_per_month_in_range"]["status"] == "fail"
    assert "202006" in row["visits_per_month_in_range"]["note"]


def test_a_month_far_below_the_baseline_range_is_a_warning(baseline):
    visits = _visits()
    visits.loc[visits["month"] == "2020-03-01", "patient_visits"] = 1  # a collapse
    row = _by_id(run_quality_checks(visits, _pos(), _reference(), baseline=baseline))
    assert row["visits_per_month_in_range"]["status"] == "fail"


def test_a_month_inside_the_tolerance_passes(baseline):
    visits = _visits()
    visits.loc[visits["month"] == "2020-03-01", "patient_visits"] = 140  # within 1.5x of the max
    row = _by_id(run_quality_checks(visits, _pos(), _reference(), baseline=baseline))
    assert row["visits_per_month_in_range"]["status"] == "pass"


def test_place_of_service_months_that_differ_from_the_visit_months_are_a_warning(baseline):
    row = _by_id(run_quality_checks(_visits(), _pos(MONTHS[:4]), _reference(), baseline=baseline))
    assert row["pos_months_match_visit_months"]["status"] == "fail"


def test_without_a_baseline_the_baseline_checks_are_skipped_not_passed(frames):
    report = _by_id(run_quality_checks(*frames, baseline=None))
    assert report["new_specialties"]["status"] == "skipped"
    assert report["month_continuity"]["status"] == "pass"  # structural checks still run


# ---------- restated history ----------


def _gold(branded=100, generic=1000, nsaid=500):
    return pd.DataFrame(
        {
            "month_id": [202001, 202002],
            "branded_injectable_visits": [branded, branded],
            "generic_corticosteroid_visits": [generic, generic],
            "nsaid_otc_visits": [nsaid, nsaid],
        }
    )


def test_unchanged_history_passes_and_a_restated_month_is_a_warning():
    assert revision_check(_gold(), _gold())["status"] == "pass"
    restated = _gold()
    restated.loc[0, "branded_injectable_visits"] = 110  # +10%
    row = revision_check(_gold(), restated)
    assert row["status"] == "fail" and row["severity"] == "warning" and "202001" in row["note"]


def test_new_months_are_not_a_restatement_and_a_small_change_is_tolerated():
    longer = pd.concat([_gold(), _gold().assign(month_id=[202003, 202004])], ignore_index=True)
    assert revision_check(_gold(), longer)["status"] == "pass"
    nudged = _gold()
    nudged.loc[0, "nsaid_otc_visits"] = 501  # 0.2%, under the 0.5% tolerance
    assert revision_check(_gold(), nudged)["status"] == "pass"


def test_with_no_previous_database_the_restatement_check_is_skipped():
    assert revision_check(None, _gold())["status"] == "skipped"


# ---------- the report table and summary ----------


def test_the_report_is_stored_replaced_on_rerun_and_summarised(frames, baseline):
    report = run_quality_checks(*frames, baseline=baseline)
    engine = create_engine("sqlite://")
    create_schema(engine)
    assert refresh_dq_report(engine, report) == len(report)
    assert refresh_dq_report(engine, report) == len(report)
    with engine.connect() as conn:
        stored = pd.read_sql(text("SELECT * FROM dq_report"), conn)
    assert len(stored) == len(report) and stored["checked_at"].notna().all()
    summary = summarise_quality(report)
    assert summary["n_checks"] == len(report) and summary["warnings"] == []


def test_the_summary_lists_warnings_with_their_notes(baseline):
    report = run_quality_checks(
        _visits(specialty="NEW ONE"), _pos(), _reference(), baseline=baseline
    )
    summary = summarise_quality(report)
    assert summary["n_warnings"] >= 1
    assert any("NEW ONE" in w["note"] for w in summary["warnings"])


# ---------- the committed baseline ----------


def test_the_committed_baseline_has_the_expected_shape():
    baseline = load_baseline(BASELINE_JSON)
    assert {"months", "sets", "monthly", "pos_monthly", "tolerances"} <= set(baseline)
    assert {"specialty", "product", "age_band", "gender", "place_of_service"} <= set(
        baseline["sets"]
    )
    assert baseline["months"][0] == 201908 and baseline["months"][1] >= 202507
