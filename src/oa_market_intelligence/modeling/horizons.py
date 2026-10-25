"""The horizon harness: pairing, walk-forward splits and the rules shared by T1, T2 and T3.

A forecast is made at an *origin* month t0 using data through t0, for the month t0 + h
(docs/modelling_plan.md, sections 2 and 14). A feature row of month m (features through m - 1, so
its origin is m - 1) is paired with the outcome of month m + h - 1. In a paired frame `month_id`
is the **outcome month** (so the month-level scoring and bootstrap code applies unchanged),
`origin_id` is the origin and `row_month` is the feature month. A row may be used for training at
origin t0 only if its outcome month is at most t0; the splits below enforce it and raise
`FutureLeakError` otherwise.

The first origin is the 24th month of data (the plan's printed test-month counts), so for h > 1
the first training window holds fewer than 24 outcome months.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np
import pandas as pd

from oa_market_intelligence.features.groups import COVID_MONTHS, DIP_MONTHS
from oa_market_intelligence.modeling.segment_eval import FutureLeakError

HORIZONS = (1, 3, 6)
ALPHA = 0.05
N_TRAINED = 3
N_SECONDARY = 2  # h = 3 and h = 6
DEFAULT_MIN_ORIGIN_MONTHS = 24
SCENARIOS = ("main", "covid_removed", "dip_2024_removed", "pediatrics_removed")


def month_index(month_id):
    """YYYYMM (an int, array or Series) as a running month number, so that arithmetic on months
    crosses year boundaries correctly."""
    month_id = np.asarray(month_id)
    return (month_id // 100) * 12 + (month_id % 100) - 1


def month_id_from_index(index):
    index = np.asarray(index)
    return (index // 12) * 100 + index % 12 + 1


def promotion_percentile(horizon: int) -> float:
    """The Bonferroni-corrected lower percentile: 0.05 over 3 models at h = 1 (the 1.67th), and
    0.05 over 3 models x 2 secondary horizons at h = 3 and 6 (the 0.83th)."""
    if horizon == 1:
        return 100 * ALPHA / N_TRAINED
    return 100 * ALPHA / (N_TRAINED * N_SECONDARY)


def segment_key(seg: pd.DataFrame) -> pd.Series:
    return seg["specialty_name"] + "|" + seg["age_band"] + "|" + seg["gender"]


def outcome_table(seg: pd.DataFrame) -> pd.DataFrame:
    """The outcome of every segment-month from the segment Gold table (`load_segment_gold`
    output): share, category visits and the market-wide share of that month. Months with no
    category visits have no share and are left out. The outcome is read from Gold, not from the
    feature table, so whether a later month happens to be a scored row cannot decide whether an
    earlier row has an outcome."""
    seg = seg.copy()
    seg["segment"] = segment_key(seg)
    z = seg["branded_injectable_visits"].astype(float)
    t = seg["total_category_visits"].astype(float)
    market = z.groupby(seg["month_id"]).sum() / t.groupby(seg["month_id"]).sum()
    out = pd.DataFrame(
        {
            "segment": seg["segment"],
            "month_id": seg["month_id"],
            "y_share": z / t.where(t > 0),
            "y_visits": t,
            "y_market": seg["month_id"].map(market),
        }
    )
    return out[out["y_visits"] > 0].reset_index(drop=True)


def pair_rows(rows: pd.DataFrame, outcomes: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """The feature rows paired with the outcome `horizon` months after the origin.

    Every `y_` column of `rows` is replaced by the outcome of month m + h - 1 from `outcomes`;
    a row with no outcome is dropped; the calendar columns follow the outcome month, which is
    known in advance. For h = 1 the outcome is the row's own month and the result equals `rows`."""
    if horizon < 1:
        raise ValueError("horizon must be at least 1")
    base = rows.drop(columns=[c for c in rows.columns if c.startswith("y_")]).copy()
    idx = month_index(base["month_id"].to_numpy())
    base["row_month"] = base["month_id"].to_numpy()
    base["origin_id"] = month_id_from_index(idx - 1)
    base["month_id"] = month_id_from_index(idx + horizon - 1)
    merged = base.merge(
        outcomes[["segment", "month_id", "y_share", "y_visits", "y_market"]],
        on=["segment", "month_id"],
        how="inner",
    )
    month = merged["month_id"] % 100
    merged["month_sin"] = np.sin(2 * np.pi * month / 12)
    merged["month_cos"] = np.cos(2 * np.pi * month / 12)
    return merged.sort_values(["month_id", "segment"]).reset_index(drop=True)


