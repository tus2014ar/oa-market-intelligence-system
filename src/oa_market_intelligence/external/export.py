"""Export the publishable part of the external database to a small committed file.

    PYTHONPATH=src python -m oa_market_intelligence.external.export

`data/processed/external.db` (134 MB, local, git-ignored) holds NPI-level rows, county-level data
and copyrighted taxonomy text that must stay local. `PUBLISHED_TABLES` (dimensions, bridges, the
small fact tables and the Gold analysis tables) can be shared. This writes exactly those tables,
with their constraints, to `data/published/external_subset.db`, plus a table `external_subset_meta`
(table, rows, export time). The IQVIA pipeline reads that file to build the ML-ready layer, so a
fresh clone needs neither the 24 GB of raw files nor the local database.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.common import DEFAULT_EXTERNAL_DB, REPO_ROOT
from oa_market_intelligence.external.schema import (
    LOCAL_ONLY_TABLES,
    PUBLISHED_TABLES,
    external_metadata,
)

DEFAULT_SUBSET = REPO_ROOT / "data" / "published" / "external_subset.db"
META_TABLE = "external_subset_meta"


def export_subset(source_db: Path | str, target_db: Path | str) -> dict[str, int]:
    """Copy the published tables of `source_db` into a fresh `target_db`; returns rows per table.
    The target is replaced; local-only tables are never copied."""
    target = Path(target_db)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    source = create_engine(f"sqlite:///{Path(source_db).resolve().as_posix()}")
    out = create_engine(f"sqlite:///{target.resolve().as_posix()}")
    tables = [external_metadata.tables[name] for name in PUBLISHED_TABLES]
    external_metadata.create_all(out, tables=tables)
    counts: dict[str, int] = {}
    try:
        with source.connect() as read, out.begin() as write:
            for table in tables:
                frame = pd.read_sql(text(f"SELECT * FROM {table.name}"), read)
                if len(frame):
                    frame.to_sql(table.name, write, if_exists="append", index=False)
                counts[table.name] = len(frame)
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            meta = pd.DataFrame(
                {
                    "table_name": list(counts),
                    "n_rows": list(counts.values()),
                    "exported_at": stamp,
                }
            )
            meta.to_sql(META_TABLE, write, if_exists="replace", index=False)
    finally:
        source.dispose()
    with out.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM"))
    out.dispose()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-db", type=Path, default=DEFAULT_EXTERNAL_DB)
    parser.add_argument("--target", type=Path, default=DEFAULT_SUBSET)
    args = parser.parse_args(argv)
    counts = export_subset(args.external_db, args.target)
    size = args.target.stat().st_size / 1e6
    print(
        f"wrote {args.target} ({size:.1f} MB): {len(counts)} tables, {sum(counts.values()):,} rows"
    )
    print("local only, not exported:", ", ".join(LOCAL_ONLY_TABLES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
