"""Tests for the 4b loaders: Part B geography, Part D geography, price files, Geographic Variation.

Each loader is exercised on tiny files shaped like the real ones, one rule at a time.
"""

import json
import zipfile

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.codes import classify_partd_drug
from oa_market_intelligence.external.common import ChecksumMismatch, read_manifest
from oa_market_intelligence.external.loaders.asp import (
    clean_asp_table,
    load_asp,
    parse_asp_text,
    source_release,
)
from oa_market_intelligence.external.loaders.geovar import clean_geovar, load_geovar
from oa_market_intelligence.external.loaders.partb_geo import clean_partb_geo, load_partb_geo
from oa_market_intelligence.external.loaders.partd_geo import clean_partd_geo, load_partd_geo
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


def _register(raw, path, dataset):
    """Write a manifest entry for a file, as the downloader does."""
    entry = {
        "dataset": dataset,
        "file": str(path.relative_to(raw)).replace("/", "\\"),  # the real manifest mixes styles
        "bytes": path.stat().st_size,
        "sha256": sha256_of(path),
        "status": "downloaded",
    }
    with open(raw / "_download_manifest.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


# ---------- manifest paths ----------


def test_manifest_paths_are_normalised_to_forward_slashes(tmp_path):
    (tmp_path / "_download_manifest.jsonl").write_text(
        json.dumps({"dataset": "d", "file": "a\\b\\c.csv"}), encoding="utf-8"
    )
    assert read_manifest(tmp_path)[0]["file"] == "a/b/c.csv"


# ---------- Part B geography ----------

GEO_COLUMNS = [
    "Rndrng_Prvdr_Geo_Lvl",
    "Rndrng_Prvdr_Geo_Cd",
    "Rndrng_Prvdr_Geo_Desc",
    "HCPCS_Cd",
    "HCPCS_Desc",
    "HCPCS_Drug_Ind",
    "Place_Of_Srvc",
    "Tot_Rndrng_Prvdrs",
    "Tot_Benes",
    "Tot_Srvcs",
    "Tot_Bene_Day_Srvcs",
    "Avg_Sbmtd_Chrg",
    "Avg_Mdcr_Alowd_Amt",
    "Avg_Mdcr_Pymt_Amt",
    "Avg_Mdcr_Stdzd_Amt",
]


def _geo_frame(rows):
    return pd.DataFrame(rows, columns=GEO_COLUMNS, dtype=str)


GEO_ROWS = [
    [
        "National",
        "",
        "National",
        "J3304",
        "Zilretta",
        "Y",
        "O",
        "4878",
        "47150",
        "3431291.6",
        "82919",
        "47.28",
        "17.26",
        "13.6",
        "13.66",
    ],
    [
        "State",
        "48",
        "Texas",
        "J3304",
        "Zilretta",
        "Y",
        "F",
        "5",
        "11",
        "30",
        "11",
        "1",
        "2",
        "3",
        "4",
    ],
    ["State", "", "", "J3301", "Kenalog", "Y", "O", "20", "300", "900", "310", "1", "2", "3", "4"],
    ["State", "48", "Texas", "99213", "Visit", "N", "O", "9", "9", "9", "9", "1", "2", "3", "4"],
    [
        "State",
        "01",
        "Alabama",
        "20610",
        "AMA text",
        "N",
        "O",
        "30",
        "400",
        "800",
        "420",
        "1",
        "2",
        "3",
        "4",
    ],
]


def test_part_b_geography_keeps_only_approved_codes_and_maps_geography_codes():
    out = clean_partb_geo(_geo_frame(GEO_ROWS), 2024)
    assert set(out["hcpcs_code"]) == {"J3304", "J3301", "20610"}  # 99213 is not approved
    by = out.set_index(["hcpcs_code", "setting"])
    assert (
        by.loc[("J3304", "O"), "geo_code"] == "US"
        and by.loc[("J3304", "O"), "geo_level"] == "National"
    )
    assert by.loc[("J3301", "O"), "geo_code"] == "UNKNOWN"  # the blank-code placeholder state row
    assert by.loc[("J3304", "F"), "geo_code"] == "48"


def test_part_b_geography_casts_numbers_and_renames_columns():
    out = clean_partb_geo(_geo_frame(GEO_ROWS), 2024).set_index(["hcpcs_code", "setting"])
    row = out.loc[("J3304", "O")]
    assert row["year"] == 2024 and row["n_providers"] == 4878 and row["benes"] == 47150
    assert row["services"] == pytest.approx(3431291.6) and row["avg_payment_amt"] == pytest.approx(
        13.6
    )
    assert row["drug_indicator"] == "Y"


def _geo_file(tmp_path, year=2024, rows=GEO_ROWS):
    folder = (
        tmp_path / "Medicare Physician & Other Practitioners - by Geography and Service" / str(year)
    )
    folder.mkdir(parents=True)
    path = folder / f"MUP_PHY_R26_P05_V10_D{year % 100}_Geo.csv"
    _geo_frame(rows).to_csv(path, index=False)
    _register(tmp_path, path, "Part B geography")
    return path


def test_part_b_geography_loads_idempotently_logs_the_run_and_marks_the_file(tmp_path, engine):
    _geo_file(tmp_path)
    load_partb_geo(engine, tmp_path)
    load_partb_geo(engine, tmp_path)
    assert _count(engine, "fact_ext_partb_geo") == 4
    with engine.connect() as conn:
        run = conn.execute(
            text(
                "SELECT source, data_year, rows_read, rows_loaded, status "
                "FROM external_load_runs ORDER BY run_id DESC"
            )
        ).first()
    assert tuple(run) == ("partb_geo", 2024, 5, 4, "ok")


def test_a_changed_raw_file_is_refused_before_anything_is_loaded(tmp_path, engine):
    path = _geo_file(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ChecksumMismatch):
        load_partb_geo(engine, tmp_path)
    assert _count(engine, "fact_ext_partb_geo") == 0


# ---------- Part D geography: the fixed drug lists ----------


@pytest.mark.parametrize(
    ("generic", "expected"),
    [
        ("Meloxicam", "nsaid"),
        ("Meloxicam, Submicronized", "nsaid"),
        ("Naproxen Sodium", "nsaid"),
        ("Diclofenac Sodium", "nsaid"),
        ("Ketorolac Tromethamine", "nsaid"),
        ("Diclofenac Epolamine", None),  # a patch
        ("Diclofenac Sodium/Misoprostol", None),  # combination
        ("Ibuprofen/Famotidine", None),
        ("Ketorolac Tromethamine/Pf", None),
        ("Flurbiprofen Sodium", None),  # eye drops
        ("Prednisone", "oral_steroid"),
        ("Dexamethasone", "oral_steroid"),
        ("Methylprednisolone", "oral_steroid"),
        ("Prednisolone", "oral_steroid"),
        ("Prednisolone Acetate", None),
        ("Hydrocortisone", None),
        ("Methylprednisolone Acetate", None),
        ("Tobramycin/Dexamethasone", None),
        ("Acetaminophen", None),
    ],
)
def test_the_fixed_part_d_drug_lists(generic, expected):
    assert classify_partd_drug(generic) == expected


DRUG_COLUMNS = [
    "Prscrbr_Geo_Lvl",
    "Prscrbr_Geo_Cd",
    "Prscrbr_Geo_Desc",
    "Brnd_Name",
    "Gnrc_Name",
    "Tot_Prscrbrs",
    "Tot_Clms",
    "Tot_30day_Fills",
    "Tot_Drug_Cst",
    "Tot_Benes",
    "GE65_Sprsn_Flag",
    "GE65_Tot_Clms",
    "GE65_Tot_30day_Fills",
    "GE65_Tot_Drug_Cst",
    "GE65_Bene_Sprsn_Flag",
    "GE65_Tot_Benes",
]
DRUG_ROWS = [
    [
        "National",
        "",
        "National",
        "Mobic",
        "Meloxicam",
        "1000",
        "5000",
        "5200.5",
        "90000.25",
        "3000",
        "",
        "4000",
        "4100",
        "70000",
        "",
        "2500",
    ],
    [
        "State",
        "48",
        "Texas",
        "Mobic",
        "Meloxicam",
        "90",
        "400",
        "410",
        "7000",
        "",
        "*",
        "",
        "",
        "",
        "*",
        "",
    ],
    [
        "State",
        "",
        "",
        "Deltasone",
        "Prednisone",
        "5",
        "20",
        "21",
        "100",
        "12",
        "",
        "15",
        "16",
        "80",
        "",
        "11",
    ],
    [
        "State",
        "48",
        "Texas",
        "Tylenol",
        "Acetaminophen",
        "9",
        "9",
        "9",
        "9",
        "9",
        "",
        "9",
        "9",
        "9",
        "",
        "9",
    ],
]


def test_part_d_geography_keeps_only_listed_drugs_and_turns_suppressed_cells_into_missing():
    out = clean_partd_geo(pd.DataFrame(DRUG_ROWS, columns=DRUG_COLUMNS, dtype=str), 2024)
    assert set(out["generic_name"]) == {"Meloxicam", "Prednisone"}
    texas = out[(out["geo_code"] == "48")].iloc[0]
    assert pd.isna(texas["benes"]) and pd.isna(
        texas["ge65_claims"]
    )  # blank means suppressed, not zero
    assert texas["ge65_suppression_flag"] == "*"
    national = out[out["geo_level"] == "National"].iloc[0]
    assert national["geo_code"] == "US" and national["drug_family"] == "nsaid"
    assert national["claims"] == 5000 and national["total_drug_cost"] == pytest.approx(90000.25)
    assert out[out["generic_name"] == "Prednisone"].iloc[0]["geo_code"] == "UNKNOWN"


def test_part_d_geography_loads_from_a_file(tmp_path, engine):
    folder = tmp_path / "Medicare Part D Prescribers - by Geography and Drug" / "2024"
    folder.mkdir(parents=True)
    path = folder / "MUP_DPR_RY26_P04_V10_DY24_Geo.csv"
    pd.DataFrame(DRUG_ROWS, columns=DRUG_COLUMNS).to_csv(path, index=False)
    _register(tmp_path, path, "Part D geography")
    load_partd_geo(engine, tmp_path)
    assert _count(engine, "fact_ext_partd_geo") == 3


# ---------- price files ----------

OLD_LAYOUT = (
    "Payment Allowance Limits for Medicare Part B Drugs,,,,,,,,,\n,,,,,,,,,\n"
    "Note 1: text,,,,,,,,,\n"
    "HCPCS Code,Short Description,HCPCS Code Dosage,Payment Limit,Vaccine AWP%,Vaccine Limit,"
    "Blood AWP%,Blood limit,Clotting Factor,Notes\n"
    "90371,Hep b ig im,1 ML,138.873,,,,,,\n"
    "J3301,Triamcinolone acet inj nos,10 MG,1.291,,,,,,\n"
    "J3304,Inj triamcinolone ace xr 1mg,1 MG,16.989,,,,,,\n"
    "J7318,Hyaluronan durolane,1 MG,,,,,,,see note\n"
)
NEW_LAYOUT = (
    "Title,,,,,,,,,,,\n,,,,,,,,,,,\nNote,,,,,,,,,,,\n,,,,,,,,,,,\n"
    "HCPCS Code,Short Description,HCPCS Code Dosage,Payment Limit,Co-insurance Percentage,"
    "Vaccine AWP%,Vaccine Limit,Blood AWP%,Blood limit,Clotting Factor,Notes,\n"
    "J3301,Triamcinolone acet inj nos,10 MG,0.787,20.000,,,,,,,\n"
    "J3304,Inj triamcinolone ace xr 1mg,1 MG,19.159,20.000,,,,,,,\n"
)


def test_the_price_table_is_read_by_column_name_in_either_layout():
    old = clean_asp_table(parse_asp_text(OLD_LAYOUT), "2022Q3", "f.zip", None)
    new = clean_asp_table(parse_asp_text(NEW_LAYOUT), "2024Q1", "g.zip", None)
    assert set(old["hcpcs_code"]) == {"J3301", "J3304", "J7318"}  # 90371 is not an approved code
    assert set(new["hcpcs_code"]) == {"J3301", "J3304"}
    z = old.set_index("hcpcs_code")
    assert (
        z.loc["J3304", "payment_limit"] == pytest.approx(16.989)
        and z.loc["J3304", "dosage"] == "1 MG"
    )
    assert pd.isna(z.loc["J7318", "payment_limit"]) and z.loc["J7318", "notes"] == "see note"
    assert old["coinsurance_pct"].isna().all()  # the column did not exist yet
    assert new.set_index("hcpcs_code").loc["J3304", "coinsurance_pct"] == pytest.approx(20.0)


def test_the_release_date_is_taken_from_the_file_name_when_present():
    assert source_release("jul_2019_asp_pricing_file_updated_052920.zip") == "052920"
    assert source_release("january-2021-asp-pricing-file.zip") is None


def test_price_files_load_one_quarter_each_and_replace_by_quarter(tmp_path, engine):
    folder = tmp_path / "ASP Pricing Files" / "2022"
    folder.mkdir(parents=True)
    path = folder / "july-2022-asp-pricing-file.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "section 508 version of July 2022 ASP Pricing File updated 060223.csv", OLD_LAYOUT
        )
        z.writestr("July 2022 ASP Pricing File updated 060223.xls", "binary")
    _register(tmp_path, path, "ASP Pricing Files")
    load_asp(engine, tmp_path)
    load_asp(engine, tmp_path)
    assert _count(engine, "fact_ext_asp_price") == 3
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT DISTINCT quarter_id FROM fact_ext_asp_price")).scalar()
            == "2022Q3"
        )


