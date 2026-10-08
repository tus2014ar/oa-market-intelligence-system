"""Tests for the 6a analysis functions: provider adoption, the cluster bootstrap, E1 and E2a.

Synthetic data with known answers: a planted rate, a planted rank agreement, a planted peak year.
The thresholds themselves are pinned to the protocol text.
"""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.adoption import (
    bootstrap_group_draws,
    cluster_bootstrap_ci,
    e1_result,
    e1_verdict,
    e2a_verdict,
    iqvia_adjusted_specialty_shares,
    provider_years,
    rank_draws,
    rates,
    top_k_overlap,
)
from oa_market_intelligence.external.common import REFERENCE_DIR
from oa_market_intelligence.external.loaders.reference import crosswalk_frame

# ---------- thresholds are the protocol's ----------


def test_every_threshold_is_the_one_written_in_the_protocol():
    protocol = (REFERENCE_DIR.parent.parent / "docs" / "external_data_protocol.md").read_text(
        encoding="utf-8"
    )
    for phrase in ("ρ ≥ 0.6", "ρ ≥ 0.7", "at least 30 visible providers", "70% of quarters"):
        assert phrase in protocol, phrase
    assert config.E1_RHO_MIN == 0.6 and config.E1_TOP_K == 3 and config.E1_TOP_MIN == 2
    assert config.E3_RANK_RHO_MIN == 0.7 and config.E3_MIN_PROVIDERS == 30
    assert config.E2A_PEAK_YEARS == (2021, 2022) and config.E2B_AGREEMENT_MIN == 0.70
    assert config.H1_DECLINE == -0.25 and config.H1_WINDOW == (202011, 202207)
    assert config.H2_CHANGE == 0.10 and config.H4_DROP == 0.10
    assert config.SEED == 0 and config.N_BOOT == 2000


# ---------- provider-years ----------

COLUMNS = ["year", "npi", "hcpcs_code", "specialty_cms", "entity_type", "state_code"]


