"""Tests for the pure helpers of the raw-data profiling runner."""

import pandas as pd
import pytest

from oa_market_intelligence.external.profile_all import (
    asp_quarter,
    crosswalk_candidates,
    find_asp_header,
    matching_names,
    normalise_specialty,
    observe,
    sec_period,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("january-2021-asp-pricing-file.zip", "2021Q1"),
        ("April_2020_ASP_Pricing_File_updated_022421.zip", "2020Q2"),
        ("jul_2019_asp_pricing_file_updated_052920.zip", "2019Q3"),
        ("october-2025-asp-pricing-final-file.zip", "2025Q4"),
        ("january-2025-asp-pricing-file-03-11-25-final-file.zip", "2025Q1"),
        ("notes.zip", None),
    ],
)
def test_asp_file_names_map_to_a_quarter(name, expected):
    assert asp_quarter(name) == expected


def test_the_asp_header_is_found_below_any_number_of_title_lines():
    lines = [
        "Title line,,",
        "",
        "HCPCS Code,Short Description,HCPCS Code Dosage,Payment Limit",
        "J3304,x,1 MG,1.0",
    ]
    assert find_asp_header(lines) == 2
    assert find_asp_header(["no header here"]) is None


def test_the_sec_period_is_read_from_the_cover_page_through_markup():
    html = "<p>For the quarterly period ended <b>September&#160;30,</b> 2021</p>".replace(
        "&#160;", " "
    )
    assert sec_period(html) == "September 30, 2021"
    annual = "<span>For the fiscal year ended</span> <span>December 31, 2020</span>"
    assert sec_period(annual) == "December 31, 2020"
    assert sec_period("nothing") is None


def test_specialty_names_are_normalised_for_comparison():
    assert normalise_specialty("Physical Medicine & Rehab") == "PHYSICAL MEDICINE AND REHAB"
    assert normalise_specialty("Orthopedic Surgery ") == "ORTHOPEDIC SURGERY"


def test_crosswalk_candidates_flag_exact_matches_and_suggest_near_ones_for_review():
    rows = crosswalk_candidates(
        ["PHYSICAL MEDICINE & REHAB", "RHEUMATOLOGY", "RARE (grouped)"],
        ["Physical Medicine and Rehabilitation", "Rheumatology", "Anesthesiology"],
    )
    by_name = {r["left_name"]: r for r in rows}
    assert by_name["RHEUMATOLOGY"]["exact_match"] is True
    assert (
        by_name["PHYSICAL MEDICINE & REHAB"]["suggestion_1"]
        == "Physical Medicine and Rehabilitation"
    )
    assert by_name["PHYSICAL MEDICINE & REHAB"]["exact_match"] is False
    assert all(r["decision"] == "" for r in rows)  # decisions are made by a person, not the code


def test_matching_names_is_case_insensitive_and_ignores_empties():
    series = pd.Series(["ZILRETTA", "Kenalog-40", "", "  ", "Tylenol", "zilretta"])
    hits = matching_names(series, "zilretta|kenalog")
    assert list(hits) == ["ZILRETTA", "Kenalog-40", "zilretta"]


def test_observe_passes_chunks_through_unchanged_while_the_callback_sees_each():
    chunks = [pd.DataFrame({"a": ["1"]}), pd.DataFrame({"a": ["2", "3"]})]
    seen = []
    out = list(observe(iter(chunks), lambda c: seen.append(len(c))))
    assert [len(c) for c in out] == [1, 2] and seen == [1, 2]


# ---------- the two optional files: Part D by provider and Medicaid drug utilisation ----------

import json  # noqa: E402

from oa_market_intelligence.external import profile_all  # noqa: E402
from oa_market_intelligence.external.profile_all import (  # noqa: E402
    DuplicateCounter,
    is_ndc11,
    profile_partd_provider,
    profile_sdud,
)


def test_duplicates_are_counted_across_chunks():
    counter = DuplicateCounter()
    counter.add(pd.DataFrame({"a": ["1", "2"], "b": ["x", "y"]}))
    counter.add(pd.DataFrame({"a": ["2", "3"], "b": ["y", "z"]}))  # ("2", "y") repeats
    counter.add(pd.DataFrame({"a": ["3"], "b": ["z"]}))  # ("3", "z") repeats
    assert counter.duplicates() == 2
    assert DuplicateCounter().duplicates() == 0


def test_a_drug_code_must_be_eleven_digits_with_leading_zeros_kept():
    codes = pd.Series(["00002143380", "2143380", "0000214338A", "", "123456789012"])
    assert list(is_ndc11(codes)) == [True, False, False, False, False]


PARTD_HEADER = (
    "Prscrbr_NPI,Prscrbr_Last_Org_Name,Prscrbr_First_Name,Prscrbr_City,Prscrbr_State_Abrvtn,"
    "Prscrbr_State_FIPS,Prscrbr_Type,Prscrbr_Type_Src,Brnd_Name,Gnrc_Name,Tot_Clms,Tot_30day_Fills,"
    "Tot_Day_Suply,Tot_Drug_Cst,Tot_Benes,GE65_Sprsn_Flag,GE65_Tot_Clms,GE65_Tot_30day_Fills,"
    "GE65_Tot_Drug_Cst,GE65_Tot_Day_Suply,GE65_Bene_Sprsn_Flag,GE65_Tot_Benes\n"
).replace("\n", "\n")