# ---------- Geographic Variation ----------

GV_COLUMNS = [
    "YEAR",
    "BENE_GEO_LVL",
    "BENE_GEO_DESC",
    "BENE_GEO_CD",
    "BENE_AGE_LVL",
    "BENES_TOTAL_CNT",
    "BENES_WTH_PTAPTB_CNT",
    "BENES_OM_CNT",
    "BENES_MA_CNT",
    "MA_PRTCPTN_RATE",
    "BENE_AVG_AGE",
]
GV_ROWS = [
    [
        "2022",
        "National",
        "National",
        "",
        "All",
        "60000000",
        "33000000",
        "34000000",
        "26000000",
        "0.4301",
        "71.2",
    ],
    [
        "2022",
        "State",
        "TX",
        "48",
        "All",
        "3000000",
        "1500000",
        "1600000",
        "1400000",
        "0.45",
        "70.1",
    ],
    [
        "2022",
        "State",
        "TX",
        "48",
        ">=65",
        "2500000",
        "1300000",
        "1350000",
        "1150000",
        "0.46",
        "73.0",
    ],
    ["2022", "State", "Territory", "", "All", "*", "*", "*", "*", "*", "*"],
    ["2022", "State", "ZZ", "", "All", "*", "*", "*", "*", "*", "*"],
    [
        "2022",
        "County",
        "Harris",
        "48201",
        "All",
        "500000",
        "250000",
        "260000",
        "240000",
        "0.48",
        "69.0",
    ],
    ["2022", "State", "WY", "56", "All", "*", "*", "*", "*", "*", "*"],
]


