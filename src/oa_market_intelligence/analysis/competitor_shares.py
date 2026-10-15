"""M6 (DL-73): Zilretta's share against its competitors, from IQVIA's product-level visits.

The product groups are frozen in `data/reference/competitor_groups.csv` (one row per product, the
rule that put it there beside it) and were fixed before any group-level number was looked at.
The tables are descriptive: shares of product-visits inside IQVIA's panel (a patient visit can
count under more than one product), with no test and no cause claimed. The panel-coverage caveat
of DL-62 applies to every number here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine

GROUPS_CSV = Path(__file__).resolve().parents[3] / "data" / "reference" / "competitor_groups.csv"
GROUPS = ("zilretta", "triamcinolone_ir", "other_injectable_corticosteroid", "other")
INJECTABLE_STEROIDS = GROUPS[:3]  # the market of this analysis: Zilretta plus the steroid groups
TRIAMCINOLONE_NAMES = (
    "KENALOG",
    "TRIAMCINOLONE",
    "ARISTOCORT",
    "ARISTOSPAN",
    "HEXATRIONE",
    "TAC-3",
)
PEAK_YEAR = 2022
LATEST_FROM, LATEST_TO = 202408, 202507  # the latest twelve months in the extract


def assign_group(product_name: str, treatment_category: str) -> tuple[str, str]:
    """The group of a product and the rule that placed it (used once to write the frozen file)."""
    name = product_name.upper()
    if name.startswith("ZILRETTA"):
        return "zilretta", "product name is Zilretta"
    if treatment_category == "generic_corticosteroid":
        if any(name.startswith(prefix) for prefix in TRIAMCINOLONE_NAMES):
            return "triamcinolone_ir", "corticosteroid whose name is a triamcinolone product"
        return "other_injectable_corticosteroid", "other product in the corticosteroid category"
    return "other", "not Zilretta and not in the corticosteroid category"


def load_groups(path: Path = GROUPS_CSV) -> pd.DataFrame:
    groups = pd.read_csv(path)
    if groups["product_name"].duplicated().any():
        raise ValueError("a product appears twice in the group file")
    if not set(groups["group"]) <= set(GROUPS):
        raise ValueError("the group file holds an unknown group")
    return groups


def monthly_group_visits(engine: Engine, groups: pd.DataFrame | None = None) -> pd.DataFrame:
    """Visits per month and group (one column per group). Every product must be in the file."""
    groups = load_groups() if groups is None else groups
    with engine.connect() as conn:
        frame = pd.read_sql(
            "SELECT f.month_id, p.product_name, SUM(f.patient_visits) AS visits "
            "FROM fact_product_visits f JOIN dim_product p USING (product_id) "
            "GROUP BY f.month_id, p.product_name",
            conn,
        )
    merged = frame.merge(groups[["product_name", "group"]], on="product_name", how="left")
    if merged["group"].isna().any():
        missing = sorted(merged.loc[merged["group"].isna(), "product_name"].unique())
        raise ValueError(f"products missing from the group file: {missing}")
    merged["visits"] = merged["visits"].astype(float)
    wide = merged.pivot_table(
        index="month_id", columns="group", values="visits", aggfunc="sum", fill_value=0
    )
    return wide.reindex(columns=list(GROUPS), fill_value=0.0)


def _share(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def period_table(monthly: pd.DataFrame, period: pd.Series) -> pd.DataFrame:
    """Visits and shares per period (a year or a quarter of each month)."""
    summed = monthly.groupby(period.reindex(monthly.index)).sum()
    steroids = summed[list(INJECTABLE_STEROIDS)].sum(axis=1)
    out = pd.DataFrame(
        {
            "months": monthly.groupby(period.reindex(monthly.index)).size(),
            "zilretta_visits": summed["zilretta"],
            "triamcinolone_ir_visits": summed["triamcinolone_ir"],
            "other_injectable_corticosteroid_visits": summed["other_injectable_corticosteroid"],
            "injectable_steroid_visits": steroids,
            "zilretta_share_of_injectable_steroids_pct": 100 * _share(summed["zilretta"], steroids),
            "zilretta_share_against_triamcinolone_ir_pct": 100
            * _share(summed["zilretta"], summed["zilretta"] + summed["triamcinolone_ir"]),
        }
    )
    return out


def who_gained(monthly: pd.DataFrame, *, peak_year: int = PEAK_YEAR) -> dict:
    """Each group's visits and share of injectable-steroid visits in the peak year against the
    latest twelve months, with the change in the whole category's volume."""
    year = monthly.index // 100
    peak = monthly[year == peak_year][list(INJECTABLE_STEROIDS)].sum()
    latest = monthly[(monthly.index >= LATEST_FROM) & (monthly.index <= LATEST_TO)][
        list(INJECTABLE_STEROIDS)
    ].sum()
    rows = {}
    for group in INJECTABLE_STEROIDS:
        rows[group] = {
            "visits_peak_year": float(peak[group]),
            "visits_latest_12": float(latest[group]),
            "share_peak_year_pct": float(100 * peak[group] / peak.sum()),
            "share_latest_12_pct": float(100 * latest[group] / latest.sum()),
            "visits_change_pct": float(100 * (latest[group] / peak[group] - 1)),
        }
    return {
        "peak_year": peak_year,
        "latest_window": [LATEST_FROM, LATEST_TO],
        "groups": rows,
        "category_visits_peak_year": float(peak.sum()),
        "category_visits_latest_12": float(latest.sum()),
        "category_visits_change_pct": float(100 * (latest.sum() / peak.sum() - 1)),
    }


def competitor_tables(engine: Engine, groups: pd.DataFrame | None = None) -> dict:
    """Everything M6 reports, as JSON-safe values."""
    monthly = monthly_group_visits(engine, groups)
    year = pd.Series(monthly.index // 100, index=monthly.index)
    quarter = pd.Series(
        [f"{m // 100}Q{(m % 100 - 1) // 3 + 1}" for m in monthly.index], index=monthly.index
    )
    return {
        "by_year": period_table(monthly, year)
        .round(4)
        .reset_index(names="year")
        .to_dict("records"),
        "by_quarter": period_table(monthly, quarter)
        .round(4)
        .reset_index(names="quarter")
        .to_dict("records"),
        "who_gained": who_gained(monthly),
        "caveat": (
            "Shares of product-visits inside IQVIA's panel; a patient visit can count under more "
            "than one product. The panel-coverage caveat of DL-62 applies: the size of the fall "
            "after 2022 is not confirmed by company sales."
        ),
    }
