"""Open Payments general payments, program years 2019 to 2025 (DL-59, step 4d).

Seven files, about 88 million payment records, read in chunks. Only records that name Zilretta or
an approved hyaluronic product in any of the five product slots are kept, aggregated to month by
product by recipient type (and by nature of payment). Rules from profiling (R8, R9):

- the product is matched by name, never by payer (three companies paid for Zilretta);
- a payment must be dated inside its program year, otherwise it is dropped and tallied in the run
  log (one 2024 payment is dated in the year 0002);
- zero-dollar and zero-count records are kept and flagged;
- physicians, non-physician practitioners and teaching hospitals are kept apart.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.codes import (
    OPENPAY_WATCH_PATTERN,
    normalise_openpay_product,
)
from oa_market_intelligence.external.common import (
    finish_partition,
    now,
    replace_partition,
    to_number,
    verified_entry,
)
from oa_market_intelligence.external.profile import iter_csv, iter_zip_csv

FOLDER = "Open Payments"
SOURCE = "openpay"
PRODUCT_COLUMNS = [
    f"Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_{i}" for i in range(1, 6)
]
OP_USECOLS = [
    "Covered_Recipient_Type",
    "Covered_Recipient_NPI",
    "Teaching_Hospital_ID",
    "Date_of_Payment",
    "Total_Amount_of_Payment_USDollars",
    "Number_of_Payments_Included_in_Total_Amount",
    "Nature_of_Payment_or_Transfer_of_Value",
    "Program_Year",
    "Record_ID",
    *PRODUCT_COLUMNS,
]
RECIPIENT_TYPES = {
    "Covered Recipient Physician": "physician",
    "Covered Recipient Non-Physician Practitioner": "non_physician_practitioner",
    "Covered Recipient Teaching Hospital": "teaching_hospital",
}
MONTH_COLUMNS = [
    "month_id",
    "product",
    "recipient_type",
    "n_records",
    "total_amount_usd",
    "n_payments_counted",
    "n_flagged_records",
    "recipients",
]
NATURE_COLUMNS = [
    "month_id",
    "product",
    "recipient_type",
    "nature_of_payment",
    "n_records",
    "total_amount_usd",
]


def _empty_stats() -> dict:
    return {
        "rows_read": 0,
        "matched_records": 0,
        "dropped_date": 0,
        "zilretta_records": 0,
        "unmapped_names": {},
    }


def aggregate_openpay_chunk(
    chunk: pd.DataFrame, year: int
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Aggregate one chunk of payment records. Returns the month table (with the set of recipients
    seen, to be merged across chunks), the nature table and a tally of what happened to each row."""
    stats = _empty_stats()
    stats["rows_read"] = len(chunk)
    hit = pd.Series(False, index=chunk.index)
    for column in PRODUCT_COLUMNS:
        hit |= chunk[column].str.contains(OPENPAY_WATCH_PATTERN, case=False, regex=True, na=False)
    subset = chunk[hit]

    expanded: list[dict] = []
    matched = 0
    for row in subset.to_dict("records"):
        products: set[str] = set()
        for column in PRODUCT_COLUMNS:
            name = row[column].strip()
            if not name:
                continue
            canonical = normalise_openpay_product(name)
            if canonical:
                products.add(canonical)
            elif re.search("hyaluron", name, flags=re.IGNORECASE):
                tally = stats["unmapped_names"]
                tally[name] = tally.get(name, 0) + 1
        if not products:
            continue
        matched += 1
        stats["zilretta_records"] += int("Zilretta" in products)
        for product in sorted(products):
            expanded.append({**row, "product": product})
    stats["matched_records"] = matched
    if not expanded:
        return pd.DataFrame(columns=MONTH_COLUMNS), pd.DataFrame(columns=NATURE_COLUMNS), stats

    frame = pd.DataFrame(expanded)
    unknown = set(frame["Covered_Recipient_Type"]) - set(RECIPIENT_TYPES)
    if unknown:
        raise ValueError(f"unknown recipient type: {sorted(unknown)[0]!r}")
    frame["recipient_type"] = frame["Covered_Recipient_Type"].map(RECIPIENT_TYPES)
    date = pd.to_datetime(frame["Date_of_Payment"], format="%m/%d/%Y", errors="coerce")
    valid = date.notna() & (date.dt.year == year)
    # one dropped record can expand to several products, so count distinct source records
    stats["dropped_date"] = int(frame.loc[~valid, "Record_ID"].nunique())
    frame, date = frame[valid], date[valid]
    if frame.empty:
        return pd.DataFrame(columns=MONTH_COLUMNS), pd.DataFrame(columns=NATURE_COLUMNS), stats

    amount = to_number(frame["Total_Amount_of_Payment_USDollars"], name="amount")
    counted = to_number(frame["Number_of_Payments_Included_in_Total_Amount"], name="payments")
    out = pd.DataFrame(
        {
            "month_id": date.dt.year * 100 + date.dt.month,
            "product": frame["product"],
            "recipient_type": frame["recipient_type"],
            "nature_of_payment": frame["Nature_of_Payment_or_Transfer_of_Value"],
            "amount": amount,
            "counted": counted,
            "flagged": ((amount.fillna(0) == 0) | (counted.fillna(0) == 0)).astype(int),
            "recipient": frame["Covered_Recipient_NPI"]
            .where(frame["recipient_type"].ne("teaching_hospital"), frame["Teaching_Hospital_ID"])
            .str.strip(),
        }
    )
    keys = ["month_id", "product", "recipient_type"]
    month = out.groupby(keys, as_index=False).agg(
        n_records=("amount", "size"),
        total_amount_usd=("amount", "sum"),
        n_payments_counted=("counted", "sum"),
        n_flagged_records=("flagged", "sum"),
        recipients=("recipient", lambda s: {v for v in s if v}),
    )
    nature = out.groupby([*keys, "nature_of_payment"], as_index=False).agg(
        n_records=("amount", "size"), total_amount_usd=("amount", "sum")
    )
    return month[MONTH_COLUMNS], nature[NATURE_COLUMNS], stats