def assert_pairing(paired: pd.DataFrame, horizon: int) -> None:
    """Every row's outcome month must be exactly `horizon` months after its origin. A pairing
    that puts an outcome at or before the origin (a leak) raises."""
    gap = month_index(paired["month_id"].to_numpy()) - month_index(paired["origin_id"].to_numpy())
    if (gap <= 0).any():
        raise FutureLeakError("a row's outcome month is not after its origin")
    if (gap != horizon).any():
        raise FutureLeakError(f"a row's outcome is not {horizon} months after its origin")


def horizon_splits(horizon: int) -> Callable[..., Iterator]:
    """A splitter for `walk_forward_predict_rows`: one origin at a time, a fresh training set of
    the rows whose outcome month is at most the origin. `min_train_months` is read as the number
    of months of data at the first origin."""

    def splits(rows: pd.DataFrame, *, min_train_months: int = DEFAULT_MIN_ORIGIN_MONTHS):
        outcome = month_index(rows["month_id"].to_numpy())
        first_origin = int(month_index(rows["row_month"].to_numpy()).min()) + min_train_months - 1
        for test_outcome in np.unique(outcome):
            origin = int(test_outcome) - horizon
            if origin < first_origin:
                continue
            train = np.flatnonzero(outcome <= origin)
            test = np.flatnonzero(outcome == test_outcome)
            if len(train) and outcome[train].max() > origin:
                raise FutureLeakError("a training row has an outcome after the origin")
            if len(train) == 0 or len(test) == 0:
                continue
            yield int(month_id_from_index(test_outcome)), train, test

    return splits


def apply_scenario(paired: pd.DataFrame, scenario: str) -> pd.DataFrame:
    """The paired rows of a robustness scenario: rows whose outcome month is in the COVID months
    or the 2024 dip, or whose specialty is PEDIATRICS, are removed from training and scoring
    (the same convention as the feature test, DL-76). The walk is then run again."""
    if scenario == "main":
        return paired
    if scenario == "covid_removed":
        keep = ~paired["month_id"].between(*COVID_MONTHS)
    elif scenario == "dip_2024_removed":
        keep = ~paired["month_id"].between(*DIP_MONTHS)
    elif scenario == "pediatrics_removed":
        keep = paired["specialty_name"] != "PEDIATRICS"
    else:
        raise ValueError(f"unknown scenario {scenario!r}")
    return paired[keep].reset_index(drop=True)


def simpler_first(order: tuple[str, ...], names: list[str]) -> list[str]:
    return sorted(names, key=order.index)


def serve_simplest(
    promoted: list[str],
    pooled_score: dict[str, float],
    draws: dict[str, np.ndarray],
    *,
    order: tuple[str, ...],
    lower_percentile: float,
    higher_is_better: bool,
) -> dict:
    """Among promoted models the simplest serves unless a more complex one is clearly better, by
    the same percentile rule. `draws` hold the metric on the same resamples for every model."""
    if not promoted:
        return {"serving": None, "reason": "no promoted model", "comparisons": {}}
    sign = -1.0 if higher_is_better else 1.0
    best = min(promoted, key=lambda name: sign * pooled_score[name])
    comparisons = {}
    for name in simpler_first(order, promoted):
        if name == best:
            return {"serving": best, "reason": "best score", "comparisons": comparisons}
        gain = (draws[best] - draws[name]) if higher_is_better else (draws[name] - draws[best])
        gain = gain[~np.isnan(gain)]
        low = float(np.percentile(gain, lower_percentile))
        comparisons[name] = low
        if low <= 0:
            return {
                "serving": name,
                "reason": f"{best} is not clearly better than {name}",
                "comparisons": comparisons,
            }
    return {"serving": best, "reason": "best score", "comparisons": comparisons}
