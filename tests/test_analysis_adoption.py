"""Tests for the specialty adoption model (src/.../analysis/adoption.py).

A binomial model on Zilretta visits out of category visits for every segment-month, with month
fixed effects plus specialty, age band and gender. Simulated data with known effects checks
that the sparse solver agrees with statsmodels, the model recovers what was put in, the tests
and intervals behave, and the split-half stability check can tell stable from unstable.
"""

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from scipy.stats import spearmanr

from oa_market_intelligence.analysis.adoption import (
    RARE_LABEL,
    adjusted_shares,
    bootstrap_adjusted_shares,
    build_design,
    combine_intervals,
    deviance_shares,
    fit_binomial,
    group_specialties,
    load_adoption_data,
    split_half_stability,
)

SPECIALTIES = ["S1", "S2", "S3", "S4", "S5"]
AGES = ["A1", "A2", "A3", "A4"]
GENDERS = ["F", "M"]


def simulate(seed=0, months=24, spec_effect=None, age_effect=None, gender_effect=0.0,
             overdispersion=0.0, base=-3.5, visits=(150, 400)):
    """Counts from a known logit model. spec_effect: one logit shift per specialty."""
    rng = np.random.default_rng(seed)
    spec_effect = spec_effect if spec_effect is not None else [0.0] * len(SPECIALTIES)
    age_effect = age_effect if age_effect is not None else [0.0] * len(AGES)
    month_effect = rng.normal(0, 0.15, months)
    rows = []
    for m in range(months):
        month_id = 201901 + (m // 12) * 100 + (m % 12)
        for s, se in zip(SPECIALTIES, spec_effect):
            for a, ae in zip(AGES, age_effect):
                for g, ge in zip(GENDERS, (0.0, gender_effect)):
                    eta = base + month_effect[m] + se + ae + ge
                    if overdispersion:
                        eta += rng.normal(0, overdispersion)
                    t = int(rng.integers(*visits))
                    z = int(rng.binomial(t, 1 / (1 + np.exp(-eta))))
                    rows.append((month_id, s, a, g, z, t))
    return pd.DataFrame(
        rows, columns=["month_id", "specialty", "age_band", "gender", "zilretta", "category"]
    )


def test_the_sparse_solver_matches_statsmodels():
    df = simulate(1, spec_effect=[0.5, 0.0, -0.4, 0.2, -0.2], age_effect=[0, 0.3, 0.1, -0.2])
    design = build_design(df)
    ours = fit_binomial(design, df)
    y = np.column_stack([df["zilretta"], df["category"] - df["zilretta"]])
    theirs = sm.GLM(y, design.X.toarray(), family=sm.families.Binomial()).fit()
    assert ours.converged
    assert ours.beta == pytest.approx(theirs.params, abs=1e-4)
    assert ours.deviance == pytest.approx(theirs.deviance, rel=1e-6)
    assert ours.pearson_chi2 == pytest.approx(theirs.pearson_chi2, rel=1e-4)


def test_fitted_totals_equal_observed_totals_at_every_level():
    """A logit model with a dummy for each level reproduces the observed total for every month,
    specialty, age band and gender: the 'model reproduces the totals' gate."""
    df = simulate(2, spec_effect=[0.4, 0, 0, -0.3, 0.1])
    design = build_design(df)
    fit = fit_binomial(design, df)
    fitted = 1 / (1 + np.exp(-(design.X @ fit.beta))) * df["category"].to_numpy()
    df = df.assign(fitted=fitted)
    for column in ("month_id", "specialty", "age_band", "gender"):
        observed = df.groupby(column)["zilretta"].sum()
        modelled = df.groupby(column)["fitted"].sum()
        assert modelled.to_numpy() == pytest.approx(observed.to_numpy(), rel=1e-6)


def test_known_specialty_effects_are_recovered_in_order_and_size():
    truth = [0.6, 0.2, 0.0, -0.3, -0.6]
    df = simulate(3, months=36, spec_effect=truth, visits=(300, 800))
    design = build_design(df)
    shares = adjusted_shares(design, df, fit_binomial(design, df))
    assert list(shares.sort_values(ascending=False).index) == SPECIALTIES  # S1 highest
    logit = np.log(shares / (1 - shares))
    assert (logit["S1"] - logit["S5"]) == pytest.approx(0.6 - (-0.6), abs=0.1)


def test_equal_specialties_give_equal_adjusted_shares():
    df = simulate(4, months=36, visits=(300, 800))
    design = build_design(df)
    shares = adjusted_shares(design, df, fit_binomial(design, df))
    assert shares.max() - shares.min() < 0.004


def test_the_specialty_test_rejects_a_real_effect_and_rarely_a_null_one():
    real = deviance_shares(simulate(5, spec_effect=[0.5, 0.2, 0.0, -0.2, -0.5]))
    assert real["specialty_p"] < 1e-6
    rejections = sum(deviance_shares(simulate(100 + i))["specialty_p"] < 0.05 for i in range(20))
    assert rejections <= 4  # nominal 5% of 20 is 1; 4 is a generous ceiling


def test_deviance_shares_attribute_the_effect_to_the_right_term():
    only_specialty = deviance_shares(
        simulate(6, months=36, spec_effect=[0.8, 0.3, 0.0, -0.3, -0.8], visits=(300, 800))
    )
    assert only_specialty["specialty"]["share"] > 0.9
    assert only_specialty["age_band"]["share"] < 0.1
    assert only_specialty["gender"]["share"] < 0.1
    only_age = deviance_shares(
        simulate(7, months=36, age_effect=[0.8, 0.3, 0.0, -0.8], visits=(300, 800))
    )
    assert only_age["age_band"]["share"] > 0.9


def test_dispersion_is_near_one_for_binomial_data_and_above_one_for_extra_variation():
    plain = deviance_shares(simulate(8, months=36, visits=(300, 800)))["dispersion"]
    extra = deviance_shares(simulate(8, months=36, overdispersion=0.4, visits=(300, 800)))[
        "dispersion"
    ]
    assert 0.85 < plain < 1.15
    assert extra > 1.5


def test_month_bootstrap_is_reproducible_and_brackets_the_estimate():
    df = simulate(9, months=24, spec_effect=[0.5, 0.2, 0.0, -0.2, -0.5])
    first = bootstrap_adjusted_shares(df, by="month", n_boot=60, seed=1)
    again = bootstrap_adjusted_shares(df, by="month", n_boot=60, seed=1)
    pd.testing.assert_frame_equal(first["table"], again["table"])
    table = first["table"]
    assert (table["low"] <= table["estimate"]).all() and (table["estimate"] <= table["high"]).all()
    assert (table["high"] > table["low"]).all()
    assert first["n_failed"] == 0


def test_segment_bootstrap_runs_and_the_combined_interval_is_the_wider_of_the_two():
    df = simulate(10, months=24)
    by_month = bootstrap_adjusted_shares(df, by="month", n_boot=40, seed=2)["table"]
    by_segment = bootstrap_adjusted_shares(df, by="segment", n_boot=40, seed=2)["table"]
    both = combine_intervals(by_month, by_segment)
    assert (both["low"] <= np.minimum(by_month["low"], by_segment["low"]) + 1e-12).all()
    assert (both["high"] >= np.maximum(by_month["high"], by_segment["high"]) - 1e-12).all()
    assert both["estimate"].to_numpy() == pytest.approx(by_month["estimate"].to_numpy())


def test_split_half_stability_detects_a_stable_pattern():
    df = simulate(11, months=48, spec_effect=[0.7, 0.3, 0.0, -0.3, -0.7], visits=(300, 800))
    result = split_half_stability(df, n_boot=60, seed=0)
    assert result["spearman"] > 0.8
    assert result["same_side_fraction"] == 1.0


def test_split_half_stability_detects_a_pattern_that_reverses():
    early = simulate(12, months=24, spec_effect=[0.7, 0.3, 0.0, -0.3, -0.7], visits=(300, 800))
    late = simulate(13, months=24, spec_effect=[-0.7, -0.3, 0.0, 0.3, 0.7], visits=(300, 800))
    late = late.assign(month_id=late["month_id"] + 1000)
    result = split_half_stability(pd.concat([early, late]), n_boot=60, seed=0)
    assert result["spearman"] < -0.5


def test_rare_specialties_are_grouped_by_visit_volume_not_row_count():
    df = pd.DataFrame(
        {
            "specialty": ["BIG", "SMALL", "SMALL", "SMALL"],
            "category": [1000, 1, 1, 1],
            "zilretta": [10, 0, 0, 0],
        }
    )
    grouped = group_specialties(df, threshold=0.005)
    assert grouped["specialty"].tolist() == ["BIG", RARE_LABEL, RARE_LABEL, RARE_LABEL]
    assert group_specialties(df, threshold=0.0001)["specialty"].tolist() == [
        "BIG", "SMALL", "SMALL", "SMALL",
    ]


def test_the_loader_reconciles_to_the_gold_totals(tiny_gold_engine):
    df = load_adoption_data(tiny_gold_engine)
    assert df["zilretta"].sum() == 30 and df["category"].sum() == 300
    assert {"month_id", "specialty", "age_band", "gender", "zilretta", "category"} <= set(df)
    assert spearmanr([1, 2, 3], [1, 2, 3])[0] == 1.0  # scipy available to the tests
