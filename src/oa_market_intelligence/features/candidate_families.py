"""The candidate feature families of the feature plan (DL-71, docs/feature_engineering_plan.md).

Task A (segment share, one row per segment and month) gets FA1 to FA6, Task B (the national share)
gets FB1 and FA5. Every column uses information from month t-1 or earlier: the IQVIA-derived
families are checked by the generic test that rewrites everything from month t onward, and the
outside series (FA5, FA6) come from the as-of table, so a value is used only once it was public.

Missing values follow the fixed rule of the plan: a column that can be missing is zero-filled and
has a `*_missing` indicator (1 where the value could not be computed), so no NaN reaches a model.

The families are built here and not run: whether any of them helps is decided by the pre-registered
test, not by this module.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oa_market_intelligence.availability import add_months
from oa_market_intelligence.mart import signals_as_of

SHRINK_K = (5, 20, 80)  # FA1: the weight of the specialty prior, chosen inside the training window
MONTHS_SINCE_CAP = 24  # FA2: "never" and long gaps both count as 24 months
EVENT_CAP = 36  # FA5: months since the last known event, never = 36
KEYS = ["month_id", "specialty_name", "age_band", "gender"]

FAMILY_COLUMNS = {
    "FA1": [f"fa1_{name}_k{k}" for k in SHRINK_K for name in ("logit_shrunk", "weight")],
    "FA2": ["fa2_months_active_prior", "fa2_months_since_branded", "fa2_log_cum_branded"],
    "FA3": ["fa3_logit_spec_share_lag1", "fa3_spec_change_3m", "fa3_missing"],
    "FA4": ["fa4_seasonal_gap", "fa4_missing"],
    "FA5": [
        "fa5_price_ratio",
        "fa5_price_ratio_chg_4q",
        "fa5_sales_yoy",
        "fa5_months_since_event",
        "fa5_price_chg_missing",
        "fa5_sales_missing",
    ],
    "FA6": ["fa6_medicare_adoption", "fa6_medicare_missing"],
}
FB1_COLUMNS = ["fb1_seasonal_gap", "fb1_missing"]


def _logit(p, eps: float = 1e-3) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def _month_index(month: int) -> int:
    return (month // 100) * 12 + (month % 100)


# ---------------------------------------------------------------- IQVIA-derived families (Task A)


def _prepare(seg: pd.DataFrame) -> pd.DataFrame:
    seg = seg.copy()
    months = sorted(seg["month_id"].unique())
    seg["mi"] = seg["month_id"].map({m: i for i, m in enumerate(months)})
    seg["segment"] = seg["specialty_name"] + "|" + seg["age_band"] + "|" + seg["gender"]
    seg["z"] = seg["branded_injectable_visits"].astype(float)
    seg["t"] = seg["total_category_visits"].astype(float)
    return seg.sort_values(["segment", "mi"]).reset_index(drop=True)


def _share_table(seg: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Volume-weighted Zilretta share of each group, one row per group and observed month."""
    grouped = seg.groupby([*by, "mi", "month_id"], as_index=False)[["z", "t"]].sum()
    grouped["share"] = grouped["z"] / grouped["t"]
    return grouped


def _fa1(seg: pd.DataFrame) -> pd.DataFrame:
    """The segment's cumulative share to t-1 shrunk toward its specialty's (the market's when the
    specialty has no history yet)."""
    own = seg.groupby("segment")[["z", "t"]].cumsum()
    seg_z, seg_t = own["z"] - seg["z"], own["t"] - seg["t"]
    by_spec = seg.groupby(["specialty_name", "mi"])[["z", "t"]].sum().reset_index()
    cum = by_spec.groupby("specialty_name")[["z", "t"]].cumsum()
    by_spec["sz"], by_spec["st"] = cum["z"] - by_spec["z"], cum["t"] - by_spec["t"]
    market = seg.groupby("mi")[["z", "t"]].sum().cumsum()
    market_prior = (market["z"] - seg.groupby("mi")["z"].sum()) / (
        market["t"] - seg.groupby("mi")["t"].sum()
    ).replace(0, np.nan)
    merged = seg[["specialty_name", "mi"]].merge(
        by_spec[["specialty_name", "mi", "sz", "st"]], how="left"
    )
    spec_prior = (merged["sz"] / merged["st"].replace(0, np.nan)).to_numpy()
    prior = np.where(np.isnan(spec_prior), seg["mi"].map(market_prior).to_numpy(), spec_prior)
    out = {}
    for k in SHRINK_K:
        shrunk = (seg_z.to_numpy() + k * prior) / (seg_t.to_numpy() + k)
        out[f"fa1_logit_shrunk_k{k}"] = _logit(shrunk)
        out[f"fa1_weight_k{k}"] = seg_t.to_numpy() / (seg_t.to_numpy() + k)
    return pd.DataFrame(out, index=seg.index)


