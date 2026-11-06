"""The model inputs, without the models (DL-117).

`design_frame` turns a table of segment features into the columns the logistic regressions use:
the shares on the logit scale (the history shares smoothed), plus the interaction that lets a model
trust a large segment's last month more than a small one's. It uses only pandas and NumPy, so the
API can build exactly the inputs the models were trained on without installing scikit-learn.
`segment_models` imports these names, so they can still be imported from there.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oa_market_intelligence.modeling.segment_baselines import smoothed

_NUMERIC = [
    "logit_lag1",
    "logit_roll3",
    "logit_seg_history",
    "logit_spec_history",
    "logit_market_lag1",
    "market_change_lag1",
    "seg_log_visits_lag1",
    "lag1_x_visits",
    "age_ordinal",
    "gender_FEMALE",
    "gender_MALE",
    "month_sin",
    "month_cos",
]


def _logit(p, eps: float = 1e-3) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def calendar_columns(frame: pd.DataFrame, target: int) -> pd.DataFrame:
    """The calendar columns follow the month being predicted (known in advance)."""
    out = frame.copy()
    month = target % 100
    out["month_sin"] = np.sin(2 * np.pi * month / 12)
    out["month_cos"] = np.cos(2 * np.pi * month / 12)
    return out


def design_frame(df: pd.DataFrame, extra_columns: tuple[str, ...] = ()) -> pd.DataFrame:
    """Inputs for the logistic regression and the forest: the shares on the logit scale, with
    the history shares smoothed, plus the interaction that lets a model trust a large
    segment's last month more than a small one's."""
    n_lag = np.expm1(df["seg_log_visits_lag1"].to_numpy(float)).round()
    logit_lag1 = _logit(smoothed(df["seg_share_lag1"].to_numpy(float) * n_lag, n_lag))
    out = pd.DataFrame(
        {
            "logit_lag1": logit_lag1,
            "logit_roll3": _logit(df["seg_share_roll3"]),
            "logit_seg_history": _logit(smoothed(df["seg_prior_z"], df["seg_prior_t"])),
            "logit_spec_history": _logit(smoothed(df["spec_prior_z"], df["spec_prior_t"])),
            "logit_market_lag1": _logit(df["market_share_lag1"]),
            "market_change_lag1": df["market_change_lag1"].to_numpy(float),
            "seg_log_visits_lag1": df["seg_log_visits_lag1"].to_numpy(float),
            "lag1_x_visits": logit_lag1 * df["seg_log_visits_lag1"].to_numpy(float),
            "age_ordinal": df["age_ordinal"].to_numpy(float),
            "gender_FEMALE": df["gender_FEMALE"].to_numpy(float),
            "gender_MALE": df["gender_MALE"].to_numpy(float),
            "month_sin": df["month_sin"].to_numpy(float),
            "month_cos": df["month_cos"].to_numpy(float),
        },
        index=df.index,
    )
    for column in extra_columns:  # a candidate family's columns (feature plan), none by default
        out[column] = df[column].to_numpy(float)
    out["specialty_grouped"] = df["specialty_grouped"].astype(str).to_numpy()
    return out
