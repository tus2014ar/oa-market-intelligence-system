"""The billing-code sets approved for the Medicare external data (DL-59).

Fixed before any analysis. A: primary set, the Medicare counterpart of the IQVIA competitive set.
B: hyaluronic-acid knee injectables, context only, never part of the share. C: joint-injection
procedure codes, a denominator. D: IV steroids, a sensitivity set.
"""

from __future__ import annotations

CODE_GROUPS: dict[str, tuple[str, ...]] = {
    "A_primary": ("J3304", "J3301", "J1010", "J1020", "J1030", "J1040", "J0702", "J1100"),
    "B_context_hyaluronic": (
        "J7318",
        "J7320",
        "J7321",
        "J7322",
        "J7323",
        "J7324",
        "J7325",
        "J7326",
        "J7327",
        "J7328",
        "J7329",
        "J7331",
        "J7332",
    ),
    "C_denominator_procedures": ("20610", "20611"),
    "D_sensitivity_iv_steroids": ("J2919", "J2920", "J2930", "J1720"),
}
ALL_CODES: tuple[str, ...] = tuple(code for codes in CODE_GROUPS.values() for code in codes)
GROUP_OF: dict[str, str] = {code: group for group, codes in CODE_GROUPS.items() for code in codes}

ZILRETTA_CODE = "J3304"

# Methylprednisolone acetate is one drug family whose billing codes changed over time: the 1 mg
# code J1010 appears only from 2024, the 20, 40 and 80 mg codes before it.
DRUG_FAMILY: dict[str, str] = {
    "J3304": "zilretta_triamcinolone_er",
    "J3301": "triamcinolone_acetonide",
    "J1010": "methylprednisolone_acetate",
    "J1020": "methylprednisolone_acetate",
    "J1030": "methylprednisolone_acetate",
    "J1040": "methylprednisolone_acetate",
    "J0702": "betamethasone",
    "J1100": "dexamethasone_sodium_phosphate",
    "J2919": "methylprednisolone_sodium_succinate",
    "J2920": "methylprednisolone_sodium_succinate",
    "J2930": "methylprednisolone_sodium_succinate",
    "J1720": "hydrocortisone_sodium_succinate",
}
for _code in CODE_GROUPS["B_context_hyaluronic"]:
    DRUG_FAMILY[_code] = "hyaluronic_acid"
for _code in CODE_GROUPS["C_denominator_procedures"]:
    DRUG_FAMILY[_code] = "joint_injection_procedure"

# Drug-name patterns (case-insensitive) used to find candidate products in Open Payments and in the
# Part D drug lists. They only nominate candidates for human review in the protocol appendix.
ZILRETTA_PATTERN = r"zilretta"
HYALURONIC_PRODUCT_PATTERN = (
    r"durolane|euflexxa|synvisc|orthovisc|monovisc|gel-?one|hyalgan|supartz|gelsyn|trivisc|"
    r"hymovis|genvisc|synojoynt|triluron|visco-?3|hyaluron"
)
STEROID_PRODUCT_PATTERN = (
    r"kenalog|triamcinolone|depo-?medrol|methylprednisolone|betamethasone|celestone"
)
NSAID_NAME_PATTERN = (
    r"meloxicam|naproxen|ibuprofen|diclofenac|celecoxib|etodolac|nabumetone|indomethacin|"
    r"ketorolac|oxaprozin|piroxicam|sulindac|ketoprofen|flurbiprofen|diflunisal|salsalate|"
    r"mefenamic|fenoprofen|tolmetin"
)
ORAL_STEROID_NAME_PATTERN = (
    r"prednisone|prednisolone|methylprednisolone|dexamethasone|hydrocortisone"
)
