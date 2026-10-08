"""Tests for the 4c loaders: provider-level Part B, provider registry, CDC PLACES, NUCC taxonomy."""

import json
import zipfile

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.common import ChecksumMismatch
from oa_market_intelligence.external.loaders.nppes import (
    aggregate_nppes_chunk,
    clean_state,
    load_nppes,
    snapshot_date_from_member,
)
from oa_market_intelligence.external.loaders.nucc import (
    classify_taxonomy,
    load_nucc,
    taxonomy_bridge,
)
from oa_market_intelligence.external.loaders.partb_provider import (
    clean_partb_provider,
    load_partb_provider,
)
from oa_market_intelligence.external.loaders.places import clean_places, load_places
from oa_market_intelligence.external.loaders.reference import states_frame
from oa_market_intelligence.external.profile import sha256_of
from oa_market_intelligence.external.schema import create_external_schema


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    create_external_schema(engine)
    return engine


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def _register(raw, path, dataset="d"):
    entry = {
        "dataset": dataset,
        "file": str(path.relative_to(raw)).replace("/", "\\"),
        "bytes": path.stat().st_size,
        "sha256": sha256_of(path),
        "status": "downloaded",
    }
    with open(raw / "_download_manifest.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


# ---------- provider-level Part B ----------

PB_COLUMNS = [
    "Rndrng_NPI",
    "Rndrng_Prvdr_Last_Org_Name",
    "Rndrng_Prvdr_St1",
    "Rndrng_Prvdr_Ent_Cd",
    "Rndrng_Prvdr_State_Abrvtn",
    "Rndrng_Prvdr_Type",
    "Rndrng_Prvdr_Mdcr_Prtcptg_Ind",
    "HCPCS_Cd",
    "Place_Of_Srvc",
    "Tot_Benes",
    "Tot_Srvcs",
    "Tot_Bene_Day_Srvcs",
    "Avg_Sbmtd_Chrg",
    "Avg_Mdcr_Alowd_Amt",
    "Avg_Mdcr_Pymt_Amt",
    "Avg_Mdcr_Stdzd_Amt",
]
PB_ROWS = [
    [
        "1003",
        "Smith",
        "1 Main St",
        "I",
        "TX",
        "Rheumatology",
        "Y",
        "J3304",
        "O",
        "13",
        "896",
        "27",
        "50",
        "17",
        "13",
        "14",
    ],
    [
        "1004",
        "Jones",
        "2 Oak St",
        "I",
        "AA",
        "Family Practice",
        "Y",
        "J3301",
        "F",
        "40",
        "100.5",
        "50",
        "1",
        "2",
        "3",
        "4",
    ],
    [
        "1005",
        "Clinic Inc",
        "3 Elm St",
        "O",
        "CA",
        "Clinic or Group Practice",
        "Y",
        "J3304",
        "O",
        "30",
        "90",
        "31",
        "1",
        "2",
        "3",
        "4",
    ],
    [
        "1006",
        "Noone",
        "4 Pine St",
        "I",
        "TX",
        "Family Practice",
        "Y",
        "99213",
        "O",
        "99",
        "99",
        "99",
        "1",
        "2",
        "3",
        "4",
    ],
]


def test_provider_rows_keep_approved_codes_and_never_carry_names_or_addresses():
    out = clean_partb_provider(pd.DataFrame(PB_ROWS, columns=PB_COLUMNS), 2022)
    assert len(out) == 3 and set(out["hcpcs_code"]) == {"J3304", "J3301"}  # 99213 is not approved
    assert not any(c in out.columns for c in ("Rndrng_Prvdr_Last_Org_Name", "Rndrng_Prvdr_St1"))
    row = out[out["npi"] == "1003"].iloc[0]
    assert (
        row["year"] == 2022 and row["specialty_cms"] == "Rheumatology" and row["entity_type"] == "I"
    )
    assert (
        row["benes"] == 13 and row["services"] == pytest.approx(896) and row["state_code"] == "TX"
    )
    assert row["avg_payment_amt"] == pytest.approx(13)
    assert out[out["npi"] == "1004"].iloc[0]["state_code"] == "AA"  # armed forces, kept as written


def _pb_tree(tmp_path):
    folder = tmp_path / "Medicare Physician & Other Practitioners - by Provider and Service"
    (folder / "2023").mkdir(parents=True)
    filtered = folder / "2023" / "PartB_ProviderService_2023_approved_codes.csv"
    keep = pd.DataFrame(PB_ROWS[:3], columns=PB_COLUMNS).assign(code_group="A_primary")
    keep.to_csv(filtered, index=False)
    _register(tmp_path, filtered)
    nested = folder / "Medicare Physician & Other Practitioners - by Provider and Service" / "2024"
    nested.mkdir(parents=True)
    full = nested / "PHY_R26_P05_V10_D24_Prov_Svc.csv"
    pd.DataFrame(PB_ROWS, columns=PB_COLUMNS).to_csv(
        full, index=False
    )  # all four rows, incl. 99213
    _register(tmp_path, full)
    return tmp_path


def test_provider_data_loads_filtered_years_and_filters_the_full_2024_file(tmp_path, engine):
    raw = _pb_tree(tmp_path)
    loaded = load_partb_provider(engine, raw)
    load_partb_provider(engine, raw)  # idempotent
    assert loaded == {2023: 3, 2024: 3} and _count(engine, "fact_ext_partb_provider") == 6
    with engine.connect() as conn:
        years = dict(
            conn.execute(
                text("SELECT year, count(*) FROM fact_ext_partb_provider GROUP BY year")
            ).all()
        )
    assert years == {2023: 3, 2024: 3}


def test_a_changed_provider_file_is_refused(tmp_path, engine):
    raw = _pb_tree(tmp_path)
    path = next((raw).rglob("PartB_ProviderService_2023_approved_codes.csv"))
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ChecksumMismatch):
        load_partb_provider(engine, raw)
    assert _count(engine, "fact_ext_partb_provider") == 0


