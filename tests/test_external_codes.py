"""The approved code sets are fixed before analysis; these tests pin them."""

import re

from oa_market_intelligence.external.codes import (
    ALL_CODES,
    CODE_GROUPS,
    DRUG_FAMILY,
    GROUP_OF,
    HYALURONIC_PRODUCT_PATTERN,
    NSAID_NAME_PATTERN,
    ZILRETTA_CODE,
    ZILRETTA_PATTERN,
)


def test_the_approved_list_has_27_distinct_codes_in_four_groups():
    assert len(ALL_CODES) == 27 and len(set(ALL_CODES)) == 27
    assert set(CODE_GROUPS) == {
        "A_primary",
        "B_context_hyaluronic",
        "C_denominator_procedures",
        "D_sensitivity_iv_steroids",
    }
    assert [len(v) for v in CODE_GROUPS.values()] == [8, 13, 2, 4]


def test_zilretta_is_in_the_primary_set_and_hyaluronic_acid_never_is():
    assert ZILRETTA_CODE in CODE_GROUPS["A_primary"]
    assert not set(CODE_GROUPS["A_primary"]) & set(CODE_GROUPS["B_context_hyaluronic"])
    assert GROUP_OF["J7318"] == "B_context_hyaluronic"


def test_methylprednisolone_acetate_is_one_family_across_its_four_codes():
    codes = [c for c, f in DRUG_FAMILY.items() if f == "methylprednisolone_acetate"]
    assert sorted(codes) == ["J1010", "J1020", "J1030", "J1040"]


def test_every_code_has_a_drug_family():
    assert set(DRUG_FAMILY) == set(ALL_CODES)


def test_name_patterns_catch_the_known_spellings_and_not_unrelated_names():
    assert re.search(ZILRETTA_PATTERN, "ZILRETTA", re.I)
    assert re.search(HYALURONIC_PRODUCT_PATTERN, "Gel-One", re.I)
    assert re.search(HYALURONIC_PRODUCT_PATTERN, "GelOne", re.I)
    assert re.search(NSAID_NAME_PATTERN, "MELOXICAM", re.I)
    assert not re.search(NSAID_NAME_PATTERN, "Acetaminophen", re.I)
