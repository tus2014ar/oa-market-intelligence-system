"""When each data source could have been known (R1).

A model that predicts month t may only use information that was public when the prediction would
have been made. `data/reference/source_availability.csv` records, for every source, the rule that
gives the first month a value could have been known, and whether that rule is documented (the
date comes from the files we hold) or assumed (a rule applied from anchor points or project
documents). `available_from_month` applies the rule; the warehouse table `dim_source_availability`
is loaded from the same CSV on every pipeline run.

Months are YYYYMM integers. Four rule types:

- `lag_months`: the period's end month plus a lag (negative = known before the period ends).
- `release_year_lag`: the period's year plus a number of years, in a fixed month of that year.
- `per_record_date`: the month of the record's own date (a filing date, an event date).
- `snapshot_date`: one fixed month for every period (a registry snapshot).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, delete

from oa_market_intelligence.warehouse.schema import dim_source_availability

AVAILABILITY_CSV = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "source_availability.csv"
)
GRAINS = ("month", "quarter", "year", "event", "snapshot")
RULE_TYPES = ("lag_months", "release_year_lag", "per_record_date", "snapshot_date")
BASES = ("documented", "assumed")
NUMBER_COLUMNS = ("lag_months", "release_year_lag", "release_month", "fixed_month")
REQUIRED = {
    "lag_months": ("lag_months",),
    "release_year_lag": ("release_year_lag", "release_month"),
    "per_record_date": (),
    "snapshot_date": ("fixed_month",),
}


def add_months(month: int, n: int) -> int:
    """YYYYMM plus n months (n may be negative)."""
    index = (month // 100) * 12 + (month % 100 - 1) + n
    return (index // 12) * 100 + index % 12 + 1


def load_availability(path: Path | str = AVAILABILITY_CSV) -> pd.DataFrame:
    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    for column in NUMBER_COLUMNS:
        table[column] = pd.to_numeric(table[column].replace("", None), errors="raise").astype(
            "Int64"
        )
    return table


def validate_availability(table: pd.DataFrame) -> list[str]:
    """Every problem found, as text; an empty list means the table is valid."""
    problems: list[str] = []
    if not table["source_id"].is_unique:
        problems.append("source_id is not unique")
    for _, row in table.iterrows():
        name = row["source_id"] or "<blank>"
        if row["period_grain"] not in GRAINS:
            problems.append(f"{name}: period_grain {row['period_grain']!r} is not one of {GRAINS}")
        if row["basis"] not in BASES:
            problems.append(f"{name}: basis {row['basis']!r} is not one of {BASES}")
        if not str(row["evidence"]).strip():
            problems.append(f"{name}: evidence is empty")
        if row["rule_type"] not in RULE_TYPES:
            problems.append(f"{name}: rule_type {row['rule_type']!r} is not one of {RULE_TYPES}")
            continue
        for column in REQUIRED[row["rule_type"]]:
            if pd.isna(row[column]):
                problems.append(f"{name}: rule_type {row['rule_type']} needs {column}")
        month = row["release_month"]
        if not pd.isna(month) and not 1 <= int(month) <= 12:
            problems.append(f"{name}: release_month {month} is not 1 to 12")
    return problems


def available_from_month(
    rule: dict, period_end_month: int, *, record_date: str | None = None
) -> int:
    """The first month a value for a period could have been known, as YYYYMM.

    `period_end_month` is the period's last month (for a year, December). `record_date`
    (YYYY-MM-DD) is needed only for per-record rules."""
    kind = rule["rule_type"]
    if kind == "lag_months":
        return add_months(int(period_end_month), int(rule["lag_months"]))
    if kind == "release_year_lag":
        year = int(period_end_month) // 100 + int(rule["release_year_lag"])
        return year * 100 + int(rule["release_month"])
    if kind == "per_record_date":
        if record_date is None:
            raise ValueError("a per_record_date rule needs a record_date")
        parts = str(record_date).split("-")
        if len(parts) != 3 or not all(p.isdigit() for p in parts) or len(parts[0]) != 4:
            raise ValueError(f"record_date must be YYYY-MM-DD, got {record_date!r}")
        return int(parts[0]) * 100 + int(parts[1])
    if kind == "snapshot_date":
        return int(rule["fixed_month"])
    raise ValueError(f"unknown rule_type {kind!r}")


def is_available(
    rule: dict, period_end_month: int, *, as_of_month: int, record_date: str | None = None
) -> bool:
    """Whether a value for the period was known by `as_of_month`."""
    return available_from_month(rule, period_end_month, record_date=record_date) <= as_of_month


def refresh_source_availability(engine: Engine, path: Path | str = AVAILABILITY_CSV) -> int:
    """Replace the warehouse table with the CSV's rows; refuses an invalid CSV."""
    table = load_availability(path)
    problems = validate_availability(table)
    if problems:
        raise ValueError("source_availability.csv is invalid: " + "; ".join(problems))
    rows = [
        {key: (None if pd.isna(value) else value) for key, value in record.items()}
        for record in table.to_dict("records")
    ]
    for record in rows:
        for column in NUMBER_COLUMNS:
            if record[column] is not None:
                record[column] = int(record[column])
    with engine.begin() as conn:
        conn.execute(delete(dim_source_availability))
        conn.execute(dim_source_availability.insert(), rows)
    return len(rows)