# ---------- provider registry ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TX", "TX"),
        ("tx", "TX"),
        (" TX ", "TX"),
        ("Texas", "TX"),
        ("NEW YORK", "NY"),
        ("District of Columbia", "DC"),
        ("ZZ", "ZZ"),
        ("", None),
        ("TXX", None),
        ("Tx.", None),
        ("Ontario", None),
    ],
)
def test_state_values_are_cleaned_to_a_two_letter_code_or_dropped(raw, expected):
    states = states_frame()
    out = clean_state(
        pd.Series([raw]),
        set(states["state_code"]),
        dict(zip(states["state_name"].str.upper(), states["state_code"], strict=True)),
    )
    assert (out.iloc[0] if out.notna().iloc[0] else None) == expected


NP_COLUMNS = [
    "NPI",
    "Entity Type Code",
    "Provider Business Practice Location Address State Name",
    "Healthcare Provider Taxonomy Code_1",
    "NPI Deactivation Date",
]
NP_ROWS = [
    ["1", "1", "TX", "207RR0500X", ""],
    ["2", "1", "Texas", "207RR0500X", ""],
    ["3", "1", "CA", "207Q00000X", ""],
    ["4", "2", "CA", "207Q00000X", ""],  # organisation
    ["5", "1", "CA", "207Q00000X", "01/01/2020"],  # deactivated
    ["6", "1", "Nowhere", "207Q00000X", ""],  # state cannot be mapped
    ["7", "1", "TX", "", ""],  # no taxonomy code
    ["8", "", "", "", ""],  # blank record
]


def test_registry_counts_individuals_by_state_and_taxonomy_and_accounts_for_every_row():
    states = states_frame()
    counts, stats = aggregate_nppes_chunk(pd.DataFrame(NP_ROWS, columns=NP_COLUMNS), states)
    got = {(r.state_code, r.taxonomy_code): r.n for r in counts.itertuples()}
    assert got == {("TX", "207RR0500X"): 2, ("CA", "207Q00000X"): 1}
    assert stats == {
        "rows_read": 8,
        "not_individual": 2,
        "deactivated": 1,
        "blank_taxonomy": 1,
        "unmapped_state": 1,
        "counted": 3,
    }
    assert (
        stats["rows_read"]
        == stats["not_individual"]
        + stats["deactivated"]
        + stats["blank_taxonomy"]
        + stats["unmapped_state"]
        + stats["counted"]
    )


def test_the_snapshot_date_comes_from_the_data_file_name():
    assert snapshot_date_from_member("npidata_pfile_20050523-20260913.csv") == "2026-09-13"