def test_geographic_variation_drops_placeholders_and_counties_and_nulls_the_asterisks():
    out = clean_geovar(pd.DataFrame(GV_ROWS, columns=GV_COLUMNS, dtype=str))
    assert set(out["geo_level"]) == {"National", "State"}  # no counties
    assert "Territory" not in set(out["geo_desc"]) and "ZZ" not in set(out["geo_desc"])
    assert len(out) == 4
    national = out[out["geo_level"] == "National"].iloc[0]
    assert national["geo_code"] == "US" and national["ma_participation_rate"] == pytest.approx(
        0.4301
    )
    wyoming = out[out["geo_code"] == "56"].iloc[0]
    assert pd.isna(wyoming["ma_participation_rate"]) and pd.isna(wyoming["benes_total"])
    tx = out[(out["geo_code"] == "48") & (out["age_level"] == ">=65")].iloc[0]
    assert tx["benes_original_medicare"] == 1350000 and tx["avg_age"] == pytest.approx(73.0)


def test_geographic_variation_loads_from_its_file(tmp_path, engine):
    folder = tmp_path / "Medicare Geographic Variation"
    folder.mkdir()
    path = folder / "2014-2024 Original Medicare Geographic Variation Public Use File.csv"
    pd.DataFrame(GV_ROWS, columns=GV_COLUMNS).to_csv(path, index=False)
    _register(tmp_path, path, "Medicare Geographic Variation")
    load_geovar(engine, tmp_path)
    load_geovar(engine, tmp_path)
    assert _count(engine, "fact_ext_geo_variation") == 4
