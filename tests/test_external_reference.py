"""Tests for the 4a reference loaders: states, calendar, codes, crosswalk, bridge, catalogue."""

import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.codes import ALL_CODES
from oa_market_intelligence.external.common import (
    ChecksumMismatch,
    log_run,
    read_manifest,
    replace_table,
    verify_file,
)
from oa_market_intelligence.external.loaders.reference import (
    catalogue_frame,
    crosswalk_frame,
    drug_family_bridge,
    hcpcs_frame,
    load_reference,
    quarters_frame,
    states_frame,
    years_frame,
)
from oa_market_intelligence.external.profile import sha256_of
from oa_market_intelligence.external.schema import create_external_schema


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    create_external_schema(engine)
    return engine


# ---------- states ----------


def test_states_cover_50_states_and_dc_flagged_and_everything_else_unflagged():
    frame = states_frame()
    assert frame["state_code"].is_unique and frame["state_fips"].is_unique
    flagged = frame[frame["is_us_state_or_dc"] == 1]
    assert len(flagged) == 51 and "DC" in set(flagged["state_code"])
    assert {"PR", "VI", "GU", "AS", "MP"} <= set(frame["state_code"]) - set(flagged["state_code"])


def test_the_cms_special_geographies_map_to_the_provider_level_abbreviations():
    by_code = states_frame().set_index("state_code")["state_fips"]
    assert by_code["AA"] == "9A" and by_code["AE"] == "9B" and by_code["AP"] == "9C"
    assert by_code["XX"] == "9D" and by_code["ZZ"] == "9E"
    assert by_code["TX"] == "48" and by_code["AL"] == "01"


# ---------- calendar ----------


def test_quarters_carry_the_months_that_join_the_iqvia_calendar():
    quarters = quarters_frame(2019, 2026).set_index("quarter_id")
    assert quarters.loc["2021Q2", ["first_month_id", "last_month_id"]].tolist() == [202104, 202106]
    assert quarters.loc["2019Q1", "first_month_id"] == 201901
    assert quarters.loc["2026Q4", "last_month_id"] == 202612
    assert len(quarters) == 8 * 4


def test_years_are_a_continuous_range():
    assert years_frame(2014, 2026)["year"].tolist() == list(range(2014, 2027))


# ---------- codes ----------


DESCRIPTIONS = pd.DataFrame(
    {
        "year": [2019, 2024, 2024, 2024, 2019, 2024],
        "code": ["J3304", "J3304", "J1010", "20610", "J7331", "J7331"],
        "description": [
            "old text",
            "Injection, triamcinolone, 1 mg",
            "Methylprednisolone, 1 mg",
            "AMA text",
            "x",
            "y",
        ],
    }
)


def test_the_code_table_has_the_27_approved_codes_in_four_groups():
    frame = hcpcs_frame(DESCRIPTIONS)
    assert set(frame["hcpcs_code"]) == set(ALL_CODES)
    assert frame["code_group"].value_counts().to_dict() == {
        "B_context_hyaluronic": 13,
        "A_primary": 8,
        "D_sensitivity_iv_steroids": 4,
        "C_denominator_procedures": 2,
    }


def test_cpt_codes_never_carry_a_description_and_j_codes_take_the_latest_wording():
    frame = hcpcs_frame(DESCRIPTIONS).set_index("hcpcs_code")
    assert pd.isna(frame.loc["20610", "short_description"]) and frame.loc["20610", "is_cpt"] == 1
    assert frame.loc["J3304", "short_description"] == "Injection, triamcinolone, 1 mg"
    assert frame.loc["J3304", "is_cpt"] == 0


def test_first_and_last_year_seen_come_from_the_years_with_rows():
    frame = hcpcs_frame(DESCRIPTIONS).set_index("hcpcs_code")
    assert frame.loc["J1010", ["first_year_seen", "last_year_seen"]].tolist() == [2024, 2024]
    assert frame.loc["J7331", ["first_year_seen", "last_year_seen"]].tolist() == [2019, 2024]
    assert pd.isna(frame.loc["J2920", "first_year_seen"])  # no rows given for it


# ---------- crosswalk and drug bridge ----------


def test_the_crosswalk_is_the_approved_one():
    frame = crosswalk_frame()
    assert frame["iqvia_group"].nunique() == 12
    pain = frame[frame["iqvia_group"] == "PAIN MEDICINE"]
    assert set(pain["medicare_name"]) == {"Interventional Pain Management", "Pain Management"}
    assert set(pain["relationship"]) == {"combined"}
    assert frame.set_index("iqvia_group").loc["OSTEOPATHIC MEDICINE", "relationship"] == "weak"
    rare = frame[frame["iqvia_group"] == "RARE (grouped)"].iloc[0]
    assert rare["relationship"] == "excluded" and rare["medicare_name"] == "NONE"
    assert (
        frame[frame["relationship"] == "exact"]["iqvia_group"].nunique() == 9
    )  # 8 exact names + PM&R


