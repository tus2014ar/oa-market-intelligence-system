"""Task A dataset: will a segment's Zilretta share be High or Low next month?

One row per segment (specialty x age band x gender) and month t. Every feature uses data from
month t-1 or earlier; the columns that describe month t itself (the share, the visits, the
market share, the label) are kept apart with a `y_` prefix and must never be used as inputs.

A prediction is made only for segments that had enough category visits in month t-1 to have a
reliable history. The label is the segment's month-t share against the market-wide month-t
share: **High** if the whole Wilson interval for the segment's share is above the market,
**Low** if it is entirely below, **Undetermined** otherwise (those rows are not scored). A
two-label fallback (raw above/below, segments with enough visits last month) exists for the
case where the audit shows too few rows survive the interval rule.

One accepted detail: rare specialties are grouped with `group_rare_specialties`, which counts
rows over the whole table. It uses which specialties appear, never any visit count or share,
and is the same rule the other feature sets use.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oa_market_intelligence.features.segment import (
    AGE_BANDS_ORDERED,
    group_rare_specialties,
)

HIGH, LOW, UNDETERMINED = "High", "Low", "Undetermined"
WILSON_Z = 1.96  # 95% interval
MIN_HISTORY_VISITS = 20  # category visits needed in month t-1 to make a prediction
MIN_VISITS_TWO_LABEL = 100  # fallback scheme: larger segments only
MIN_SCORED_ROWS = 3000  # audit thresholds, fixed in the Phase 4 plan before any model ran
MIN_CLASS_SHARE = 0.15

TASK_A_FEATURES = [
    "specialty_grouped",
    "specialty_prior_share",
    "seg_prior_share",
    "age_ordinal",
    "gender_FEMALE",
    "gender_MALE",
    "seg_share_lag1",
    "seg_share_roll3",
    "seg_margin_lag1",
    "seg_log_visits_lag1",
    "seg_high_lag1",  # raw sign last month: share above the market-wide share, 1 or 0
    "market_share_lag1",
    "market_change_lag1",
    "month_sin",
    "month_cos",
]
OUTCOME_COLUMNS = ["y_label", "y_raw_above", "y_share", "y_visits", "y_market"]
_ID_COLUMNS = ["month_id", "specialty_name", "age_band", "gender", "segment"]


def wilson_interval(successes, trials, z: float = WILSON_Z):
    """Wilson score interval for a proportion (arrays or scalars)."""
    successes = np.asarray(successes, dtype=float)
    trials = np.asarray(trials, dtype=float)
    p = successes / trials
    denominator = 1 + z**2 / trials
    centre = (p + z**2 / (2 * trials)) / denominator
    half = z * np.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / denominator
    return np.clip(centre - half, 0.0, 1.0), np.clip(centre + half, 0.0, 1.0)


def interval_label(successes, trials, market, z: float = WILSON_Z) -> str:
    """High, Low or Undetermined for one segment-month."""
    low, high = wilson_interval(successes, trials, z)
    if low > market:
        return HIGH
    if high < market:
        return LOW
    return UNDETERMINED


def _lagged(frame: pd.DataFrame, columns: list[str], lag: int, suffix: str) -> pd.DataFrame:
    shifted = frame[["segment", "mi", *columns]].copy()
    shifted["mi"] = shifted["mi"] + lag
    return shifted.rename(columns={c: f"{c}_{suffix}" for c in columns})


def build_task_a_rows(
    seg: pd.DataFrame,
    *,
    scheme: str = "interval",
    z: float = WILSON_Z,
    min_history_visits: int = MIN_HISTORY_VISITS,
    min_visits_two_label: int = MIN_VISITS_TWO_LABEL,
) -> pd.DataFrame:
    """The Task A rows for a segment table (`load_segment_gold` output).

    `scheme='interval'` gives High/Low/Undetermined; `scheme='two_label'` is the fallback."""
    if scheme not in ("interval", "two_label"):
        raise ValueError("scheme must be 'interval' or 'two_label'")
    seg = seg.copy()
    months = sorted(seg["month_id"].unique())
    seg["mi"] = seg["month_id"].map({m: i for i, m in enumerate(months)})
    seg["segment"] = seg["specialty_name"] + "|" + seg["age_band"] + "|" + seg["gender"]
    seg["z"] = seg["branded_injectable_visits"].astype(float)
    seg["t"] = seg["total_category_visits"].astype(float)
    seg["share"] = seg["z"] / seg["t"]
    seg = seg.sort_values(["segment", "mi"]).reset_index(drop=True)

    market_totals = seg.groupby("mi")[["z", "t"]].sum()
    market = market_totals["z"] / market_totals["t"]
    seg["market"] = seg["mi"].map(market)

    for lag in (1, 2, 3):
        seg = seg.merge(
            _lagged(seg, ["share", "t", "market"], lag, f"l{lag}"),
            on=["segment", "mi"], how="left",
        )

    # history that ends at t-1: the segment's and its specialty's earlier cumulative shares
    own = seg.groupby("segment")[["z", "t"]].cumsum()
    seg["seg_prior_share"] = (
        (own["z"] - seg["z"]) / (own["t"] - seg["t"]).replace(0, np.nan)
    )
    by_specialty = seg.groupby(["specialty_name", "mi"])[["z", "t"]].sum().reset_index()
    cumulative = by_specialty.groupby("specialty_name")[["z", "t"]].cumsum()
    earlier_t = (cumulative["t"] - by_specialty["t"]).replace(0, np.nan)
    by_specialty["specialty_prior_share"] = (cumulative["z"] - by_specialty["z"]) / earlier_t
    seg = seg.merge(
        by_specialty[["specialty_name", "mi", "specialty_prior_share"]],
        on=["specialty_name", "mi"], how="left",
    )

    month_number = seg["month_id"] % 100
    seg["specialty_grouped"] = group_rare_specialties(seg["specialty_name"])
    seg["age_ordinal"] = seg["age_band"].map({a: i for i, a in enumerate(AGE_BANDS_ORDERED)})
    seg["gender_FEMALE"] = (seg["gender"] == "FEMALE").astype(int)
    seg["gender_MALE"] = (seg["gender"] == "MALE").astype(int)
    seg["month_sin"] = np.sin(2 * np.pi * month_number / 12)
    seg["month_cos"] = np.cos(2 * np.pi * month_number / 12)
    seg["seg_share_lag1"] = seg["share_l1"]
    seg["seg_share_roll3"] = seg[["share_l1", "share_l2", "share_l3"]].mean(axis=1)
    seg["market_share_lag1"] = seg["market_l1"]
    market_by_mi = market.to_dict()
    seg["market_change_lag1"] = seg["mi"].map(
        lambda i: market_by_mi.get(i - 1, np.nan) - market_by_mi.get(i - 2, np.nan)
    )
    seg["seg_margin_lag1"] = seg["seg_share_lag1"] - seg["market_share_lag1"]
    seg["seg_log_visits_lag1"] = np.log1p(seg["t_l1"])
    seg["seg_high_lag1"] = (seg["seg_share_lag1"] > seg["market_share_lag1"]).astype(float)
    seg.loc[seg["seg_share_lag1"].isna(), "seg_high_lag1"] = np.nan

    seg["y_share"] = seg["share"]
    seg["y_visits"] = seg["t"]
    seg["y_market"] = seg["market"]
    seg["y_raw_above"] = (seg["share"] > seg["market"]).astype(int)

    keep = (seg["mi"] >= 2) & (seg["t_l1"] >= min_history_visits)
    if scheme == "interval":
        low, high = wilson_interval(seg["z"], seg["t"], z)
        seg["y_label"] = np.select(
            [low > seg["market"], high < seg["market"]], [HIGH, LOW], default=UNDETERMINED
        )
    else:
        keep &= seg["t_l1"] >= min_visits_two_label  # last month's volume: known at prediction time
        seg["y_label"] = np.where(seg["y_raw_above"] == 1, HIGH, LOW)

    rows = seg.loc[keep, _ID_COLUMNS + TASK_A_FEATURES + OUTCOME_COLUMNS]
    return rows.sort_values(["month_id", "segment"]).reset_index(drop=True)


def _balanced_accuracy(actual: pd.Series, predicted: pd.Series) -> float:
    recalls = [
        float((predicted[actual == label] == label).mean()) for label in (HIGH, LOW)
        if (actual == label).any()
    ]
    return float(np.mean(recalls))


def audit_labels(
    rows: pd.DataFrame,
    *,
    min_scored: int = MIN_SCORED_ROWS,
    min_class_share: float = MIN_CLASS_SHARE,
) -> dict:
    """Is the label scheme usable? Counts the classes, applies the two thresholds from the plan
    (enough scored rows, and each class a large enough share of them), and reports how well
    'same side of the market as last month' does on the scored rows."""
    labels = rows["y_label"]
    n_high, n_low = int((labels == HIGH).sum()), int((labels == LOW).sum())
    n_scored = n_high + n_low
    high_share = n_high / n_scored if n_scored else 0.0
    low_share = n_low / n_scored if n_scored else 0.0
    reason = "ok"
    if n_scored < min_scored:
        reason = f"only {n_scored} scored rows, fewer than {min_scored}"
    elif min(high_share, low_share) < min_class_share:
        reason = f"a class is under {min_class_share:.0%} of the scored rows"
    scored = rows[labels.isin([HIGH, LOW])]
    persistence = scored["seg_high_lag1"].map({1.0: HIGH, 0.0: LOW})
    return {
        "n_prediction_rows": int(len(rows)),
        "n_high": n_high,
        "n_low": n_low,
        "n_undetermined": int((labels == UNDETERMINED).sum()),
        "n_scored": n_scored,
        "high_share": high_share,
        "low_share": low_share,
        "passes": reason == "ok",
        "reason": reason,
        "persistence_balanced_accuracy": _balanced_accuracy(scored["y_label"], persistence),
    }