def _partd_tree(tmp_path, rows):
    folder = (
        tmp_path
        / "raw"
        / "Medicare Part D Prescribers - by Provider and Drug"
        / "Medicare Part D Prescribers - by Provider and Drug"
        / "2024"
    )
    folder.mkdir(parents=True)
    (folder / "MUP_DPR_RY26_P04_V10_DY24_NPIBN.csv").write_text(
        PARTD_HEADER + "\n".join(rows) + "\n", encoding="utf-8"
    )
    return tmp_path / "raw"


def _partd_row(npi, brand, generic, kind="Internal Medicine", clms="20"):
    return (
        f"{npi},Secretname,Firstsecret,Hiddencity,MD,24,{kind},Claim-Specialty,{brand},{generic},"
        f"{clms},20,20,5994.01,20,,20,20,5994.01,20,,20"
    )


def test_partd_provider_profile_is_structure_only_and_never_lists_names(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_all, "CHUNK", 2)  # forces a repeated key across chunk boundaries
    raw = _partd_tree(
        tmp_path,
        [
            _partd_row("1001", "Celebrex", "Celecoxib"),
            _partd_row("1001", "Ibuprofen", "Ibuprofen"),
            _partd_row("1002", "Celebrex", "Celecoxib", kind="Anesthesiology"),
            _partd_row("1001", "Celebrex", "Celecoxib"),  # same key as row 1, in the next chunk
            _partd_row("1003", "Prednisone", "Prednisone"),
            _partd_row("1004", "Zilretta", "Triamcinolone Acetonide"),
        ],
    )
    out = tmp_path / "out"
    profile_partd_provider(raw, out)
    text = (out / "partd_provider.json").read_text(encoding="utf-8")
    profile = json.loads(text)["2024"]
    assert profile["n_rows"] == 6 and profile["n_distinct_prescribers"] == 4
    assert profile["n_distinct_drug_names"] == 4 and profile["duplicate_keys"] == 1
    assert profile["n_nsaid_name_pairs"] == 2 and profile["n_oral_steroid_name_pairs"] == 1
    assert profile["zilretta_name_matches"] == 1  # the name is recorded, with no volumes
    assert profile["zilretta_name_pairs"] == [["Zilretta", "Triamcinolone Acetonide"]]
    for secret in ("Secretname", "Firstsecret", "Hiddencity", "1001", "1002", "1004"):
        assert secret not in text
    assert "Hiddencity" not in (out / "partd_provider_prescriber_types.csv").read_text("utf-8")
    types = pd.read_csv(out / "partd_provider_prescriber_types.csv").set_index("prescriber_type")
    assert types.loc["Internal Medicine", "n_rows"] == 5
    assert profile["columns"]["Tot_Clms"]["min"] == 20.0


SDUD_HEADER = (
    "Utilization Type,State,NDC,Labeler Code,Product Code,Package Size,Year,Quarter,"
    "Suppression Used,Product Name,Units Reimbursed,Number of Prescriptions,"
    "Total Amount Reimbursed,Medicaid Amount Reimbursed,Non Medicaid Amount Reimbursed\n"
)


def test_sdud_profile_records_structure_and_names_but_no_volumes(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_all, "CHUNK", 2)
    rows = [
        "FFSU,AK,00002143380,00002,1433,80,2024,4,false,ZILRETTA ,00000000226.000,000000108,"
        "000000106607.76,0000103963.72,0000002644.04",
        "FFSU,AK,00002143611,00002,1436,11,2024,4,false,KENALOG-40,00000000032.000,000000031,"
        "000000022193.94,0000022193.94,0000000000.00",
        "FFSU,AK,00002144509,00002,1445,09,2024,4,true,SYNVISC ,,,,,",
        "MCOU,AK,123,00002,1445,09,2024,4,false,OTHER,00000000001.000,000000001,"
        "000000000001.00,0000000001.00,0000000000.00",
        "FFSU,AK,00002143380,00002,1433,80,2024,4,false,ZILRETTA ,00000000226.000,000000108,"
        "000000106607.76,0000103963.72,0000002644.04",
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "sdud2024_test.csv").write_text(SDUD_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    profile_sdud(raw, out)
    text = (out / "medicaid_sdud.json").read_text(encoding="utf-8")
    profile = json.loads(text)["2024"]
    assert profile["n_rows"] == 5 and profile["n_distinct_ndc"] == 4
    assert profile["n_ndc_not_eleven_digits"] == 1  # "123"
    assert profile["duplicate_keys"] == 1
    assert profile["columns"]["Suppression Used"]["top_values"] == {"false": 4, "true": 1}
    assert profile["columns"]["Units Reimbursed"]["n_empty"] == 1  # the suppressed row
    assert profile["columns"]["Units Reimbursed"]["max"] == 226.0  # zero-padded text parses
    matches = profile["matching_product_names"]
    assert matches["zilretta"] == ["ZILRETTA"] and matches["hyaluronic"] == ["SYNVISC"]
    assert "KENALOG-40" in matches["steroid"]
    assert all(isinstance(names, list) for names in matches.values())  # names only, no numbers
    assert "ZILRETTA" not in json.dumps(profile["columns"])  # no per-product statistics