def test_the_drug_bridge_is_conservative_and_leaves_combination_products_unmapped():
    names = [
        "ZILRETTA",
        "KENALOG",
        "DEPO-MEDROL",
        "SOLU-MEDROL",
        "CELESTONE",
        "BETAMETH DIP AUG",
        "TYLENOL",
        "TAC-3",
        "DEXAMETH S PH",
    ]
    mapped = dict(
        zip(
            drug_family_bridge(names)["iqvia_product_name"],
            drug_family_bridge(names)["drug_family"],
            strict=True,
        )
    )
    assert mapped["ZILRETTA"] == "zilretta_triamcinolone_er"
    assert mapped["KENALOG"] == "triamcinolone_acetonide"
    assert mapped["DEPO-MEDROL"] == "methylprednisolone_acetate"
    assert mapped["SOLU-MEDROL"] == "methylprednisolone_sodium_succinate"
    assert mapped["CELESTONE"] == "betamethasone"
    assert mapped["DEXAMETH S PH"] == "dexamethasone_sodium_phosphate"
    assert "BETAMETH DIP AUG" not in mapped and "TYLENOL" not in mapped and "TAC-3" not in mapped


# ---------- catalogue and common helpers ----------


def _manifest(tmp_path, lines):
    path = tmp_path / "_download_manifest.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return path


def test_the_catalogue_keeps_the_latest_manifest_entry_per_file(tmp_path):
    _manifest(
        tmp_path,
        [
            {
                "dataset": "D",
                "file": "a/x.csv",
                "url": "u1",
                "bytes": 1,
                "sha256": "s1",
                "status": "downloaded",
                "at": "t1",
            },
            {
                "dataset": "D",
                "file": "a/x.csv",
                "url": "u1",
                "bytes": 2,
                "sha256": "s2",
                "status": "downloaded",
                "at": "t2",
            },
            {
                "dataset": "E",
                "file": "b/y.zip",
                "url": "u2",
                "bytes": 3,
                "sha256": "s3",
                "status": "skipped",
                "at": "t3",
            },
        ],
    )
    frame = catalogue_frame(read_manifest(tmp_path)).set_index("relative_path")
    assert (
        len(frame) == 2
        and frame.loc["a/x.csv", "bytes"] == 2
        and frame.loc["a/x.csv", "sha256"] == "s2"
    )
    assert frame.loc["b/y.zip", "dataset"] == "E"


def test_verify_file_accepts_a_matching_file_and_refuses_a_changed_one(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("abc", encoding="utf-8")
    entry = {"file": "f.csv", "bytes": 3, "sha256": sha256_of(path)}
    verify_file(tmp_path, entry)
    path.write_text("abd", encoding="utf-8")
    with pytest.raises(ChecksumMismatch):
        verify_file(tmp_path, entry)
    path.write_text("abcd", encoding="utf-8")
    with pytest.raises(ChecksumMismatch):
        verify_file(tmp_path, entry)
    with pytest.raises(FileNotFoundError):
        verify_file(tmp_path, {"file": "missing.csv", "bytes": 1, "sha256": "x"})


def test_replace_table_is_idempotent_and_log_run_records_the_result(engine):
    frame = states_frame()
    replace_table(engine, "dim_state", frame)
    replace_table(engine, "dim_state", frame)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dim_state")).scalar() == len(frame)
    log_run(engine, "reference", None, rows_read=5, rows_loaded=5, status="ok", note="test")
    with engine.connect() as conn:
        row = conn.execute(text("SELECT source, rows_loaded, status FROM external_load_runs")).one()
    assert tuple(row) == ("reference", 5, "ok")


def test_load_reference_fills_every_reference_table_and_twice_changes_nothing(tmp_path, engine):
    manifest = [
        {
            "dataset": "D",
            "file": "a.csv",
            "url": "u",
            "bytes": 1,
            "sha256": "s",
            "status": "downloaded",
            "at": "t",
        }
    ]
    _manifest(tmp_path, manifest)
    profile = tmp_path / "descriptions.csv"
    DESCRIPTIONS.to_csv(profile, index=False)
    kwargs = dict(
        raw_root=tmp_path, descriptions_csv=profile, product_names=["KENALOG", "ZILRETTA"]
    )
    first = load_reference(engine, **kwargs)
    second = load_reference(engine, **kwargs)
    assert first == second
    with engine.connect() as conn:
        counts = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in first}
    assert counts["dim_state"] == len(states_frame()) and counts["dim_hcpcs_code"] == 27
    assert counts["bronze_external_files"] == 1 and counts["bridge_specialty_crosswalk"] == len(
        crosswalk_frame()
    )
