"""Medicare Part B payment limit (ASP) files, 2019Q3 to 2025Q4 (DL-59, step 4b).

The files are spreadsheets saved as CSV, with title lines above the header and five different
layouts over the years, so the header row is located and columns are read by name. Only the
approved codes are kept.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.codes import ALL_CODES
from oa_market_intelligence.external.common import (
    finish_partition,
    now,
    to_number,
    verified_entry,
)
from oa_market_intelligence.external.profile_all import asp_quarter, find_asp_header

FOLDER = "ASP Pricing Files"
SOURCE = "asp"
WANTED = {
    "HCPCS Code": "hcpcs_code",
    "Short Description": "short_description",
    "HCPCS Code Dosage": "dosage",
    "Payment Limit": "payment_limit",
    "Co-insurance Percentage": "coinsurance_pct",
    "Notes": "notes",
}


def source_release(name: str) -> str | None:
    """The 'updated MMDDYY' date in a file name, when there is one."""
    found = re.search(r"updated[ _](\d{6})", name, flags=re.IGNORECASE)
    return found.group(1) if found else None


def parse_asp_text(text: str) -> pd.DataFrame:
    """The table of a price file: title lines skipped, header found, columns read by name."""
    lines = text.splitlines()
    start = find_asp_header(lines)
    if start is None:
        raise ValueError("no HCPCS Code header row found")
    rows = list(csv.reader(io.StringIO("\n".join(lines[start:]))))
    header = [h.strip() for h in rows[0]]
    position = {}
    for index, name in enumerate(header):
        position.setdefault(name, index)
    out = {}
    for name in WANTED:
        if name in position:
            index = position[name]
            out[name] = [row[index].strip() if index < len(row) else "" for row in rows[1:]]
    return pd.DataFrame(out)


def clean_asp_table(
    table: pd.DataFrame, quarter: str, source_file: str, release: str | None
) -> pd.DataFrame:
    table = table[table["HCPCS Code"].isin(ALL_CODES)]
    blank = pd.Series([None] * len(table), index=table.index, dtype=object)
    coinsurance = (
        to_number(table["Co-insurance Percentage"], name="Co-insurance Percentage")
        if "Co-insurance Percentage" in table
        else blank
    )
    notes = table["Notes"].replace("", None) if "Notes" in table else blank
    out = pd.DataFrame(
        {
            "quarter_id": quarter,
            "hcpcs_code": table["HCPCS Code"],
            "short_description": table["Short Description"],
            "dosage": table["HCPCS Code Dosage"],
            "payment_limit": to_number(table["Payment Limit"], name="Payment Limit"),
            "coinsurance_pct": coinsurance,
            "notes": notes,
            "source_file": source_file,
            "source_release": release,
        }
    )
    return out.reset_index(drop=True)


def load_asp(engine: Engine, raw_root: Path) -> dict[str, int]:
    loaded = {}
    for path in sorted((Path(raw_root) / FOLDER).rglob("*.zip")):
        started = now()
        verified_entry(raw_root, path)
        quarter = asp_quarter(path.name)
        if quarter is None:
            raise ValueError(f"cannot read a quarter from {path.name}")
        with zipfile.ZipFile(path) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            text = archive.read(member).decode("latin-1")
        table = parse_asp_text(text)
        release = source_release(path.name) or source_release(member)
        frame = clean_asp_table(table, quarter, path.name, release)
        loaded[quarter] = finish_partition(
            engine,
            raw_root,
            source=SOURCE,
            year=int(quarter[:4]),
            path=path,
            table="fact_ext_asp_price",
            frame=frame,
            rows_read=len(table),
            where={"quarter_id": quarter},
            started_at=started,
        )
    return loaded
