"""Baselines for Task A: simple predictions every trained model has to beat.

A1 baselines predict next month's share for a segment from information up to last month:
the market-wide share, the segment's own share last month, the segment's history and the
specialty's history. The last three are smoothed with half a visit of each kind, so a segment
with no Zilretta visits yet is not predicted to have exactly zero share.

A2 baselines predict High or Low: the majority class, last month's side of the market
(persistence), and the specialty rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SMOOTHING = 0.5


def smoothed(z, n) -> np.ndarray:
    """Share with half a Zilretta visit and half a non-Zilretta visit added."""
    return (np.asarray(z, dtype=float) + SMOOTHING) / (np.asarray(n, dtype=float) + 2 * SMOOTHING)


def market_baseline(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return test["market_share_lag1"].to_numpy(float)


def last_month_baseline(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    n = np.expm1(test["seg_log_visits_lag1"].to_numpy(float)).round()
    return smoothed(test["seg_share_lag1"].to_numpy(float) * n, n)


def segment_history_baseline(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return smoothed(test["seg_prior_z"], test["seg_prior_t"])


def specialty_history_baseline(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return smoothed(test["spec_prior_z"], test["spec_prior_t"])


BASELINES = {
    "market": market_baseline,
    "last_month": last_month_baseline,
    "segment_history": segment_history_baseline,
    "specialty_history": specialty_history_baseline,
}


def majority_label(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    labels = train["y_label"]
    winner = "High" if (labels == "High").sum() > (labels == "Low").sum() else "Low"
    return np.full(len(test), winner, dtype=object)


def persistence_label(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return np.where(test["seg_high_lag1"].to_numpy(float) == 1, "High", "Low")


def specialty_rule_label(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    specialty = test["specialty_prior_share"].to_numpy(float)
    above = specialty > test["market_share_lag1"].to_numpy(float)
    return np.where(above, "High", "Low")


LABEL_BASELINES = {
    "majority": majority_label,
    "persistence": persistence_label,
    "specialty_rule": specialty_rule_label,
}
