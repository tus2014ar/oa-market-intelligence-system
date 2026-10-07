"""Step 5: do the headline conclusions survive the known data problems?

The Q1 and Q3 analyses are re-run on a baseline and with each of three exclusions (the
suspected mis-coded PEDIATRICS prescriber, the COVID months, the Mar to Jul 2024 volume dip).
Each headline conclusion has one pass/fail rule, written into the Phase 4 plan before the
real run; a conclusion is *robust* only if its rule holds in every run, otherwise *fragile*.
"""

from __future__ import annotations

import pandas as pd

from oa_market_intelligence.analysis.adoption import (
    RARE_LABEL,
    bootstrap_adjusted_shares,
    combine_intervals,
    deviance_shares,
    group_specialties,
    split_half_stability,
)
from oa_market_intelligence.analysis.decomposition import bootstrap_decomposition, year_windows
from oa_market_intelligence.analysis.trend import bootstrap_break_locations, select_breaks
from oa_market_intelligence.features.monthly import COVID_SHOCK_MONTHS, DIP_2024_MONTHS

EXCLUSIONS = {
    "PEDIATRICS": ("specialty", "PEDIATRICS"),
    "COVID months": ("months", COVID_SHOCK_MONTHS),
    "2024 dip months": ("months", DIP_2024_MONTHS),
}
A1_MIN_SHARE = 0.25
A1_MAX_P = 0.01
A3_MIN_SPEARMAN = 0.6
D1_MIN_RATIO = 2.0
T1_MAX_P = 0.05


def apply_exclusion(
    df: pd.DataFrame, name: str, *, specialty_column: str = "specialty"
) -> pd.DataFrame:
    """The data without a specialty or without a window of months; 'baseline' removes nothing."""
    if name == "baseline":
        return df.copy()
    kind, value = EXCLUSIONS[name]
    if kind == "specialty":
        return df[df[specialty_column] != value].reset_index(drop=True)
    low, high = value
    return df[~df["month_id"].between(low, high)].reset_index(drop=True)


