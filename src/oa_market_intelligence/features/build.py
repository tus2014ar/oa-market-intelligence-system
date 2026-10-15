"""Builds the two feature tables of the feature plan (DL-75) and writes them to a warehouse.

    PYTHONPATH=src python -m oa_market_intelligence.features.build [--db PATH]

`feat_segment_month` has one row per scored segment-month (a segment with at least 20 category
visits in the previous month, from the third month on): the keys, the base set B0, the groups
G1 to G7, and the targets (`y_share`, `y_visits`, `y_market`). `feat_monthly` has one row per month:
the base lags, the groups that apply to the national series, and the target. No row of either table
uses information from the month it predicts or later. Missing values follow the plan: zero-filled
with an indicator, so no NaN is stored.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine

from oa_market_intelligence.features.candidate_families import KEYS
from oa_market_intelligence.features.groups import (
    task_a_group_columns,
    task_b_group_columns,
)
from oa_market_intelligence.features.segment_task import build_task_a_rows

SEGMENT_TABLE = "feat_segment_month"
MONTHLY_TABLE = "feat_monthly"
LABEL_COLUMNS = ["y_label", "y_raw_above"]  # the direction-style labels are out of scope


def monthly_base_columns(monthly: pd.DataFrame) -> pd.DataFrame:
    """The base set B0 of the national target: the share's lags 1 to 3 and their mean, and the
    target month's sine and cosine. The target is the month's share."""
    frame = monthly.sort_values("month_id").reset_index(drop=True)
    total = (
        frame["branded_injectable_visits"]
        + frame["generic_corticosteroid_visits"]
        + frame["nsaid_otc_visits"]
    ).astype(float)
    share = frame["branded_injectable_visits"].astype(float) / total
    out = pd.DataFrame({"month_id": frame["month_id"]})
    for lag in (1, 2, 3):
        out[f"share_lag{lag}"] = share.shift(lag)
    out["share_roll3"] = out[["share_lag1", "share_lag2", "share_lag3"]].mean(axis=1, skipna=False)
    month = frame["month_id"] % 100
    out["month_sin"] = np.sin(2 * np.pi * month / 12)
    out["month_cos"] = np.cos(2 * np.pi * month / 12)
    out["y_share"] = share
    out["y_visits"] = total
    return out


def build_feature_tables(
    seg: pd.DataFrame,
    monthly: pd.DataFrame,
    signals: pd.DataFrame | None = None,
    group_visits: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(feat_segment_month, feat_monthly) from the segment Gold table (`load_segment_gold`
    output), the monthly Gold table, the long as-of signal table and the competitor group visits."""
    rows = build_task_a_rows(seg).drop(columns=LABEL_COLUMNS)
    columns = task_a_group_columns(seg, signals, group_visits)
    segment_table = rows.merge(columns, on=KEYS, how="left")

    base = monthly_base_columns(monthly)
    groups = task_b_group_columns(monthly, signals, group_visits)
    monthly_table = base.merge(groups, on="month_id", how="left")
    # the first months have no lags: they are kept with a stated rule, not dropped
    monthly_table = monthly_table.dropna(subset=["share_lag1", "share_lag2", "share_lag3"])
    return segment_table, monthly_table.reset_index(drop=True)


def write_feature_tables(
    engine: Engine, segment_table: pd.DataFrame, monthly_table: pd.DataFrame
) -> None:
    """Replace the two tables in the warehouse (the rest of the warehouse is untouched)."""
    with engine.begin() as conn:
        segment_table.to_sql(SEGMENT_TABLE, conn, if_exists="replace", index=False)
        monthly_table.to_sql(MONTHLY_TABLE, conn, if_exists="replace", index=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[3]
    parser.add_argument("--db", type=Path, default=root / "data" / "published" / "warehouse.db")
    args = parser.parse_args(argv)

    from oa_market_intelligence.analysis.competitor_shares import monthly_group_visits
    from oa_market_intelligence.availability import load_availability
    from oa_market_intelligence.external.export import DEFAULT_SUBSET
    from oa_market_intelligence.features.segment import load_segment_gold
    from oa_market_intelligence.mart import MANIFEST_JSON, build_signals, load_manifest_rows

    engine = create_engine(f"sqlite:///{args.db.as_posix()}")
    subset = create_engine(f"sqlite:///{DEFAULT_SUBSET.as_posix()}")
    signals = build_signals(subset, load_availability(), load_manifest_rows(MANIFEST_JSON))
    with engine.connect() as conn:
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
    segment_table, monthly_table = build_feature_tables(
        load_segment_gold(engine), monthly, signals, monthly_group_visits(engine)
    )
    write_feature_tables(engine, segment_table, monthly_table)
    print(f"{SEGMENT_TABLE}: {len(segment_table)} rows, {segment_table.shape[1]} columns")
    print(f"{MONTHLY_TABLE}: {len(monthly_table)} rows, {monthly_table.shape[1]} columns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