def _rows(*rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def test_a_visible_provider_is_an_individual_billing_a_primary_set_code():
    frame = _rows(
        (2022, "1", "J3301", "Rheumatology", "I", "TX"),
        (2022, "1", "J3304", "Rheumatology", "I", "TX"),  # same provider, a second code
        (2022, "2", "J3301", "Family Practice", "I", "CA"),
        (2022, "3", "J3301", "Clinic or Group Practice", "O", "CA"),  # organisation
        (2022, "4", "J7323", "Orthopedic Surgery", "I", "TX"),  # hyaluronic: not set A
        (2022, "5", "J2930", "Internal Medicine", "I", "TX"),  # IV steroid: not set A
    )
    out = provider_years(frame, crosswalk_frame())
    assert sorted(out["npi"]) == ["1", "2"]
    one = out[out["npi"] == "1"].iloc[0]
    assert bool(one["zilretta"]) and one["group"] == "RHEUMATOLOGY"
    assert not bool(out[out["npi"] == "2"].iloc[0]["zilretta"])


def test_groups_come_from_the_approved_crosswalk_and_unmatched_specialties_have_none():
    frame = _rows(
        (2022, "1", "J3301", "Interventional Pain Management", "I", "TX"),
        (2022, "2", "J3301", "Pain Management", "I", "TX"),
        (2022, "3", "J3301", "Dermatology", "I", "TX"),
        (2022, "4", "J3301", "Physical Medicine and Rehabilitation", "I", "TX"),
    )
    out = provider_years(frame, crosswalk_frame()).set_index("npi")
    assert out.loc["1", "group"] == out.loc["2", "group"] == "PAIN MEDICINE"
    assert pd.isna(out.loc["3", "group"])
    assert out.loc["4", "group"] == "PHYSICAL MEDICINE & REHAB"


def test_a_provider_billing_in_two_years_is_two_provider_years():
    frame = _rows(
        (2021, "1", "J3301", "Rheumatology", "I", "TX"),
        (2022, "1", "J3304", "Rheumatology", "I", "TX"),
    )
    out = provider_years(frame, crosswalk_frame())
    assert len(out) == 2 and out["zilretta"].tolist() == [False, True]


# ---------- rates and the cluster bootstrap ----------


def _synthetic(n_a=3000, n_b=3000, p_a=0.30, p_b=0.10, years=2, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for group, n, p in (("A", n_a, p_a), ("B", n_b, p_b)):
        status = rng.random(n) < p
        for i in range(n):
            for y in range(years):
                rows.append((2020 + y, f"{group}{i}", group, "TX", bool(status[i])))
    return pd.DataFrame(rows, columns=["year", "npi", "group", "state_code", "zilretta"])


def test_rates_count_provider_years_and_distinct_providers():
    py = _synthetic(n_a=10, n_b=10, years=3)
    out = rates(py, "group").set_index("group")
    assert out.loc["A", "n_provider_years"] == 30 and out.loc["A", "n_providers"] == 10
    assert out.loc["A", "rate"] == pytest.approx(out.loc["A", "n_zilretta"] / 30)


def test_the_interval_covers_the_planted_rate_and_has_about_the_binomial_width():
    py = _synthetic(n_a=4000, n_b=1000, p_a=0.30, p_b=0.10, years=1)
    out = cluster_bootstrap_ci(py, "group", n_boot=500, seed=0).set_index("group")
    a = out.loc["A"]
    assert a["low"] < 0.30 < a["high"] and a["low"] < a["rate"] < a["high"]
    expected = 2 * 1.96 * np.sqrt(0.3 * 0.7 / 4000)
    assert (a["high"] - a["low"]) == pytest.approx(expected, rel=0.25)


def test_clustering_by_provider_widens_the_interval_when_years_repeat_the_same_provider():
    one = _synthetic(n_a=2000, n_b=2000, years=1)
    many = _synthetic(n_a=2000, n_b=2000, years=4)
    w1 = cluster_bootstrap_ci(one, "group", n_boot=400, seed=0).set_index("group").loc["A"]
    w4 = cluster_bootstrap_ci(many, "group", n_boot=400, seed=0).set_index("group").loc["A"]
    # four copies of each provider carry no extra information: the width must not shrink
    assert (w4["high"] - w4["low"]) > 0.8 * (w1["high"] - w1["low"])


def test_the_bootstrap_is_reproducible_and_depends_on_the_seed():
    py = _synthetic(n_a=300, n_b=300)
    a = bootstrap_group_draws(py, ["A", "B"], n_boot=50, seed=0)
    b = bootstrap_group_draws(py, ["A", "B"], n_boot=50, seed=0)
    c = bootstrap_group_draws(py, ["A", "B"], n_boot=50, seed=1)
    assert a.shape == (50, 2) and np.array_equal(a, b) and not np.array_equal(a, c)


def test_a_group_with_no_zilretta_providers_has_a_zero_rate_and_a_zero_interval():
    py = _synthetic(n_a=200, n_b=200, p_a=0.0, p_b=0.2)
    out = cluster_bootstrap_ci(py, "group", n_boot=100, seed=0).set_index("group")
    assert out.loc["A", ["rate", "low", "high"]].tolist() == [0.0, 0.0, 0.0]


# ---------- ranks and verdicts ----------


def test_rank_draws_match_scipys_spearman():
    rng = np.random.default_rng(3)
    draws = rng.random((5, 11))
    fixed = rng.random(11)
    got = rank_draws(draws, fixed)
    want = [spearmanr(row, fixed).statistic for row in draws]
    assert got == pytest.approx(want)


def test_ties_are_given_average_ranks():
    got = rank_draws(np.array([[1.0, 1.0, 2.0, 3.0]]), np.array([1.0, 2.0, 3.0, 4.0]))
    assert got[0] == pytest.approx(spearmanr([1, 1, 2, 3], [1, 2, 3, 4]).statistic)


def test_top_k_overlap_counts_shared_groups():
    a = pd.Series({"A": 5, "B": 4, "C": 3, "D": 2, "E": 1})
    b = pd.Series({"C": 9, "B": 8, "E": 7, "A": 6, "D": 1})
    assert top_k_overlap(a, b, 3) == 2  # {A, B, C} against {C, B, E}


@pytest.mark.parametrize(
    ("rho", "overlap", "expected"),
    [
        (0.8, 3, "agrees"),
        (0.6, 2, "agrees"),  # both boundaries inclusive
        (0.59, 2, "partial"),
        (0.7, 1, "partial"),
        (0.5, 3, "partial"),
        (0.2, 0, "disagrees"),
        (-0.4, 1, "disagrees"),
    ],
)
def test_the_e1_verdict_follows_the_pre_set_rule(rho, overlap, expected):
    assert e1_verdict(rho, overlap) == expected


def test_a_missing_correlation_is_an_error_not_a_verdict():
    with pytest.raises(ValueError):
        e1_verdict(float("nan"), 2)


@pytest.mark.parametrize(
    ("rates_by_year", "expected"),
    [
        (
            {2019: 0.10, 2020: 0.12, 2021: 0.14, 2022: 0.13, 2023: 0.12, 2024: 0.11},
            ("consistent", 2021),
        ),
        (
            {2019: 0.10, 2020: 0.12, 2021: 0.13, 2022: 0.14, 2023: 0.12, 2024: 0.11},
            ("consistent", 2022),
        ),
        (
            {2019: 0.10, 2020: 0.12, 2021: 0.13, 2022: 0.12, 2023: 0.15, 2024: 0.14},
            ("inconsistent", 2023),
        ),
        (
            {2019: 0.10, 2020: 0.12, 2021: 0.14, 2022: 0.13, 2023: 0.12, 2024: 0.14},
            ("inconsistent", 2021),
        ),
    ],
)
def test_the_e2a_rule_needs_a_peak_in_2021_or_2022_and_a_lower_2024(rates_by_year, expected):
    assert e2a_verdict(rates_by_year) == expected


# ---------- E1 on planted data ----------

GROUPS = [f"G{i}" for i in range(11)]


def _planted(agree=True, seed=0):
    rng = np.random.default_rng(seed)
    base = np.linspace(0.05, 0.40, 11)
    medicare_rates = base if agree else rng.permutation(base)
    rows = []
    for g, p in zip(GROUPS, medicare_rates, strict=True):
        n = 800
        status = rng.random(n) < p
        rows += [(2022, f"{g}-{i}", g, "TX", bool(s)) for i, s in enumerate(status)]
    py = pd.DataFrame(rows, columns=["year", "npi", "group", "state_code", "zilretta"])
    iqvia = pd.Series(np.linspace(0.02, 0.09, 11), index=GROUPS)
    return py, iqvia


def test_e1_finds_an_agreement_that_was_planted():
    py, iqvia = _planted(agree=True)
    out = e1_result(py, iqvia, n_boot=300, seed=0)
    assert out["rho"] > 0.9 and out["overlap"] == 3 and out["verdict"] == "agrees"
    assert out["rho_low"] > 0.6 and out["rho_high"] <= 1.0
    assert out["top3_medicare"] == out["top3_iqvia"]


def test_e1_reports_disagreement_when_the_rankings_are_unrelated():
    py, iqvia = _planted(agree=False, seed=4)
    out = e1_result(py, iqvia, n_boot=300, seed=0)
    assert out["verdict"] in {"disagrees", "partial"} and out["rho"] < 0.6


def test_e1_can_be_rerun_without_one_group_as_a_sensitivity_check():
    py, iqvia = _planted(agree=True)
    full = e1_result(py, iqvia, n_boot=200, seed=0)
    without = e1_result(py, iqvia, n_boot=200, seed=0, drop=["G3"])
    assert without["n_groups"] == full["n_groups"] - 1 == 10


def test_e1_is_reproducible():
    py, iqvia = _planted()
    a = e1_result(py, iqvia, n_boot=200, seed=0)
    b = e1_result(py, iqvia, n_boot=200, seed=0)
    assert a == b


# ---------- IQVIA side ----------

SPECIALTIES = ["S1", "S2", "S3", "S4", "S5", "TINY"]
AGES = ["60 TO 64", "65 TO 74", "75 TO 84"]


def _iqvia(seed=0):
    rng = np.random.default_rng(seed)
    effects = dict(zip(SPECIALTIES, [0.8, 0.4, 0.0, -0.4, -0.8, 0.0], strict=True))
    rows = []
    for month in (
        list(range(201901, 201913)) + list(range(202001, 202013)) + list(range(202101, 202113))
    ):
        for s in SPECIALTIES:
            for a in AGES:
                for g in ("F", "M"):
                    t = 2 if s == "TINY" else int(rng.integers(200, 500))
                    eta = -3.5 + effects[s]
                    z = int(rng.binomial(t, 1 / (1 + np.exp(-eta))))
                    rows.append((month, s, a, g, z, t))
    return pd.DataFrame(
        rows, columns=["month_id", "specialty", "age_band", "gender", "zilretta", "category"]
    )


def test_iqvia_adjusted_shares_recover_the_planted_specialty_order_for_a_month_window():
    shares = iqvia_adjusted_specialty_shares(_iqvia(), first_month=202001, last_month=202112)
    order = [s for s in shares.sort_values(ascending=False).index if s.startswith("S")]
    assert order == ["S1", "S2", "S3", "S4", "S5"]


def test_the_rare_grouping_is_decided_on_all_the_data_not_on_the_window():
    shares = iqvia_adjusted_specialty_shares(_iqvia(), first_month=202101, last_month=202112)
    assert "RARE (grouped)" in shares.index and "TINY" not in shares.index


def test_the_age_filter_restricts_the_fit_to_the_chosen_bands():
    df = _iqvia()
    older = iqvia_adjusted_specialty_shares(
        df, first_month=202001, last_month=202112, ages=("65 TO 74", "75 TO 84")
    )
    allages = iqvia_adjusted_specialty_shares(df, first_month=202001, last_month=202112)
    assert set(older.index) == set(allages.index) and older.notna().all()