def share_series(df: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """Zilretta's share of the category total per month, with months missing from `df`
    filled in by linear interpolation (and flagged) so the series stays continuous."""
    totals = df.groupby("month_id")[["zilretta", "category"]].sum().reindex(months)
    share = totals["zilretta"] / totals["category"]
    out = pd.DataFrame(
        {
            "month_id": months,
            "share": share.interpolate(limit_direction="both").to_numpy(),
            "interpolated": share.isna().to_numpy(),
        }
    )
    return out


def side_of_overall(low: float, high: float, overall: float) -> str:
    if low > overall:
        return "above"
    if high < overall:
        return "below"
    return "not clear"


def rule_t1(p_first: float, break_months: list[int], baseline_intervals: list) -> bool:
    """A significant first break test, and a break inside the baseline 90% interval."""
    if p_first >= T1_MAX_P:
        return False
    return any(
        low <= month <= high for month in break_months for low, high in baseline_intervals
    )


def rule_d1(effects: dict) -> bool:
    """In every comparison the rate effect is negative and at least twice the mix effect."""
    return all(
        e["rate"] < 0 and abs(e["rate"]) >= D1_MIN_RATIO * abs(e["mix"]) for e in effects.values()
    )


def rule_a1(share: float, p: float) -> bool:
    return share >= A1_MIN_SHARE and p < A1_MAX_P


def rule_a2(
    baseline: pd.DataFrame, table: pd.DataFrame, overall_baseline: float, overall: float
) -> dict[str, bool]:
    """For every specialty that was clearly above or below the overall share at baseline
    (RARE group excluded), does it stay clearly on the same side?"""
    out: dict[str, bool] = {}
    for specialty in baseline.index:
        if specialty == RARE_LABEL:
            continue
        side = side_of_overall(
            baseline.loc[specialty, "low"], baseline.loc[specialty, "high"], overall_baseline
        )
        if side == "not clear":
            continue
        out[specialty] = bool(
            specialty in table.index
            and side_of_overall(table.loc[specialty, "low"], table.loc[specialty, "high"], overall)
            == side
        )
    return out


def rule_a3(spearman: float) -> bool:
    return spearman >= A3_MIN_SPEARMAN


def verdict(checks: dict[str, bool]) -> dict:
    failed = [name for name, held in checks.items() if not held]
    return {"label": "fragile" if failed else "robust", "failed": failed}


def _month_interval(months: list[int], interval: tuple[int, int]) -> tuple[int, int]:
    return months[interval[0]], months[interval[1]]


def _run_one(df, name, months, windows, *, n_null, n_boot, n_boot_stability, seed) -> dict:
    d = apply_exclusion(df, name)
    remaining = set(d["month_id"].unique())

    # Q1 trend: share series (excluded months interpolated), breaks and their intervals
    y = share_series(d, months)["share"].to_numpy() * 100
    fit = select_breaks(y, n_null=n_null, seed=seed)
    intervals = [
        _month_interval(months, interval)
        for interval in bootstrap_break_locations(y, fit.n_breaks, n_boot=n_boot, seed=seed)
    ]
    trend = {
        "p_first": fit.p_values[0] if fit.p_values else 1.0,
        "n_breaks": fit.n_breaks,
        "break_months": [months[b] for b in fit.breaks],
        "intervals": intervals,
    }

    # Q1 decomposition by specialty, first window against last and third against last
    counts = (
        d.groupby(["month_id", "specialty"])[["zilretta", "category"]].sum().reset_index()
        .rename(columns={"specialty": "group"})
    )
    pairs = {"first_to_last": (0, len(windows) - 1)}
    if len(windows) >= 4:
        pairs["third_to_last"] = (2, len(windows) - 1)
    decomposition = {}
    for label, (i, j) in pairs.items():
        result = bootstrap_decomposition(
            counts,
            [m for m in windows[i] if m in remaining],
            [m for m in windows[j] if m in remaining],
            n_boot=n_boot,
            seed=seed,
        )
        decomposition[label] = {key: result[key]["estimate"] for key in ("total", "mix", "rate")}

    # Q3 adoption by specialty
    grouped = group_specialties(d)
    ds = deviance_shares(grouped)
    by_month = bootstrap_adjusted_shares(grouped, by="month", n_boot=n_boot, seed=seed)["table"]
    by_segment = bootstrap_adjusted_shares(grouped, by="segment", n_boot=n_boot, seed=seed)["table"]
    stability = split_half_stability(grouped, n_boot=n_boot_stability, seed=seed)
    adoption = {
        "specialty_share": ds["specialty"]["share"],
        "specialty_p": ds["specialty_p"],
        "overall": float(grouped["zilretta"].sum() / grouped["category"].sum()),
        "table": combine_intervals(by_month, by_segment),
        "spearman": stability["spearman"],
    }
    return {"n_months": len(remaining), "trend": trend, "decomposition": decomposition,
            "adoption": adoption}


def run_sensitivity(
    df: pd.DataFrame,
    *,
    n_null: int = 1000,
    n_boot: int = 300,
    n_boot_stability: int = 100,
    seed: int = 0,
) -> dict:
    """Run every analysis on the baseline and each exclusion and apply the verdict rules.

    `df` has one row per month, specialty, age band and gender with `zilretta` and `category`
    visit counts (the output of `load_adoption_data`)."""
    months = sorted(int(m) for m in df["month_id"].unique())
    windows = year_windows(months)
    names = ["baseline", *EXCLUSIONS]
    runs = {
        name: _run_one(
            df, name, months, windows, n_null=n_null, n_boot=n_boot,
            n_boot_stability=n_boot_stability, seed=seed,
        )
        for name in names
    }
    base = runs["baseline"]
    rules: dict[str, dict] = {}
    for name, run in runs.items():
        rules[name] = {
            "T1": rule_t1(run["trend"]["p_first"], run["trend"]["break_months"],
                          base["trend"]["intervals"]),
            "D1": rule_d1(run["decomposition"]),
            "A1": rule_a1(run["adoption"]["specialty_share"], run["adoption"]["specialty_p"]),
            "A2": rule_a2(base["adoption"]["table"], run["adoption"]["table"],
                          base["adoption"]["overall"], run["adoption"]["overall"]),
            "A3": rule_a3(run["adoption"]["spearman"]),
        }
    verdicts = {
        key: verdict({name: rules[name][key] for name in names}) for key in ("T1", "D1", "A1", "A3")
    }
    specialties = list(rules["baseline"]["A2"])
    by_specialty = {
        s: verdict({name: rules[name]["A2"].get(s, False) for name in names}) for s in specialties
    }
    fragile = [s for s, v in by_specialty.items() if v["label"] == "fragile"]
    verdicts["A2"] = {
        "label": "fragile" if fragile else "robust",
        "failed": fragile,
        "by_specialty": by_specialty,
    }
    return {"runs": runs, "rules": rules, "verdicts": verdicts}