def _fa2(seg: pd.DataFrame) -> pd.DataFrame:
    active = seg.groupby("segment").cumcount()
    position = np.where(seg["z"] > 0, seg["mi"], np.nan)
    last = (
        pd.Series(position, index=seg.index)
        .groupby(seg["segment"])
        .ffill()
        .groupby(seg["segment"])
        .shift(1)
    )
    since = (seg["mi"] - last).fillna(MONTHS_SINCE_CAP).clip(upper=MONTHS_SINCE_CAP)
    cumulative = seg.groupby("segment")["z"].cumsum() - seg["z"]
    return pd.DataFrame(
        {
            "fa2_months_active_prior": active.astype(float),
            "fa2_months_since_branded": since.astype(float),
            "fa2_log_cum_branded": np.log1p(cumulative),
        },
        index=seg.index,
    )


def _fa3(seg: pd.DataFrame) -> pd.DataFrame:
    shares = _share_table(seg, ["specialty_name"])
    wide = shares.pivot(index="mi", columns="specialty_name", values="share")
    wide = wide.reindex(range(int(seg["mi"].max()) + 1))
    lag1 = wide.shift(1).stack(future_stack=True).rename("lag1")
    lag4 = wide.shift(4).stack(future_stack=True).rename("lag4")
    table = pd.concat([lag1, lag4], axis=1).reset_index()
    table.columns = ["mi", "specialty_name", "lag1", "lag4"]
    merged = seg[["specialty_name", "mi"]].merge(table, how="left", on=["specialty_name", "mi"])
    change = merged["lag1"] - merged["lag4"]
    missing = (merged["lag1"].isna() | change.isna()).astype(float)
    return pd.DataFrame(
        {
            "fa3_logit_spec_share_lag1": np.where(missing == 1, 0.0, _logit(merged["lag1"])),
            "fa3_spec_change_3m": change.fillna(0.0).to_numpy(),
            "fa3_missing": missing.to_numpy(),
        },
        index=seg.index,
    )


def seasonal_gap(shares: pd.DataFrame) -> pd.Series:
    """For each (year, month) of a share series: the mean over *earlier years* of that calendar
    month's share minus the year's own share. `shares` has year, month, z and t (visits); the
    result is aligned to its index and NaN where no earlier year has that calendar month."""
    frame = shares.copy()
    year_share = frame.groupby("year")[["z", "t"]].sum()
    year_share = year_share["z"] / year_share["t"]
    frame["share"] = frame["z"] / frame["t"]
    frame["gap"] = frame["share"] - frame["year"].map(year_share)
    wide = frame.pivot(index="year", columns="month", values="gap").sort_index()
    earlier = wide.expanding().mean().shift(1)  # years before this one only
    stacked = earlier.stack(future_stack=True).rename("value")
    keyed = frame.set_index(["year", "month"]).index
    return pd.Series(stacked.reindex(keyed).to_numpy(), index=frame.index)


