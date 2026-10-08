"""Tests for the 6b analysis: state adoption of Zilretta (E3).

Synthetic provider-years with planted state rates check the 30-provider reporting rule, the 2022
to 2024 rank-stability gate (usable only at rho >= 0.7), the headroom arithmetic, and the context
columns. Nothing here touches the real data.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.external.analysis import config
from oa_market_intelligence.external.analysis.states import (
    add_context,
    add_headroom,
    arthritis_by_state,
    ma_by_state_year,
    paired_rank_draws,
    rank_stability,
    state_table,
)
from oa_market_intelligence.external.loaders.reference import states_frame

STATES = states_frame()
US = ["TX", "CA", "NY", "FL", "OH", "PA", "IL", "GA", "NC", "MI"]


def _py(rates_22, rates_24=None, n=400, seed=0, extra=None):
    """Provider-years for 2022 and 2024: each state has n providers with the given rate."""
    rng = np.random.default_rng(seed)
    rates_24 = rates_24 if rates_24 is not None else rates_22
    rows = []
    for state, r22, r24 in zip(US, rates_22, rates_24, strict=True):
        for i in range(n):
            rows.append((2022, f"{state}{i}", state, bool(rng.random() < r22)))
            rows.append((2024, f"{state}{i}", state, bool(rng.random() < r24)))
    for state, count in (extra or {}).items():
        rows += [(2022, f"{state}{i}", state, False) for i in range(count)]
    frame = pd.DataFrame(rows, columns=["year", "npi", "state_code", "zilretta"])
    return frame.assign(group="x", specialty_cms="x")


RATES = np.linspace(0.01, 0.12, 10)


# ---------- the reporting rule ----------


def test_a_state_year_is_reported_only_with_at_least_30_visible_providers():
    py = _py(RATES, extra={"AK": 29, "WY": 30, "XX": 500})
    table = state_table(py, STATES, n_boot=100, seed=0).set_index(["year", "state_code"])
    assert table.loc[(2022, "AK"), "reported"] == 0 and pd.isna(
        table.loc[(2022, "AK"), "adoption_rate"]
    )
    assert table.loc[(2022, "WY"), "reported"] == 1  # exactly 30 counts
    assert table.loc[(2022, "AK"), "n_visible_providers"] == 29  # the count is kept
    assert (2022, "XX") not in table.index  # not a state or DC


def test_unreported_rows_carry_no_rate_or_interval_but_reported_ones_have_both():
    py = _py(RATES, extra={"AK": 5})
    table = state_table(py, STATES, n_boot=100, seed=0).set_index(["year", "state_code"])
    assert table.loc[(2022, "AK"), ["adoption_rate", "ci_low", "ci_high"]].isna().all()
    tx = table.loc[(2022, "TX")]
    assert tx["ci_low"] <= tx["adoption_rate"] <= tx["ci_high"]


def test_the_interval_covers_the_planted_state_rate():
    py = _py(RATES, n=2000)
    table = state_table(py, STATES, n_boot=300, seed=0).set_index(["year", "state_code"])
    covered = [
        table.loc[(2022, s), "ci_low"] <= r <= table.loc[(2022, s), "ci_high"]
        for s, r in zip(US, RATES, strict=True)
    ]
    assert np.mean(covered) >= 0.7  # 95% intervals: nearly all of the ten cover their truth


# ---------- stability gate ----------


def test_ranks_that_hold_between_2022_and_2024_are_usable():
    py = _py(RATES, RATES, n=1500)
    out = rank_stability(py, STATES, n_boot=200, seed=0)
    assert out["rho"] >= 0.9 and out["verdict"] == "usable" and out["n_states"] == 10
    assert out["rho_low"] <= out["rho"] <= out["rho_high"]


def test_ranks_that_reshuffle_are_unstable():
    rng = np.random.default_rng(5)
    py = _py(RATES, rng.permutation(RATES), n=1500)
    out = rank_stability(py, STATES, n_boot=200, seed=0)
    assert out["rho"] < config.E3_RANK_RHO_MIN and out["verdict"] == "unstable"


def test_a_state_below_30_providers_in_either_year_is_left_out_of_the_comparison():
    py = _py(RATES, n=400, extra={"AK": 10})
    out = rank_stability(py, STATES, n_boot=100, seed=0)
    assert "AK" not in out["states"] and out["n_states"] == 10


def test_the_stability_check_is_reproducible():
    py = _py(RATES, RATES, n=500)
    a = rank_stability(py, STATES, n_boot=100, seed=0)
    b = rank_stability(py, STATES, n_boot=100, seed=0)
    assert a == b


def test_paired_draws_have_the_right_shape_and_depend_on_the_seed():
    py = _py(RATES, n=200)
    a = paired_rank_draws(py, US, (2022, 2024), n_boot=40, seed=0)
    b = paired_rank_draws(py, US, (2022, 2024), n_boot=40, seed=1)
    assert a.shape == (40,) and not np.array_equal(a, b)


# ---------- headroom ----------


def _table():
    return pd.DataFrame(
        {
            "year": [2024, 2024, 2024],
            "state_code": ["TX", "CA", "NY"],
            "n_visible_providers": [200, 100, 100],
            "n_zilretta_providers": [20, 2, 8],
            "adoption_rate": [0.10, 0.02, 0.08],
            "reported": [1, 1, 1],
        }
    )


def test_headroom_is_visible_providers_times_the_gap_to_the_national_rate_when_ranks_are_usable():
    out = add_headroom(_table(), usable=True).set_index("state_code")
    national = 30 / 400  # 0.075 over the three states
    assert out.loc["CA", "headroom"] == pytest.approx(100 * (national - 0.02))
    assert out.loc["TX", "headroom"] == pytest.approx(200 * (national - 0.10))  # negative: above
    assert out["headroom"].sum() == pytest.approx(0.0)


def test_there_is_no_headroom_when_the_ranks_are_unstable():
    assert add_headroom(_table(), usable=False)["headroom"].isna().all()


def test_unreported_rows_get_no_headroom():
    table = _table()
    table.loc[1, ["reported", "adoption_rate"]] = [0, np.nan]
    out = add_headroom(table, usable=True).set_index("state_code")
    assert pd.isna(out.loc["CA", "headroom"]) and not pd.isna(out.loc["TX", "headroom"])


# ---------- context ----------


def test_arthritis_prevalence_is_population_weighted_and_skips_missing_values():
    counties = pd.DataFrame(
        {
            "state_code": ["TX", "TX", "TX", "CA"],
            "prevalence_pct": [10.0, 30.0, np.nan, 20.0],
            "total_population": [100, 300, 1000, 50],
        }
    )
    out = arthritis_by_state(counties).set_index("state_code")["arthritis_prevalence_pct"]
    assert out["TX"] == pytest.approx((10 * 100 + 30 * 300) / 400) and out["CA"] == pytest.approx(
        20.0
    )


def test_advantage_share_is_taken_from_state_rows_at_all_ages_and_joined_by_fips():
    geovar = pd.DataFrame(
        {
            "year": [2024, 2024, 2024, 2024],
            "geo_level": ["State", "State", "State", "National"],
            "geo_code": ["48", "48", "06", "US"],
            "age_level": ["All", ">=65", "All", "All"],
            "ma_participation_rate": [0.45, 0.50, 0.55, 0.40],
        }
    )
    out = ma_by_state_year(geovar, STATES).set_index(["year", "state_code"])[
        "ma_participation_rate"
    ]
    assert out[(2024, "TX")] == pytest.approx(0.45) and out[(2024, "CA")] == pytest.approx(0.55)
    assert len(out) == 2


def test_context_columns_are_joined_by_year_and_state():
    table = _table().assign(ma_participation_rate=None, arthritis_prevalence_pct=None)
    ma = pd.DataFrame({"year": [2024], "state_code": ["TX"], "ma_participation_rate": [0.45]})
    arth = pd.DataFrame({"state_code": ["TX", "CA"], "arthritis_prevalence_pct": [21.0, 17.0]})
    out = add_context(table, ma, arth).set_index("state_code")
    assert out.loc["TX", "ma_participation_rate"] == pytest.approx(0.45)
    assert pd.isna(out.loc["CA", "ma_participation_rate"])
    assert out.loc["CA", "arthritis_prevalence_pct"] == pytest.approx(17.0)


# ---------- Advantage-adjusted sensitivity ----------


def test_when_adoption_is_fully_explained_by_advantage_share_the_adjusted_ranking_is_unrelated():
    from oa_market_intelligence.external.analysis.states import ma_adjusted_sensitivity

    ma = np.linspace(0.2, 0.6, 10)
    table = pd.DataFrame(
        {
            "year": 2024,
            "state_code": US,
            "n_visible_providers": 100,
            "n_zilretta_providers": (100 * (0.12 - 0.15 * ma)).round().astype(int),
            "reported": 1,
            "ma_participation_rate": ma,
        }
    )
    table["adoption_rate"] = table["n_zilretta_providers"] / table["n_visible_providers"]
    table = add_headroom(table, usable=True)
    out = ma_adjusted_sensitivity(table, 2024)
    assert out["corr_with_ma"] < -0.9 and out["slope"] < 0
    assert out["n_states"] == 10 and abs(out["rho"]) < 0.9  # the ranking moves once MA is removed
