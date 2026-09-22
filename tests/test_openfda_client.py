"""Tests for the openFDA client.

Parsing logic is tested against captured response shapes (no network — see the
docstrings for where each shape came from). A small number of tests hit the real
openFDA API, the same standard this project holds its other loaders to: verify
against the real source, not an assumed shape.
"""

from datetime import date
from pathlib import Path

import pytest

from oa_market_intelligence.ingestion.openfda_client import (
    OpenFDAError,
    branded_products,
    earliest_approval_date,
)
from oa_market_intelligence.ingestion.reference_loader import parse_reference_table

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def _fake_fetch(payload: dict):
    return lambda brand_name: payload


# ---------- parsing logic, no network ----------


def test_single_application_single_orig_submission():
    # Real shape for ZILRETTA, captured live 2026-09-21.
    payload = {
        "results": [
            {
                "application_number": "NDA208845",
                "sponsor_name": "PACIRA PHARMS INC",
                "products": [{"brand_name": "ZILRETTA"}],
                "submissions": [
                    {
                        "submission_number": "1",
                        "submission_type": "ORIG",
                        "submission_status_date": "20171006",
                    }
                ],
            }
        ]
    }
    assert earliest_approval_date("ZILRETTA", fetch=_fake_fetch(payload)) == date(2017, 10, 6)


def test_takes_the_earliest_orig_date_across_several_applications():
    # Shape for KENALOG, captured live: 5 applications, one with no submissions field.
    payload = {
        "results": [
            {
                "application_number": "ANDA083943",
                "products": [{"brand_name": "KENALOG"}],
                "submissions": [{"submission_type": "ORIG", "submission_status_date": "19740129"}],
            },
            {
                "application_number": "NDA011602",
                "products": [{"brand_name": "KENALOG"}],
                # No "submissions" key at all — real openFDA applications can omit it.
            },
            {
                "application_number": "NDA012104",
                "products": [{"brand_name": "KENALOG"}],
                "submissions": [{"submission_type": "ORIG", "submission_status_date": "19741011"}],
            },
            {
                "application_number": "ANDA084343",
                "products": [{"brand_name": "KENALOG"}],
                "submissions": [{"submission_type": "ORIG", "submission_status_date": "19740716"}],
            },
        ]
    }
    assert earliest_approval_date("KENALOG", fetch=_fake_fetch(payload)) == date(1974, 1, 29)


def test_ignores_an_application_whose_products_do_not_include_this_brand():
    payload = {
        "results": [
            {
                "products": [{"brand_name": "SOME OTHER DRUG"}],
                "submissions": [{"submission_type": "ORIG", "submission_status_date": "19990101"}],
            }
        ]
    }
    assert earliest_approval_date("ZILRETTA", fetch=_fake_fetch(payload)) is None


def test_ignores_non_orig_submissions():
    payload = {
        "results": [
            {
                "products": [{"brand_name": "ZILRETTA"}],
                "submissions": [
                    {"submission_type": "SUPPL", "submission_status_date": "20150101"},
                    {"submission_type": "ORIG", "submission_status_date": "20171006"},
                ],
            }
        ]
    }
    assert earliest_approval_date("ZILRETTA", fetch=_fake_fetch(payload)) == date(2017, 10, 6)


def test_matches_brand_name_case_insensitively():
    payload = {
        "results": [
            {
                "products": [{"brand_name": "Zilretta"}],
                "submissions": [{"submission_type": "ORIG", "submission_status_date": "20171006"}],
            }
        ]
    }
    assert earliest_approval_date("ZILRETTA", fetch=_fake_fetch(payload)) == date(2017, 10, 6)


def test_no_matches_returns_none_not_an_error():
    # Real shape of the client's own 404-to-empty-results translation.
    assert earliest_approval_date("NOT-A-REAL-DRUG-XYZ", fetch=_fake_fetch({"results": []})) is None


def test_a_submission_with_no_date_is_skipped():
    payload = {
        "results": [
            {
                "products": [{"brand_name": "ZILRETTA"}],
                "submissions": [
                    {"submission_type": "ORIG", "submission_status_date": None},
                    {"submission_type": "ORIG", "submission_status_date": "20171006"},
                ],
            }
        ]
    }
    assert earliest_approval_date("ZILRETTA", fetch=_fake_fetch(payload)) == date(2017, 10, 6)


def test_an_unparseable_date_raises_rather_than_silently_dropping():
    payload = {
        "results": [
            {
                "products": [{"brand_name": "ZILRETTA"}],
                "submissions": [
                    {"submission_type": "ORIG", "submission_status_date": "not-a-date"}
                ],
            }
        ]
    }
    with pytest.raises(OpenFDAError, match="Unrecognized submission_status_date"):
        earliest_approval_date("ZILRETTA", fetch=_fake_fetch(payload))


# ---------- scoping: which products get looked up ----------


def test_branded_products_scopes_to_brand_and_branded_generic_tags():
    oa = parse_reference_table(RAW / "Branded Generic - OA.xlsx")
    ra = parse_reference_table(RAW / "Branded Generic - RA.xlsx")
    import pandas as pd

    products = branded_products(pd.concat([oa, ra]))
    assert len(products) == 87
    assert "ZILRETTA" in products
    assert "KENALOG" in products
    # Tagged GENERIC/OTHER only — must not appear even though they're OA products.
    assert "TRIAMCINOLONE ACTN" not in products
    assert "ASPIRIN" not in products


def test_branded_products_includes_a_product_tagged_branded_on_only_one_row():
    # HYDROCORTISONE is BRAND on one row and GENERIC on another (data_dictionary.md §4).
    oa = parse_reference_table(RAW / "Branded Generic - OA.xlsx")
    assert "HYDROCORTISONE" in branded_products(oa)


# ---------- live openFDA (network required) ----------


def test_zilretta_approval_date_matches_the_verified_ground_truth():
    """Cross-checks the openFDA finding already recorded in PROPOSAL.md §18.5."""
    assert earliest_approval_date("ZILRETTA") == date(2017, 10, 6)


def test_kenalog_has_a_real_multi_application_history():
    """Confirms the multi-application path (test above) against the live API, not just a
    captured fixture — same 1974-01-29 date found by direct inspection while building this
    client."""
    assert earliest_approval_date("KENALOG") == date(1974, 1, 29)