def _nppes_file(tmp_path):
    folder = tmp_path / "NPPES"
    folder.mkdir()
    path = folder / "NPPES_Data_Dissemination_September_2026_V2.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "npidata_pfile_20050523-20260913.csv",
            pd.DataFrame(NP_ROWS, columns=NP_COLUMNS).to_csv(index=False),
        )
        z.writestr("pl_pfile_20050523-20260913.csv", "x\n1\n")
    _register(tmp_path, path)
    return path


def test_the_registry_loads_idempotently_and_logs_where_every_row_went(tmp_path, engine):
    _nppes_file(tmp_path)
    load_nppes(engine, tmp_path)
    load_nppes(engine, tmp_path)
    assert _count(engine, "fact_ext_provider_counts") == 2
    with engine.connect() as conn:
        note = conn.execute(
            text("SELECT note FROM external_load_runs WHERE source = 'nppes' ORDER BY run_id DESC")
        ).scalar()
        snapshot = conn.execute(
            text("SELECT DISTINCT snapshot_date FROM fact_ext_provider_counts")
        ).scalar()
    assert json.loads(note)["counted"] == 3 and snapshot == "2026-09-13"


# ---------- CDC PLACES ----------

PL_COLUMNS = [
    "Year",
    "StateAbbr",
    "LocationName",
    "Data_Value_Type",
    "Data_Value",
    "Low_Confidence_Limit",
    "High_Confidence_Limit",
    "TotalPopulation",
    "LocationID",
    "MeasureId",
    "Data_Value_Footnote",
]
PL_ROWS = [
    [
        "2023",
        "TX",
        "Harris",
        "Age-adjusted prevalence",
        "20.5",
        "19.1",
        "22.0",
        "4700000",
        "48201",
        "ARTHRITIS",
        "",
    ],
    [
        "2023",
        "TX",
        "Harris",
        "Crude prevalence",
        "18.0",
        "17.0",
        "19.0",
        "4700000",
        "48201",
        "ARTHRITIS",
        "",
    ],
    [
        "2023",
        "TX",
        "Harris",
        "Age-adjusted prevalence",
        "9.9",
        "9.0",
        "10.0",
        "4700000",
        "48201",
        "OBESITY",
        "",
    ],
    [
        "2022",
        "WY",
        "Teton",
        "Age-adjusted prevalence",
        "15.0",
        "14.0",
        "16.0",
        "23000",
        "56039",
        "ARTHRITIS",
        "",
    ],
    [
        "2023",
        "WY",
        "Teton",
        "Age-adjusted prevalence",
        "15.5",
        "14.5",
        "16.5",
        "23100",
        "56039",
        "ARTHRITIS",
        "",
    ],
    [
        "2023",
        "AK",
        "Yakutat",
        "Age-adjusted prevalence",
        "",
        "",
        "",
        "600",
        "02282",
        "ARTHRITIS",
        "Estimate suppressed",
    ],
]


def test_places_keeps_arthritis_age_adjusted_prevalence_and_the_latest_year_per_location():
    out = clean_places(pd.DataFrame(PL_ROWS, columns=PL_COLUMNS))
    assert set(out["measure_id"]) == {"ARTHRITIS"} and set(out["value_type"]) == {
        "Age-adjusted prevalence"
    }
    assert len(out) == 3
    teton = out[out["location_id"] == "56039"].iloc[0]
    assert teton["data_year"] == 2023 and teton["prevalence_pct"] == pytest.approx(15.5)
    yakutat = out[out["location_id"] == "02282"].iloc[0]
    assert pd.isna(yakutat["prevalence_pct"]) and yakutat["footnote"] == "Estimate suppressed"
    harris = out[out["location_id"] == "48201"].iloc[0]
    assert harris["ci_low_pct"] == pytest.approx(19.1) and harris["total_population"] == 4700000


def test_places_loads_from_its_file(tmp_path, engine):
    folder = tmp_path / "CDC PLACES"
    folder.mkdir()
    path = folder / "PLACES_Local_Data_County_2025_release.csv"
    pd.DataFrame(PL_ROWS, columns=PL_COLUMNS).to_csv(path, index=False)
    _register(tmp_path, path)
    load_places(engine, tmp_path)
    load_places(engine, tmp_path)
    assert _count(engine, "fact_ext_arthritis_prevalence") == 3


# ---------- NUCC taxonomy and the specialty bridge ----------


