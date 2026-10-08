"""An audit record of what was ingested from the raw IQVIA extracts (R5).

For every parsed output of every raw file, one row: which file, its SHA-256 and size, how many rows
were parsed, the month span and the visit sum, and when. The table `bronze_ingest_files` holds the
latest run; the publish run log keeps the history (one entry per run). With the file hash in both,
any published database can be traced to the exact extracts that produced it.

The parsers already refuse malformed files (a Grand Total that does not reconcile, a product above
its manufacturer subtotal, an unknown place of service...). Those checks run before anything is
recorded; the `parser_checks` column says which ones apply, so a reader of the table knows what a
row has already been through.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, delete

from oa_market_intelligence.ingestion.files import (
    OA_PIVOT_FILE,
    OA_REFERENCE_FILE,
    RA_PIVOT_FILE,
    RA_REFERENCE_FILE,
)
from oa_market_intelligence.warehouse.schema import bronze_ingest_files

# role -> disease area -> raw file. A workbook appears under two roles (pivot and place of service).
RAW_FILES = {
    "nmta_pivot": {"OA": OA_PIVOT_FILE, "RA": RA_PIVOT_FILE},
    "place_of_service": {"OA": OA_PIVOT_FILE, "RA": RA_PIVOT_FILE},
    "reference_table": {"OA": OA_REFERENCE_FILE, "RA": RA_REFERENCE_FILE},
}
PARSER_CHECKS = {
    "nmta_pivot": (
        "Grand Total equals the sum of the month rows; every product row within its manufacturer "
        "subtotal; header and row-indent structure; counts numeric"
    ),
    "place_of_service": (
        "exactly one place-of-service sheet; recognised place-of-service labels; no duplicated "
        "month; at least one data row"
    ),
    "reference_table": "exactly one Grand Total row; header structure",
}


def sha256_file(path: Path | str, block: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(block):
            digest.update(chunk)
    return digest.hexdigest()


def _months(frame: pd.DataFrame) -> tuple[int | None, int | None, int | None]:
    if "month" not in frame.columns or frame.empty:
        return None, None, None
    stamps = pd.to_datetime(frame["month"])
    ids = (stamps.dt.year * 100 + stamps.dt.month).astype(int)
    return int(ids.min()), int(ids.max()), int(ids.nunique())


def build_ingest_audit(
    raw_dir: Path | str,
    visits: pd.DataFrame,
    place_of_service: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    ingested_at: str | None = None,
) -> list[dict]:
    """One row per (file, role): the parsed frames are the pipeline's `ingest` output."""
    raw_dir = Path(raw_dir)
    stamp = ingested_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    frames = {
        "nmta_pivot": visits,
        "place_of_service": place_of_service,
        "reference_table": reference,
    }
    rows: list[dict] = []
    for role, files in RAW_FILES.items():
        for area, name in files.items():
            path = raw_dir / name
            if not path.exists():
                raise FileNotFoundError(f"raw extract not found: {path}")
            part = frames[role][frames[role]["disease_area"] == area]
            first, last, count = _months(part)
            rows.append(
                {
                    "file_name": name,
                    "role": role,
                    "disease_area": area,
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "rows_parsed": int(len(part)),
                    "first_month": first,
                    "last_month": last,
                    "n_months": count,
                    "visits_sum": int(part["patient_visits"].sum()) if len(part) else 0,
                    "parser_checks": PARSER_CHECKS[role],
                    "ingested_at": stamp,
                }
            )
    return rows


def refresh_bronze_ingest_files(engine: Engine, rows: list[dict]) -> int:
    """Replace the table's content with this run's rows."""
    with engine.begin() as conn:
        conn.execute(delete(bronze_ingest_files))
        conn.execute(bronze_ingest_files.insert(), rows)
    return len(rows)
