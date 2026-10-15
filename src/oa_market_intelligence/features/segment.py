"""Segment-level features for gold_segment_adoption (the stretch adoption objective).

Promoted from notebooks/02_eda_cleaned_data.ipynb §13.2. The headline feature is
`specialty_prior_share`, a target encoding: a specialty's volume-weighted Zilretta share
over all months strictly before the segment's own month. It replaces 50 specialty dummy
columns with one number and, built from earlier months only, is free of leakage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import Engine

AGE_BANDS_ORDERED = [
    "00 TO 02",
    "03 TO 09",
    "10 TO 19",
    "20 TO 39",
    "40 TO 59",
    "60 TO 64",
    "65 TO 74",
    "75 TO 84",
    "85 +",
]  # UNSPECIFIED is deliberately absent: it has no position, so its ordinal is NaN

MIN_SEGMENT_VISITS = 20  # below this a segment's share is mostly small-sample noise (§2, §10)
RARE_SPECIALTY_THRESHOLD = 0.01
RARE_LABEL = "RARE (grouped)"  # 'OTHER' is already a real specialty in dim_specialty

SEGMENT_FEATURES = [
    "specialty_grouped",
    "specialty_prior_share",
    "age_ordinal",
    "gender_FEMALE",
    "gender_MALE",
    "log_total_visits",
    "month_sin",
    "month_cos",
]

_SEGMENT_QUERY = """
    SELECT g.month_id, s.specialty_name, d.age_band, d.gender,
           g.branded_injectable_visits, g.total_category_visits, g.segment_visit_share
    FROM gold_segment_adoption g
    JOIN dim_specialty s USING (specialty_id)
    JOIN dim_demographics d USING (demographic_id)
"""


def load_segment_gold(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(_SEGMENT_QUERY, conn)


def group_rare_specialties(
    specialty: pd.Series, threshold: float = RARE_SPECIALTY_THRESHOLD
) -> pd.Series:
    """Specialties under `threshold` of all rows become `RARE_LABEL`. Frequency is
    measured over the whole table; it uses no target information."""
    frequency = specialty.value_counts(normalize=True)
    common = frequency[frequency >= threshold].index
    return specialty.where(specialty.isin(common), RARE_LABEL)


def compute_segment_features(
    seg: pd.DataFrame, *, min_visits: int = MIN_SEGMENT_VISITS
) -> pd.DataFrame:
    """Modelling rows for the segment table: segments with at least `min_visits`
    category visits and at least one earlier month of specialty history, with the
    features in `SEGMENT_FEATURES`, the target `segment_visit_share`, and
    `sample_weight` (the segment's volume, so large segments count more)."""
    seg = seg.sort_values("month_id").copy()
    month_number = seg["month_id"] % 100

    seg["specialty_grouped"] = group_rare_specialties(seg["specialty_name"])
    seg["age_ordinal"] = seg["age_band"].map({a: i for i, a in enumerate(AGE_BANDS_ORDERED)})
    seg["gender_FEMALE"] = (seg["gender"] == "FEMALE").astype(int)
    seg["gender_MALE"] = (seg["gender"] == "MALE").astype(int)
    seg["log_total_visits"] = np.log1p(seg["total_category_visits"])
    seg["month_sin"] = np.sin(2 * np.pi * month_number / 12)
    seg["month_cos"] = np.cos(2 * np.pi * month_number / 12)

    cumulative_before = (
        seg.groupby(["specialty_name", "month_id"])[
            ["branded_injectable_visits", "total_category_visits"]
        ]
        .sum()
        .groupby(level=0)
        .cumsum()
        .groupby(level=0)
        .shift(1)
    )
    cumulative_before["specialty_prior_share"] = (
        cumulative_before["branded_injectable_visits"] / cumulative_before["total_category_visits"]
    )
    seg = seg.join(cumulative_before["specialty_prior_share"], on=["specialty_name", "month_id"])

    modelling = seg[seg["total_category_visits"] >= min_visits].dropna(
        subset=["specialty_prior_share"]
    )
    modelling = modelling.assign(sample_weight=modelling["total_category_visits"])
    return modelling.reset_index(drop=True)