@pytest.mark.parametrize(
    ("classification", "specialization", "expected"),
    [
        ("Anesthesiology", "", "ANESTHESIOLOGY"),
        ("Anesthesiology", "Pain Medicine", "PAIN MEDICINE"),
        ("Pain Medicine", "Interventional Pain Medicine", "PAIN MEDICINE"),
        ("Physical Medicine & Rehabilitation", "Pain Medicine", "PAIN MEDICINE"),
        ("Family Medicine", "", "FAMILY PRACTICE"),
        ("Family Medicine", "Sports Medicine", "SPORTS MEDICINE"),
        ("Orthopaedic Surgery", "Sports Medicine", "SPORTS MEDICINE"),
        ("Internal Medicine", "", "INTERNAL MEDICINE"),
        ("Internal Medicine", "Rheumatology", "RHEUMATOLOGY"),
        ("Internal Medicine", "Cardiovascular Disease", "OTHER"),
        ("Nurse Practitioner", "Family", "NURSE PRACTITIONER"),
        ("Physician Assistant", "Surgical", "PHYSICIAN ASSISTANT"),
        ("Orthopaedic Surgery", "", "ORTHOPEDIC SURGERY"),
        ("Orthopaedic Surgery", "Hand Surgery", "ORTHOPEDIC SURGERY"),
        ("Orthopaedic Surgery", "Orthopaedic Surgery of the Spine", "OTHER"),
        ("Neuromusculoskeletal Medicine & OMM", "", "OSTEOPATHIC MEDICINE"),
        ("Physical Medicine & Rehabilitation", "", "PHYSICAL MEDICINE & REHAB"),
        (
            "Physical Medicine & Rehabilitation",
            "Brain Injury Medicine",
            "PHYSICAL MEDICINE & REHAB",
        ),
        ("Podiatrist", "Sports Medicine", "OTHER"),
        ("Dentist", "", "OTHER"),
    ],
)
def test_taxonomy_codes_map_to_the_approved_specialty_groups(
    classification, specialization, expected
):
    assert classify_taxonomy(classification, specialization) == expected


def test_the_taxonomy_bridge_has_one_row_per_code_and_names_the_medicare_specialty():
    nucc = pd.DataFrame(
        {
            "Code": ["207L00000X", "208VP0014X", "208VP0000X", "207XS0117X"],
            "Grouping": ["Allopathic & Osteopathic Physicians"] * 4,
            "Classification": [
                "Anesthesiology",
                "Pain Medicine",
                "Pain Medicine",
                "Orthopaedic Surgery",
            ],
            "Specialization": [
                "",
                "Interventional Pain Medicine",
                "Pain Medicine",
                "Orthopaedic Surgery of the Spine",
            ],
            "Display Name": ["a", "b", "c", "d"],
        }
    )
    bridge = taxonomy_bridge(nucc).set_index("taxonomy_code")
    assert bridge.loc["207L00000X", "specialty_group"] == "ANESTHESIOLOGY"
    assert bridge.loc["207L00000X", "medicare_name"] == "Anesthesiology"
    assert bridge.loc["208VP0014X", "medicare_name"] == "Interventional Pain Management"
    assert bridge.loc["208VP0000X", "medicare_name"] == "Pain Management"
    assert bridge.loc["207XS0117X", "specialty_group"] == "OTHER" and pd.isna(
        bridge.loc["207XS0117X", "medicare_name"]
    )


def test_nucc_loads_the_local_text_table_and_the_bridge(tmp_path, engine):
    folder = tmp_path / "NUCC Taxonomy"
    folder.mkdir()
    path = folder / "nucc_taxonomy_261.csv"
    pd.DataFrame(
        {
            "Code": ["207L00000X", "207Q00000X"],
            "Grouping": ["g", "g"],
            "Classification": ["Anesthesiology", "Family Medicine"],
            "Specialization": ["", ""],
            "Definition": ["long text", "long text"],
            "Notes": ["", ""],
            "Display Name": ["a", "b"],
            "Section": ["Individual", "Individual"],
        }
    ).to_csv(path, index=False)
    _register(tmp_path, path)
    load_nucc(engine, tmp_path)
    load_nucc(engine, tmp_path)
    assert (
        _count(engine, "src_nucc_taxonomy") == 2
        and _count(engine, "bridge_taxonomy_specialty") == 2
    )
