"""E4 (what lines up with the 2022 turn?) and E2b (company sales against IQVIA visits).

Pure functions on small series, so each pre-registered rule can be tested on a planted case.
Everything is association or description: none of it says what caused anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from oa_market_intelligence.external.analysis import config

H4_CATEGORIES = (
    "branded_injectable_visits",
    "generic_corticosteroid_visits",
    "nsaid_otc_visits",
)
TOLERANCE_DIGITS = 9  # a threshold met exactly (for example -10%) must count despite float noise


def months_between(first: int, second: int) -> int:
    """Months from `first` to `second`, both YYYYMM."""
    return (second // 100 - first // 100) * 12 + (second % 100 - first % 100)


def _check_consecutive(index) -> None:
    months = list(index)
    if any(months_between(a, b) != 1 for a, b in zip(months, months[1:], strict=False)):
        raise ValueError("the months must be consecutive, with no gaps")


def _at_least(value: float, threshold: float) -> bool:
    """value >= threshold, tolerant of floating-point noise at the boundary."""
    return round(value - threshold, TOLERANCE_DIGITS) >= 0


# ---------------------------------------------------------------- H1 promotion


def rolling_change(series: pd.Series, months: int = 6) -> pd.DataFrame:
    """Per month: the mean of the latest `months` against the mean of the `months` before."""
    _check_consecutive(series.index)
    recent = series.rolling(months).mean()
    previous = recent.shift(months)
    out = pd.DataFrame(
        {
            "month_id": series.index,
            "recent_mean": recent.to_numpy(),
            "previous_mean": previous.to_numpy(),
        }
    )
    out["change"] = out["recent_mean"] / out["previous_mean"] - 1
    return out


def h1_verdict(
    series: pd.Series,
    window: tuple[int, int] = config.H1_WINDOW,
    threshold: float = config.H1_DECLINE,
) -> dict:
    """Supported if, in any month of the window, the 6-month mean is at least 25% below the
    previous 6-month mean."""
    table = rolling_change(series)
    inside = table[table["month_id"].between(*window)].dropna(subset=["change"])
    worst = inside.loc[inside["change"].idxmin()]
    met = inside[inside["change"].map(lambda change: _at_least(threshold, change))]
    return {
        "verdict": "supported" if len(met) else "not_supported",
        "worst_month": int(worst["month_id"]),
        "worst_change": float(worst["change"]),
        "n_months_meeting": int(len(met)),
        "first_month_meeting": int(met["month_id"].min()) if len(met) else None,
        "n_months_tested": int(len(inside)),
    }


# ---------------------------------------------------------------- lagged correlations


def _aligned_differences(promo: pd.Series, share: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    common = promo.index.intersection(share.index)
    _check_consecutive(common)
    return (
        promo.loc[common].diff().to_numpy()[1:],
        share.loc[common].diff().to_numpy()[1:],
    )


def _lag_correlations(dp: np.ndarray, ds: np.ndarray, max_lag: int) -> np.ndarray:
    """Pearson r between the promotion change `lag` months earlier and the share change."""
    out = np.empty(max_lag + 1)
    for lag in range(max_lag + 1):
        x = dp[: len(dp) - lag]
        y = ds[lag:]
        out[lag] = np.corrcoef(x, y)[0, 1] if x.std() > 0 and y.std() > 0 else np.nan
    return out


def lagged_first_difference_corr(promo: pd.Series, share: pd.Series, max_lag: int) -> pd.DataFrame:
    dp, ds = _aligned_differences(promo, share)
    r = _lag_correlations(dp, ds, max_lag)
    return pd.DataFrame({"lag": range(max_lag + 1), "r": r, "n": len(dp) - np.arange(max_lag + 1)})


def _block_shuffle(values: np.ndarray, block: int, rng) -> np.ndarray:
    pieces = [values[i : i + block] for i in range(0, len(values), block)]
    order = rng.permutation(len(pieces))
    return np.concatenate([pieces[i] for i in order])


def block_permutation_lags(
    promo: pd.Series,
    share: pd.Series,
    *,
    max_lag: int = config.H1_MAX_LAG,
    block: int = config.H1_BLOCK,
    n_perm: int = config.H1_N_PERM,
    seed: int = config.SEED,
    alpha: float = config.H1_ALPHA,
) -> pd.DataFrame:
    """Two-sided permutation p-value for each lag, shuffling blocks of the promotion changes so
    that their short-run pattern is kept. Bonferroni across the lags tested."""
    dp, ds = _aligned_differences(promo, share)
    observed = _lag_correlations(dp, ds, max_lag)
    rng = np.random.default_rng(seed)
    hits = np.zeros(max_lag + 1)
    for _ in range(n_perm):
        permuted = _lag_correlations(_block_shuffle(dp, block, rng), ds, max_lag)
        hits += np.abs(permuted) >= np.abs(observed)
    p_values = (1 + hits) / (1 + n_perm)
    level = alpha / (max_lag + 1)
    return pd.DataFrame(
        {
            "lag": range(max_lag + 1),
            "r": observed,
            "p_value": p_values,
            "alpha": level,
            "significant": p_values < level,
        }
    )


def e5_trigger(lags: pd.DataFrame) -> tuple[bool, list[int]]:
    """E5 (lagged promotion in the share forecast) runs only if some lag is significant."""
    hit = [int(lag) for lag in lags.loc[lags["significant"], "lag"]]
    return bool(hit), hit


# ---------------------------------------------------------------- H2 price


def price_per_mg(limit: float, mg_per_unit: float) -> float:
    return limit / mg_per_unit


def h2_verdict(ratio_by_quarter: dict[str, float]) -> dict:
    """Supported if the J3304-to-J3301 price ratio changes by at least 10% (either way)."""
    start = ratio_by_quarter[config.H2_FROM]
    end = ratio_by_quarter[config.H2_TO]
    change = end / start - 1
    met = _at_least(abs(change), config.H2_CHANGE)
    return {
        "verdict": "supported" if met else "not_supported",
        "change": float(change),
        "from": float(start),
        "to": float(end),
    }


# ---------------------------------------------------------------- H3 pass-through


def h3_verdict(break_month: int) -> dict:
    """The detected break (an existing result) against the month after pass-through ended."""
    distance = months_between(config.H3_ANCHOR_MONTH, break_month)
    inside = abs(distance) <= config.H3_WINDOW_MONTHS
    return {
        "verdict": "supported" if inside else "not_supported",
        "distance_months": distance,
        "break_month": break_month,
    }


# ---------------------------------------------------------------- H4 early-2024 dip


def h4_result(visits: pd.DataFrame) -> dict:
    """Supported (consistent with a data-capture artefact) if all three IQVIA categories are at
    least 10% below the same months of 2023 over March to July 2024."""
    first, last = config.H4_MONTHS

    def window(year: int) -> pd.DataFrame:
        month = visits["month_id"] % 100
        return visits[(visits["month_id"] // 100 == year) & month.between(first, last)]

    now, before = window(config.H4_YEAR), window(config.H4_BASE_YEAR)
    changes = {name: float(now[name].sum() / before[name].sum() - 1) for name in H4_CATEGORIES}
    met = all(_at_least(-config.H4_DROP, change) for change in changes.values())
    return {"verdict": "supported" if met else "not_supported", "changes": changes}


# ---------------------------------------------------------------- E2b company sales


def direction(previous: float, current: float, band: float = config.E2B_FLAT_BAND) -> str:
    return _label(current / previous - 1, band)


def _label(change: float, band: float = config.E2B_FLAT_BAND) -> str | None:
    if pd.isna(change):
        return None
    change = round(change, TOLERANCE_DIGITS)
    if abs(change) < band:
        return "flat"
    return "up" if change > 0 else "down"


def quarter_of(month_id: int) -> str:
    return f"{month_id // 100}Q{(month_id % 100 - 1) // 3 + 1}"


def quarterly_visits(monthly: pd.Series) -> pd.DataFrame:
    """Quarterly visit totals and the year-on-year change. A quarter is compared with the same
    quarter a year earlier on the months present in both (IQVIA's third quarter of 2019 has only
    August and September, so the third quarter of 2020 is compared on those two months)."""
    frame = monthly.rename("visits").to_frame().assign(quarter_id=lambda f: f.index.map(quarter_of))
    rows = []
    for quarter, part in frame.groupby("quarter_id"):
        matched = [m for m in part.index if (m - 100) in monthly.index]
        change = np.nan
        if matched:
            change = monthly.loc[matched].sum() / monthly.loc[[m - 100 for m in matched]].sum() - 1
        rows.append(
            {
                "quarter_id": quarter,
                "visits": float(part["visits"].sum()),
                "n_months": len(part),
                "n_matched_months": len(matched),
                "yoy_change": change,
            }
        )
    return pd.DataFrame(rows)


def quarterly_sales(revenue: pd.DataFrame) -> pd.DataFrame:
    """Quarterly net sales and the year-on-year change, from the quarter rows of the revenue
    table (the fourth quarters are derived there and flagged)."""
    quarters = revenue[revenue["period_type"] == "quarter"].copy()
    ends = pd.to_datetime(quarters["period_end"])
    quarters["quarter_id"] = ends.dt.year.astype(str) + "Q" + ends.dt.quarter.astype(str)
    quarters = quarters.sort_values("period_end").reset_index(drop=True)
    sales = quarters.set_index("quarter_id")["net_sales_usd"]

    def year_ago(quarter: str) -> str:
        return f"{int(quarter[:4]) - 1}{quarter[4:]}"

    quarters["yoy_sales_change"] = [
        sales[q] / sales[year_ago(q)] - 1 if year_ago(q) in sales.index else np.nan
        for q in quarters["quarter_id"]
    ]
    return quarters[["quarter_id", "company", "net_sales_usd", "yoy_sales_change", "derived"]]


def e2b_table(
    sales: pd.DataFrame, visits: pd.DataFrame, quarters: tuple[str, str] = config.E2B_QUARTERS
) -> pd.DataFrame:
    first, last = quarters
    merged = sales.merge(visits, on="quarter_id", how="inner")
    merged = merged[merged["quarter_id"].between(first, last)].sort_values("quarter_id")
    out = pd.DataFrame(
        {
            "quarter_id": merged["quarter_id"].to_numpy(),
            "company": merged["company"].to_numpy(),
            "net_sales_usd": merged["net_sales_usd"].to_numpy(),
            "iqvia_zilretta_visits": merged["visits"].to_numpy(),
            "yoy_sales_direction": [_label(c) for c in merged["yoy_sales_change"]],
            "yoy_visits_direction": [_label(c) for c in merged["yoy_change"]],
        }
    )
    both = out["yoy_sales_direction"].notna() & out["yoy_visits_direction"].notna()
    agree = (out["yoy_sales_direction"] == out["yoy_visits_direction"]).astype(float)
    out["directions_agree"] = agree.where(both)
    return out


def e2b_verdict(table: pd.DataFrame) -> dict:
    evaluated = table["directions_agree"].dropna()
    share = float(evaluated.mean()) if len(evaluated) else float("nan")
    ok = len(evaluated) > 0 and _at_least(share, config.E2B_AGREEMENT_MIN)
    return {
        "verdict": "consistent" if ok else "inconsistent",
        "agreement": share,
        "n_quarters": int(len(evaluated)),
        "n_agree": int(evaluated.sum()),
    }
