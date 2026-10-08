"""Company revenue (DL-59, step 5): `data/reference/company_revenue.csv` into
`fact_ext_company_revenue`.

The CSV is transcribed by hand from the SEC filings we downloaded (10-K and 10-Q), one row per
reported figure with its accession number and the table it comes from. Fourth quarters are derived
(the year minus the first nine months) and flagged. `revenue_problems` re-checks the arithmetic:
the three reported quarters add up to the nine-month figure, and nine months plus the derived
fourth quarter add up to the year.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.common import (
    REFERENCE_DIR,
    log_run,
    now,
    replace_table,
)

COLUMNS = [
    "period_end",
    "company",
    "product",
    "period_type",
    "fiscal_label",
    "net_sales_usd",
    "source_accession",
    "source_form",
    "source_page",
    "derived",
    "note",
]
PERIOD_TYPES = ("quarter", "nine_months", "year")
ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def revenue_frame(reference_dir: Path = REFERENCE_DIR) -> pd.DataFrame:
    frame = pd.read_csv(
        Path(reference_dir) / "company_revenue.csv", dtype=str, keep_default_na=False
    )
    if list(frame.columns) != COLUMNS:
        raise ValueError(f"company_revenue.csv columns must be {COLUMNS}")
    if not frame["period_type"].isin(PERIOD_TYPES).all():
        raise ValueError("period_type must be quarter, nine_months or year")
    if not frame["derived"].isin(["0", "1"]).all():
        raise ValueError("derived must be 0 or 1")
    if not frame["source_accession"].map(lambda a: bool(ACCESSION.match(a))).all():
        raise ValueError("every row needs a source accession number such as 0001396814-22-000021")
    if pd.to_datetime(frame["period_end"], format="%Y-%m-%d", errors="coerce").isna().any():
        raise ValueError("period_end must be an ISO date")
    sales = pd.to_numeric(frame["net_sales_usd"], errors="coerce")
    if sales.isna().any() or (sales <= 0).any():
        raise ValueError("net_sales_usd must be a positive number on every row")
    if frame.loc[frame["derived"] == "1", "note"].str.strip().eq("").any():
        raise ValueError("a derived row must explain its derivation in the note")
    if frame.duplicated(["period_end", "company", "product", "period_type"]).any():
        raise ValueError("duplicate period, company, product and period type")
    return frame.assign(
        net_sales_usd=sales.astype(float),
        derived=frame["derived"].astype(int),
        note=frame["note"].replace("", None),
    ).reset_index(drop=True)


def revenue_problems(frame: pd.DataFrame) -> list[str]:
    """The arithmetic of the table. An empty list means every identity holds."""
    problems = []
    year_of = frame["period_end"].str[:4]
    for year in sorted(set(year_of)):
        part = frame[year_of == year]
        quarters = part[part["period_type"] == "quarter"].set_index("fiscal_label")["net_sales_usd"]
        nine = part[part["period_type"] == "nine_months"]["net_sales_usd"]
        annual = part[part["period_type"] == "year"]["net_sales_usd"]
        first3 = [quarters.get(f"{year}Q{q}") for q in (1, 2, 3)]
        q4 = quarters.get(f"{year}Q4")
        if len(nine) and None not in first3 and abs(sum(first3) - nine.iloc[0]) > 0.5:
            problems.append(f"{year}: the first three quarters do not add up to the nine months")
        if (
            len(nine)
            and len(annual)
            and q4 is not None
            and abs(nine.iloc[0] + q4 - annual.iloc[0]) > 0.5
        ):
            problems.append(f"{year}: nine months plus the fourth quarter do not equal the year")
    return problems


def load_revenue(engine: Engine, reference_dir: Path = REFERENCE_DIR) -> int:
    started = now()
    frame = revenue_frame(reference_dir)
    problems = revenue_problems(frame)
    if problems:
        raise ValueError("; ".join(problems))
    loaded = replace_table(engine, "fact_ext_company_revenue", frame)
    log_run(
        engine,
        "revenue",
        None,
        rows_read=len(frame),
        rows_loaded=loaded,
        status="ok",
        note=f"{int((frame['derived'] == 1).sum())} derived rows",
        started_at=started,
    )
    return loaded
