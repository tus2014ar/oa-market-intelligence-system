"""Shared helpers for the external-data loaders: manifest, file checks, writes, run log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from oa_market_intelligence.external.profile import sha256_of
from oa_market_intelligence.external.schema import create_external_schema

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_DIR = REPO_ROOT / "data" / "reference"
DEFAULT_RAW_ROOT = REPO_ROOT / "data" / "raw" / "New Datasets"
DEFAULT_EXTERNAL_DB = REPO_ROOT / "data" / "processed" / "external.db"
CHECKSUM_LIMIT_BYTES = 200_000_000  # larger files are checked by size only


class ChecksumMismatch(RuntimeError):
    """A raw file no longer matches what was recorded when it was downloaded."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_external_engine(path: Path | str = DEFAULT_EXTERNAL_DB) -> Engine:
    """Open (and create if needed) the external database with its schema."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    create_external_schema(engine)
    return engine


def read_manifest(raw_root: Path | str) -> list[dict]:
    lines = (Path(raw_root) / "_download_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def verify_file(raw_root: Path | str, entry: dict) -> None:
    """Refuse a file that is missing or differs from its manifest entry (size, and checksum for
    files under 200 MB)."""
    path = Path(raw_root) / entry["file"]
    if not path.exists():
        raise FileNotFoundError(path)
    expected = entry.get("bytes")
    if expected and path.stat().st_size != expected:
        raise ChecksumMismatch(f"{entry['file']}: size {path.stat().st_size} != {expected}")
    if entry.get("sha256") and path.stat().st_size < CHECKSUM_LIMIT_BYTES:
        if sha256_of(path) != entry["sha256"]:
            raise ChecksumMismatch(f"{entry['file']}: checksum differs from the manifest")


def replace_table(engine: Engine, table: str, frame: pd.DataFrame) -> int:
    """Replace the whole content of a table in one transaction."""
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table}"))
        if len(frame):
            frame.to_sql(table, conn, if_exists="append", index=False)
    return len(frame)


def replace_partition(engine: Engine, table: str, frame: pd.DataFrame, where: dict) -> int:
    """Replace the rows of one partition (for example a year) in one transaction."""
    clause = " AND ".join(f"{column} = :{column}" for column in where)
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table} WHERE {clause}"), where)
        if len(frame):
            frame.to_sql(table, conn, if_exists="append", index=False)
    return len(frame)


def log_run(
    engine: Engine,
    source: str,
    data_year: int | None,
    *,
    rows_read: int,
    rows_loaded: int,
    status: str,
    note: str | None = None,
    started_at: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO external_load_runs (source, data_year, rows_read, rows_loaded, "
                "status, "
                "started_at, finished_at, note) VALUES (:source, :year, :read, :loaded, :status, "
                ":started, :finished, :note)"
            ),
            {
                "source": source,
                "year": data_year,
                "read": rows_read,
                "loaded": rows_loaded,
                "status": status,
                "started": started_at or now(),
                "finished": now(),
                "note": note,
            },
        )
