"""Leak-safe monthly features for the Up / Down / Flat classifier.

Promoted from notebooks/02_eda_cleaned_data.ipynb §13.1, where each feature is traced to
the EDA finding that motivated it. The leakage rule: a feature on month t may use only
information available at the end of month t-1. Calendar and event features are the
exception that proves the rule - they depend only on month t's own date, which is known
in advance.

The target is the Gold table's stored `direction_label` (PROPOSAL.md §18.2, trailing
12-month z-score rule), not a re-derivation of it, so there is one definition of the label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import Engine

TARGET = "direction_label"

CATEGORY_COLUMNS = [
    "branded_injectable_visits",
    "generic_corticosteroid_visits",
    "nsaid_otc_visits",
]
SETTING_COLUMNS = ["office_visits", "hospital_visits", "other_visits", "telehealth_visits"]

# Month ids (YYYYMM, inclusive). The COVID shock is the Mar-May 2020 Office -> Telehealth
# collapse; the 2024 dip is the Mar-Jul volume drop across unrelated categories that
# looks like a claims-supply disruption (PROPOSAL.md §10).
COVID_SHOCK_MONTHS = (202003, 202005)
DIP_2024_MONTHS = (202403, 202407)

VOLATILITY_WINDOW = 12

MONTHLY_FEATURES = [
    "share_lag_1",
    "share_lag_2",
    "share_lag_3",
    "share_mean_prior_3m",
    "share_mean_prior_6m",
    "share_gap_to_prior_6m",
    "share_change_lag_1",
    "share_change_lag_2",
    "share_change_std_prior_12m",
    "share_rolling_z_lag_1",
    "branded_injectable_growth_lag_1",
    "generic_corticosteroid_growth_lag_1",
    "nsaid_otc_growth_lag_1",
    "nsaid_mix_lag_1",
    "office_mix_lag_1",
    "telehealth_mix_lag_1",
    "month_sin",
    "month_cos",
    "is_december",
    "is_january",
    "months_since_launch",
    "covid_shock",
    "dip_2024",
]


# For ablations: which families of features a model can lean on. Every monthly feature is in
# exactly one group.
FEATURE_GROUPS = {
    "share_history": [
        "share_lag_1",
        "share_lag_2",
        "share_lag_3",
        "share_mean_prior_3m",
        "share_mean_prior_6m",
        "share_gap_to_prior_6m",
        "share_change_lag_1",
        "share_change_lag_2",
        "share_change_std_prior_12m",
        "share_rolling_z_lag_1",
    ],
    "volume": [
        "branded_injectable_growth_lag_1",
        "generic_corticosteroid_growth_lag_1",
        "nsaid_otc_growth_lag_1",
        "nsaid_mix_lag_1",
        "office_mix_lag_1",
        "telehealth_mix_lag_1",
    ],
    "calendar": ["month_sin", "month_cos", "is_december", "is_january", "months_since_launch"],
    "events": ["covid_shock", "dip_2024"],
}


def load_monthly_gold(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)


def _between(month_ids: pd.Series, bounds: tuple[int, int]) -> pd.Series:
    return month_ids.between(bounds[0], bounds[1]).astype(int)


def compute_monthly_features(gold: pd.DataFrame) -> pd.DataFrame:
    """One row per month, indexed by `month_id`: the target followed by every feature in
    `MONTHLY_FEATURES`. Early months have NaN features until enough history exists;
    `model_ready_monthly` drops them."""
    gold = gold.sort_values("month_id").reset_index(drop=True)
    share = gold["visit_share"]
    change = share.diff()
    prev = gold.shift(1)
    month_number = gold["month_id"] % 100

    prior_mean = share.shift(1).rolling(VOLATILITY_WINDOW).mean()
    prior_std = share.shift(1).rolling(VOLATILITY_WINDOW).std()
    rolling_z = (share - prior_mean) / prior_std

    fe = pd.DataFrame({TARGET: gold[TARGET]})

    fe["share_lag_1"] = share.shift(1)
    fe["share_lag_2"] = share.shift(2)
    fe["share_lag_3"] = share.shift(3)
    fe["share_mean_prior_3m"] = share.shift(1).rolling(3).mean()
    fe["share_mean_prior_6m"] = share.shift(1).rolling(6).mean()
    fe["share_gap_to_prior_6m"] = fe["share_lag_1"] - share.shift(2).rolling(6).mean()

    fe["share_change_lag_1"] = change.shift(1)
    fe["share_change_lag_2"] = change.shift(2)
    fe["share_change_std_prior_12m"] = change.shift(1).rolling(VOLATILITY_WINDOW).std()
    fe["share_rolling_z_lag_1"] = rolling_z.shift(1)

    for column in CATEGORY_COLUMNS:
        name = column.removesuffix("_visits")
        fe[f"{name}_growth_lag_1"] = np.log1p(gold[column]).diff().shift(1)
    fe["nsaid_mix_lag_1"] = prev["nsaid_otc_visits"] / prev[CATEGORY_COLUMNS].sum(axis=1)
    fe["office_mix_lag_1"] = prev["office_visits"] / prev[SETTING_COLUMNS].sum(axis=1)
    fe["telehealth_mix_lag_1"] = prev["telehealth_visits"] / prev[SETTING_COLUMNS].sum(axis=1)

    fe["month_sin"] = np.sin(2 * np.pi * month_number / 12)
    fe["month_cos"] = np.cos(2 * np.pi * month_number / 12)
    fe["is_december"] = (month_number == 12).astype(int)
    fe["is_january"] = (month_number == 1).astype(int)
    fe["months_since_launch"] = gold["months_since_launch"]

    fe["covid_shock"] = _between(gold["month_id"], COVID_SHOCK_MONTHS)
    fe["dip_2024"] = _between(gold["month_id"], DIP_2024_MONTHS)

    fe.index = pd.Index(gold["month_id"], name="month_id")
    return fe[[TARGET, *MONTHLY_FEATURES]]


def model_ready_monthly(features: pd.DataFrame, *, drop_constant: bool = True) -> pd.DataFrame:
    """Rows with a target and a complete feature vector. `drop_constant` also removes
    features with a single value across those rows (e.g. `covid_shock`, which falls
    entirely inside the warm-up months), since a zero-variance column breaks
    standardisation and carries no signal."""
    ready = features.dropna()
    if drop_constant:
        constant = [c for c in MONTHLY_FEATURES if ready[c].nunique() <= 1]
        ready = ready.drop(columns=constant)
    return ready