def _fa4(seg: pd.DataFrame) -> pd.DataFrame:
    frame = seg.assign(year=seg["month_id"] // 100, month=seg["month_id"] % 100)
    by = frame.groupby(["specialty_name", "year", "month"], as_index=False)[["z", "t"]].sum()
    parts = []
    for _, group in by.groupby("specialty_name"):
        parts.append(group.assign(gap=seasonal_gap(group[["year", "month", "z", "t"]]).to_numpy()))
    table = pd.concat(parts)[["specialty_name", "year", "month", "gap"]]
    merged = frame[["specialty_name", "year", "month"]].merge(table, how="left")
    missing = merged["gap"].isna().astype(float)
    return pd.DataFrame(
        {
            "fa4_seasonal_gap": merged["gap"].fillna(0.0).to_numpy(),
            "fa4_missing": missing.to_numpy(),
        },
        index=seg.index,
    )


# ---------------------------------------------------------------- outside series (FA5, FA6)


def market_outside_features(signals: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """FA5, one row per IQVIA month: what was known as of the previous month about the price ratio,
    company sales and events."""
    rows = []
    for month in months:
        as_of = add_months(month, -1)
        known = signals[signals["available_from_month"] <= as_of]
        row = {"month_id": month}

        price = known[known["signal_id"] == "asp.price_ratio"].sort_values("period_end_month")
        row["fa5_price_ratio"] = float(price["value"].iloc[-1]) if len(price) else 0.0
        row["fa5_price_ratio_chg_4q"], row["fa5_price_chg_missing"] = 0.0, 1.0
        if len(price):
            latest = price.iloc[-1]
            earlier = price[
                price["period_end_month"] == add_months(int(latest["period_end_month"]), -12)
            ]
            if len(earlier):
                row["fa5_price_ratio_chg_4q"] = float(latest["value"] - earlier["value"].iloc[0])
                row["fa5_price_chg_missing"] = 0.0
        row["fa5_price_ratio"] = row["fa5_price_ratio"] if len(price) else 0.0

        sales = known[known["signal_id"] == "company.net_sales_usd"].sort_values("period_end_month")
        row["fa5_sales_yoy"], row["fa5_sales_missing"] = 0.0, 1.0
        if len(sales):
            latest = sales.iloc[-1]
            earlier = sales[
                sales["period_end_month"] == add_months(int(latest["period_end_month"]), -12)
            ]
            if len(earlier) and earlier["value"].iloc[0] > 0:
                row["fa5_sales_yoy"] = float(latest["value"] / earlier["value"].iloc[0] - 1)
                row["fa5_sales_missing"] = 0.0

        events = known[known["signal_id"] == "event.count_in_month"]
        if len(events):
            gap = _month_index(as_of) - _month_index(int(events["period_end_month"].max()))
            row["fa5_months_since_event"] = float(min(max(gap, 0), EVENT_CAP))
        else:
            row["fa5_months_since_event"] = float(EVENT_CAP)
        rows.append(row)
    return pd.DataFrame(rows)[["month_id", *FAMILY_COLUMNS["FA5"]]]


def medicare_adoption_features(signals: pd.DataFrame, seg: pd.DataFrame) -> pd.DataFrame:
    """FA6, aligned to `seg`: the Medicare adoption rate of the segment's specialty group as known
    as of the previous month; missing (0 and an indicator) when unknown or not in the 11 groups."""
    adoption = signals[signals["signal_id"] == "medicare.adoption_rate"]
    value = pd.Series(np.nan, index=seg.index)
    for month, group in seg.groupby("month_id"):
        known = signals_as_of(adoption, add_months(int(month), -1))
        lookup = known.set_index("specialty_group")["value"]
        value.loc[group.index] = group["specialty_name"].map(lookup).to_numpy()
    missing = value.isna().astype(float)
    return pd.DataFrame(
        {"fa6_medicare_adoption": value.fillna(0.0), "fa6_medicare_missing": missing},
        index=seg.index,
    )


# ---------------------------------------------------------------- the entry points


def task_a_family_columns(
    seg: pd.DataFrame, signals: pd.DataFrame | None = None, families: tuple[str, ...] | None = None
) -> pd.DataFrame:
    """The candidate columns for every segment-month of a segment table (`load_segment_gold`
    output), keyed by month, specialty, age band and gender. FA5 and FA6 need `signals` (the
    long table of `mart.build_signals`); without it they are left out."""
    wanted = families or tuple(FAMILY_COLUMNS)
    prepared = _prepare(seg)
    pieces = [prepared[KEYS]]
    builders = {"FA1": _fa1, "FA2": _fa2, "FA3": _fa3, "FA4": _fa4}
    for name in wanted:
        if name in builders:
            pieces.append(builders[name](prepared))
    out = pd.concat(pieces, axis=1)
    months = sorted(prepared["month_id"].unique())
    if "FA5" in wanted and signals is not None:
        out = out.merge(market_outside_features(signals, months), on="month_id", how="left")
    if "FA6" in wanted and signals is not None:
        out = pd.concat([out, medicare_adoption_features(signals, prepared)], axis=1)
    return out


def task_b_family_columns(
    monthly: pd.DataFrame, signals: pd.DataFrame | None = None
) -> pd.DataFrame:
    """FB1 (and FA5 if `signals` is given) for the national series: `monthly` has `month_id`,
    `branded_injectable_visits` and a total (`total_category_visits`, or the three category columns
    of `gold_visit_share_monthly`)."""
    frame = monthly.copy()
    if "total_category_visits" not in frame.columns:
        frame["total_category_visits"] = (
            frame["branded_injectable_visits"]
            + frame["generic_corticosteroid_visits"]
            + frame["nsaid_otc_visits"]
        )
    shares = pd.DataFrame(
        {
            "year": frame["month_id"] // 100,
            "month": frame["month_id"] % 100,
            "z": frame["branded_injectable_visits"].astype(float),
            "t": frame["total_category_visits"].astype(float),
        },
        index=frame.index,
    )
    gap = seasonal_gap(shares)
    out = pd.DataFrame(
        {
            "month_id": frame["month_id"],
            "fb1_seasonal_gap": gap.fillna(0.0),
            "fb1_missing": gap.isna().astype(float),
        }
    )
    if signals is not None:
        out = out.merge(
            market_outside_features(signals, sorted(frame["month_id"])), on="month_id", how="left"
        )
    return out
