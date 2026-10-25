"""Small synthetic data for the modelling tests: a segment Gold table, the feature rows built from
it with the real row builder, and the Gold outcome table. Shares drift and fluctuate so that some
segment-months fall by 30% or more."""

import numpy as np
import pandas as pd

from oa_market_intelligence.features.segment_task import build_task_a_rows
from oa_market_intelligence.modeling.horizons import outcome_table

SPECIALTIES = ["ORTHOPEDIC SURGERY", "RHEUMATOLOGY", "FAMILY MEDICINE", "PEDIATRICS"]
AGES = ["40 TO 59", "65 TO 74", "75 TO 84"]
GENDERS = ["FEMALE", "MALE"]


def month_ids(n=44, start=(2019, 8)):
    year, month = start
    out = []
    for _ in range(n):
        out.append(year * 100 + month)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def make_seg(n_months=44, seed=0):
    rng = np.random.default_rng(seed)
    records = []
    for i, month in enumerate(month_ids(n_months)):
        for s, specialty in enumerate(SPECIALTIES):
            for a, age in enumerate(AGES):
                for gender in GENDERS:
                    t = int(rng.integers(150, 500))
                    base = 0.05 + 0.02 * s + 0.01 * a
                    share = max(0.005, base * (1 - 0.004 * i) * rng.lognormal(0, 0.35))
                    z = int(rng.binomial(t, min(share, 0.9)))
                    records.append(
                        dict(month_id=month, specialty_name=specialty, age_band=age,
                             gender=gender, branded_injectable_visits=z,
                             total_category_visits=t)
                    )
    return pd.DataFrame(records)


def make_inputs(n_months=44, seed=0):
    """(feature rows, outcome table, segment Gold table)."""
    seg = make_seg(n_months, seed)
    rows = build_task_a_rows(seg).drop(columns=["y_label", "y_raw_above"])
    return rows, outcome_table(seg), seg


def make_series(n=72, seed=0):
    """A national share series in percentage points, with consecutive month ids."""
    rng = np.random.default_rng(seed)
    values = 2.5 + np.cumsum(rng.normal(0, 0.1, n)) + 0.2 * np.sin(np.arange(n) * 2 * np.pi / 12)
    return pd.Series(values, index=month_ids(n))
