"""The ML-ready layer (R2): every usable outside series with the month it became known, and what was
known as of each month.

Two tables in the warehouse, built from the committed public subset (`external_subset.db`) and the
availability rules of R1:

- `mart_signal`: one row per value of each series (price, promotion, company sales, events, Medicare
  adoption by specialty), with `available_from_month`, the first month it could have been known.
- `mart_signal_asof`: for each IQVIA month t, the latest value of each series known as of the end of
  month t-1 (`AS_OF_LAG_MONTHS`, the rule the IQVIA features already follow), with its age.

A model reads `mart_signal_asof`, never `mart_signal`, so it cannot see a value before it was
public. `leakage_violations` is the test of that and the pipeline refuses a table that fails it.

Left out on purpose: anything computed from IQVIA's own visits (the IQVIA share in the promotion
table, IQVIA's adjusted share in the specialty table, the sales-versus-visits table), because it
would put the target into the inputs; the pooled 2020 to 2024 Medicare rate, which needs 2024 data;
state-level results, which have no key to join to IQVIA (no geography); and the NPI-level data.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, delete, text

from oa_market_intelligence.availability import add_months, available_from_month, load_availability
from oa_market_intelligence.external.common import REFERENCE_DIR
from oa_market_intelligence.warehouse.schema import mart_signal, mart_signal_asof

AS_OF_LAG_MONTHS = 1  # month t is modelled from what was known by the end of month t-1
MANIFEST_JSON = REFERENCE_DIR / "external_manifest.jsonl"
SIGNAL_COLUMNS = [
    "signal_id",
    "source_id",
    "specialty_group",
    "grain",
    "period_start_month",
    "period_end_month",
    "value",
    "available_from_month",
]
_FILING = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+?)_(\d{10}-\d{2}-\d{6})_")


def load_manifest_rows(path: Path | str = MANIFEST_JSON) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def filing_dates(manifest_rows: list[dict]) -> dict[str, str]:
    """accession number -> filing date, from the SEC file names in the download manifest."""
    dates: dict[str, str] = {}
    for row in manifest_rows:
        if not str(row.get("dataset", "")).startswith("SEC "):
            continue
        name = row["file"].replace("\\", "/").split("/")[-1]
        found = _FILING.match(name)
        if found:
            dates[found.group(3)] = found.group(1)
    return dates


def _month(date_text: str) -> int:
    return int(date_text[:4]) * 100 + int(date_text[5:7])


def _quarter_months(quarter_id: str) -> tuple[int, int]:
    year, quarter = int(quarter_id[:4]), int(quarter_id[-1])
    return year * 100 + 3 * quarter - 2, year * 100 + 3 * quarter


def _read(engine: Engine, query: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)


def build_signals(
    subset: Engine, availability: pd.DataFrame, manifest_rows: list[dict]
) -> pd.DataFrame:
    """Every usable series as a long table with the month each value became known."""
    rules = availability.set_index("source_id").to_dict("index")
    rows: list[dict] = []

    def add(signal_id, source, group, grain, start, end, value, record_date=None):
        if value is None or pd.isna(value):
            return
        rows.append(
            {
                "signal_id": signal_id,
                "source_id": source,
                "specialty_group": group,
                "grain": grain,
                "period_start_month": int(start),
                "period_end_month": int(end),
                "value": float(value),
                "available_from_month": available_from_month(
                    rules[source], int(end), record_date=record_date
                ),
            }
        )

    for row in _read(subset, "SELECT * FROM gold_ext_price_quarterly").itertuples():
        start, end = _quarter_months(row.quarter_id)
        for column in ("j3304_limit_per_mg", "j3301_limit_per_mg", "price_ratio"):
            add(f"asp.{column}", "asp_price", "", "quarter", start, end, getattr(row, column))

    promotion = {
        "n_physicians_paid": "openpay.physicians_paid",
        "n_practitioners_paid": "openpay.practitioners_paid",
        "total_amount_usd": "openpay.total_amount_usd",
        "n_records": "openpay.n_records",
    }
    for row in _read(subset, "SELECT * FROM gold_ext_promotion_monthly").itertuples():
        for column, signal_id in promotion.items():
            add(signal_id, "openpay", "", "month", row.month_id, row.month_id, getattr(row, column))

    filings = filing_dates(manifest_rows)
    revenue = _read(
        subset,
        "SELECT period_end, net_sales_usd, source_accession FROM fact_ext_company_revenue "
        "WHERE period_type = 'quarter'",
    )
    unknown = sorted(set(revenue["source_accession"]) - set(filings))
    if unknown:
        raise ValueError(f"no SEC filing date for accession(s): {', '.join(unknown)}")
    for row in revenue.itertuples():
        end = _month(row.period_end)
        add(
            "company.net_sales_usd",
            "sec_filings",
            "",
            "quarter",
            add_months(end, -2),
            end,
            row.net_sales_usd,
            record_date=filings[row.source_accession],
        )

    events = _read(subset, "SELECT event_date FROM dim_event")
    events["month"] = events["event_date"].map(_month)
    for month, group in events.groupby("month"):
        add(
            "event.count_in_month",
            "events",
            "",
            "event",
            month,
            month,
            len(group),
            record_date=group["event_date"].min(),
        )

    triangulation = _read(
        subset,
        "SELECT period, specialty_group, n_visible_providers, n_zilretta_providers, "
        "medicare_adoption_rate FROM gold_ext_specialty_triangulation",
    )
    triangulation = triangulation[triangulation["period"].str.fullmatch(r"\d{4}")]
    medicare = {
        "medicare_adoption_rate": "medicare.adoption_rate",
        "n_visible_providers": "medicare.n_visible_providers",
        "n_zilretta_providers": "medicare.n_zilretta_providers",
    }
    for row in triangulation.itertuples():
        year = int(row.period)
        for column, signal_id in medicare.items():
            add(
                signal_id,
                "partb_provider",
                row.specialty_group,
                "year",
                year * 100 + 1,
                year * 100 + 12,
                getattr(row, column),
            )
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


def _month_index(month: int) -> int:
    return (month // 100) * 12 + (month % 100)


def signals_as_of(signals: pd.DataFrame, as_of_month: int) -> pd.DataFrame:
    """The latest value of each series (and specialty group) already known by `as_of_month`."""
    known = signals[signals["available_from_month"] <= as_of_month]
    if known.empty:
        return known.assign(age_months=pd.Series(dtype="int64"))[
            [
                "signal_id",
                "specialty_group",
                "value",
                "period_end_month",
                "available_from_month",
                "age_months",
            ]
        ]
    latest = (
        known.sort_values(["period_end_month", "available_from_month"])
        .groupby(["signal_id", "specialty_group"], as_index=False)
        .tail(1)
    )
    latest = latest.assign(
        age_months=_month_index(as_of_month) - latest["period_end_month"].map(_month_index)
    )
    return latest[
        [
            "signal_id",
            "specialty_group",
            "value",
            "period_end_month",
            "available_from_month",
            "age_months",
        ]
    ].reset_index(drop=True)


def build_as_of_table(
    signals: pd.DataFrame, months: list[int], lag: int = AS_OF_LAG_MONTHS
) -> pd.DataFrame:
    """For each month, the series values known `lag` months earlier."""
    pieces = []
    for month in months:
        as_of = add_months(month, -lag)
        piece = signals_as_of(signals, as_of)
        pieces.append(piece.assign(month_id=month, as_of_month=as_of))
    columns = [
        "month_id",
        "as_of_month",
        "signal_id",
        "specialty_group",
        "value",
        "period_end_month",
        "available_from_month",
        "age_months",
    ]
    return (
        pd.concat(pieces, ignore_index=True)[columns] if pieces else pd.DataFrame(columns=columns)
    )


def leakage_violations(table: pd.DataFrame) -> pd.DataFrame:
    """Rows whose value was not yet public at their as-of month (there must be none).

    A value known before its period ends (a price schedule posted ahead of the quarter) is not a
    violation: the test is the month it became known, not the end of the period it describes."""
    return table[table["available_from_month"] > table["as_of_month"]]


def wide(table: pd.DataFrame) -> pd.DataFrame:
    """One row per month and one column per series (`signal` or `signal|specialty group`)."""
    named = table.assign(
        column=table["signal_id"]
        + table["specialty_group"].map(lambda group: f"|{group}" if group else "")
    )
    return named.pivot(index="month_id", columns="column", values="value")


def build_mart(
    engine: Engine,
    subset_path: Path | str,
    manifest_rows: list[dict],
    availability: pd.DataFrame | None = None,
) -> dict | None:
    """Write `mart_signal` and `mart_signal_asof` into the warehouse; None if there is no subset
    file (nothing is written then)."""
    if not Path(subset_path).exists():
        return None
    availability = load_availability() if availability is None else availability
    subset = create_engine(f"sqlite:///{Path(subset_path).resolve().as_posix()}")
    try:
        signals = build_signals(subset, availability, manifest_rows)
    finally:
        subset.dispose()
    with engine.connect() as conn:
        months = [
            int(m) for m in pd.read_sql(text("SELECT month_id FROM dim_month"), conn)["month_id"]
        ]
    table = build_as_of_table(signals, sorted(months))
    violations = leakage_violations(table)
    if len(violations):
        raise ValueError(f"leakage: {len(violations)} as-of rows were not yet known")
    with engine.begin() as conn:
        conn.execute(delete(mart_signal_asof))
        conn.execute(delete(mart_signal))
        if len(signals):
            signals.to_sql("mart_signal", conn, if_exists="append", index=False)
        if len(table):
            table.to_sql("mart_signal_asof", conn, if_exists="append", index=False)
    return {"signals": int(len(signals)), "as_of_rows": int(len(table))}
