"""Tests for the leak-safe monthly feature matrix (src/.../features/monthly.py).

The central guarantee is the leakage rule from notebooks/02_eda_cleaned_data.ipynb §13:
a feature on month t may use only information available at the end of month t-1
(calendar and event features are deterministic functions of month t's own date, known
in advance). `test_features_at_month_t_ignore_everything_from_t_onward` enforces that
generically for every feature column, rather than pinning it one feature at a time.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.features.monthly import (
    MONTHLY_FEATURES,
    TARGET,
    compute_monthly_features,
    model_ready_monthly,
)

CATEGORY_COLS = [
    "branded_injectable_visits",
    "generic_corticosteroid_visits",
    "nsaid_otc_visits",
]
SETTING_COLS = ["office_visits", "hospital_visits", "other_visits", "telehealth_visits"]
MEASUREMENT_COLS = ["visit_share", *CATEGORY_COLS, *SETTING_COLS]


def _synthetic_gold(n_months: int = 30, start_year: int = 2019, start_month: int = 8):
    """A deterministic Gold-shaped frame: realistic columns, arbitrary but fixed values."""
    rng = np.random.RandomState(7)
    month_ids = []
    year, month = start_year, start_month
    for _ in range(n_months):
        month_ids.append(year * 100 + month)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    branded = rng.randint(1_000, 3_000, n_months)
    generic = rng.randint(60_000, 90_000, n_months)
    nsaid = rng.randint(3_000, 7_000, n_months)
    labels = rng.choice(["Up", "Flat", "Down"], n_months).astype(object)
    labels[:13] = None
    return pd.DataFrame(
        {
            "month_id": month_ids,
            "branded_injectable_visits": branded,
            "generic_corticosteroid_visits": generic,
            "nsaid_otc_visits": nsaid,
            "visit_share": branded / (branded + generic + nsaid),
            "direction_label": labels,
            "office_visits": rng.randint(70_000, 110_000, n_months),
            "hospital_visits": rng.randint(2_000, 3_500, n_months),
            "other_visits": rng.randint(1_000, 2_500, n_months),
            "telehealth_visits": rng.randint(100, 1_100, n_months),
            "months_since_launch": np.arange(22, 22 + n_months),
        }
    )


def _same(a: pd.Series, b: pd.Series) -> bool:
    return bool(np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True))


def test_feature_columns_match_the_declared_list():
    result = compute_monthly_features(_synthetic_gold())
    assert list(result.columns) == [TARGET, *MONTHLY_FEATURES]
    assert result.index.name == "month_id"
    assert len(result) == 30


@pytest.mark.parametrize("t", [13, 20, 29])
def test_features_at_month_t_ignore_everything_from_t_onward(t):
    gold = _synthetic_gold()
    base = compute_monthly_features(gold)

    altered = gold.copy()
    altered[MEASUREMENT_COLS] = altered[MEASUREMENT_COLS].astype(float)
    altered.loc[t:, MEASUREMENT_COLS] = altered.loc[t:, MEASUREMENT_COLS] * 3.7 + 11
    altered.loc[t:, "direction_label"] = "Down"
    changed = compute_monthly_features(altered)

    for feature in MONTHLY_FEATURES:
        assert _same(base[feature].iloc[[t]], changed[feature].iloc[[t]]), (
            f"{feature} at month index {t} depends on data from month t or later"
        )


def test_features_do_depend_on_the_previous_month():
    gold = _synthetic_gold()
    base = compute_monthly_features(gold)
    altered = gold.copy()
    altered.loc[19, "visit_share"] = altered.loc[19, "visit_share"] * 2
    changed = compute_monthly_features(altered)
    assert base.loc[gold.loc[20, "month_id"], "share_lag_1"] != pytest.approx(
        changed.loc[gold.loc[20, "month_id"], "share_lag_1"]
    )


def test_momentum_growth_and_mix_values():
    gold = _synthetic_gold()
    result = compute_monthly_features(gold)
    share = gold["visit_share"]
    i = 16
    month_id = gold.loc[i, "month_id"]
    row = result.loc[month_id]

    assert row["share_change_lag_1"] == pytest.approx(share[i - 1] - share[i - 2])
    assert row["share_change_lag_2"] == pytest.approx(share[i - 2] - share[i - 3])
    assert row["share_gap_to_prior_6m"] == pytest.approx(share[i - 1] - share[i - 7 : i - 1].mean())
    assert row["share_mean_prior_3m"] == pytest.approx(share[i - 3 : i].mean())

    brand = gold["branded_injectable_visits"]
    assert row["branded_injectable_growth_lag_1"] == pytest.approx(
        np.log1p(brand[i - 1]) - np.log1p(brand[i - 2])
    )
    prev = gold.loc[i - 1]
    assert row["nsaid_mix_lag_1"] == pytest.approx(
        prev["nsaid_otc_visits"] / prev[CATEGORY_COLS].sum()
    )
    assert row["office_mix_lag_1"] == pytest.approx(
        prev["office_visits"] / prev[SETTING_COLS].sum()
    )
    assert row["telehealth_mix_lag_1"] == pytest.approx(
        prev["telehealth_visits"] / prev[SETTING_COLS].sum()
    )


def test_trailing_volatility_and_rolling_z_use_prior_months_only():
    gold = _synthetic_gold()
    result = compute_monthly_features(gold)
    share = gold["visit_share"]
    chg = share.diff()
    i = 20
    row = result.loc[gold.loc[i, "month_id"]]

    assert row["share_change_std_prior_12m"] == pytest.approx(chg[i - 12 : i].std())
    prior = share[i - 13 : i - 1]
    expected_z = (share[i - 1] - prior.mean()) / prior.std()
    assert row["share_rolling_z_lag_1"] == pytest.approx(expected_z)


def test_calendar_and_event_flags():
    gold = _synthetic_gold(n_months=60, start_year=2019, start_month=8)
    result = compute_monthly_features(gold)

    assert result.loc[201912, "is_december"] == 1
    assert result.loc[202001, "is_january"] == 1
    assert result.loc[202001, "is_december"] == 0
    assert result.loc[202003, "month_sin"] == pytest.approx(1.0)
    assert result.loc[202003, "month_cos"] == pytest.approx(0.0, abs=1e-12)

    covid = result.index[result["covid_shock"] == 1].tolist()
    assert covid == [202003, 202004, 202005]
    dip = result.index[result["dip_2024"] == 1].tolist()
    assert dip == [202403, 202404, 202405, 202406, 202407]


def test_model_ready_drops_warmup_rows_and_constant_features():
    gold = _synthetic_gold()  # Mar-May 2020 fall inside the 13 warm-up months
    full = compute_monthly_features(gold)
    ready = model_ready_monthly(full)

    assert len(ready) == 30 - 13
    assert not ready.isna().any().any()
    assert "covid_shock" not in ready.columns  # constant (all 0) once warm-up rows are gone
    assert "dip_2024" not in ready.columns  # also absent from this 30-month window

    kept = model_ready_monthly(full, drop_constant=False)
    assert "covid_shock" in kept.columns
    assert kept["covid_shock"].nunique() == 1