def combine(parts: list[tuple[pd.DataFrame, pd.DataFrame, dict]]):
    """Merge the chunk results of one file."""
    stats = _empty_stats()
    for _, _, s in parts:
        for key, value in s.items():
            if key == "unmapped_names":
                for name, n in value.items():
                    stats[key][name] = stats[key].get(name, 0) + n
            else:
                stats[key] += value
    months = [m for m, _, _ in parts if len(m)]
    natures = [n for _, n, _ in parts if len(n)]
    if not months:
        return pd.DataFrame(columns=MONTH_COLUMNS), pd.DataFrame(columns=NATURE_COLUMNS), stats
    month = pd.concat(months, ignore_index=True)
    keys = ["month_id", "product", "recipient_type"]
    month = month.groupby(keys, as_index=False).agg(
        n_records=("n_records", "sum"),
        total_amount_usd=("total_amount_usd", "sum"),
        n_payments_counted=("n_payments_counted", "sum"),
        n_flagged_records=("n_flagged_records", "sum"),
        recipients=("recipients", lambda sets: set().union(*sets)),
    )
    nature = pd.concat(natures, ignore_index=True)
    nature = nature.groupby([*keys, "nature_of_payment"], as_index=False)[
        ["n_records", "total_amount_usd"]
    ].sum()
    return month, nature, stats


def sources(raw_root: Path) -> list[tuple[int, Path, bool]]:
    """(program year, file, is_zip) for each yearly file."""
    found = []
    for path in sorted((Path(raw_root) / FOLDER).glob("*/PGYR*.zip")):
        found.append((int(re.search(r"PGYR(\d{4})", path.name).group(1)), path, True))
    for path in sorted(Path(raw_root).glob("OP_DTL_GNRL_PGYR*.csv")):
        found.append((int(re.search(r"PGYR(\d{4})", path.name).group(1)), path, False))
    return sorted(found)


def process_file(args: tuple[int, str, bool]):
    """Read one yearly file in chunks (runs in a worker process)."""
    year, path_text, zipped = args
    path = Path(path_text)
    reader = (
        iter_zip_csv(path, member_contains="DTL_GNRL", usecols=OP_USECOLS, chunksize=250_000)
        if zipped
        else iter_csv(path, usecols=OP_USECOLS, chunksize=250_000)
    )
    parts = [aggregate_openpay_chunk(chunk, year) for chunk in reader]
    return year, combine(parts)


def load_openpay(engine: Engine, raw_root: Path, *, workers: int = 3) -> dict[int, int]:
    jobs = []
    for year, path, zipped in sources(raw_root):
        verified_entry(raw_root, path)
        jobs.append((year, str(path), zipped))
    started = now()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(process_file, jobs))
    else:
        results = [process_file(job) for job in jobs]
    paths = {year: Path(path) for year, path, _ in jobs}
    loaded = {}
    for year, (month, nature, stats) in sorted(results):
        frame = month.assign(
            n_distinct_recipients=month["recipients"].map(len) if len(month) else []
        ).drop(columns="recipients")
        months = (year * 100 + 1, year * 100 + 12)
        replace_partition(engine, "fact_ext_openpay_nature", nature, {"month_id": months})
        loaded[year] = finish_partition(
            engine,
            raw_root,
            source=SOURCE,
            year=year,
            path=paths[year],
            table="fact_ext_openpay_month",
            frame=frame,
            rows_read=stats["rows_read"],
            where={"month_id": months},
            started_at=started,
            note=json.dumps(stats),
        )
    return loaded
