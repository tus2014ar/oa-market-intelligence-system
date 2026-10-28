"""The public-data stage of the pipeline (DL-81).

The IQVIA pipeline reads the committed `data/published/external_subset.db` (the publishable tables
of the public-data database) to build the as-of layer. This stage makes sure that file is what it
is supposed to be, in one of two modes:

`committed` (the default, used by CI, the container and the monthly workflow): the 24 GB of raw
public files are not there, so the stage only *checks* the committed subset: every published table
is present, its row count equals the one recorded in the file's own `external_subset_meta`, its
content hash equals the one committed in `data/reference/external_subset_fingerprint.json`, and the
download manifest of the raw files is complete (one SHA-256 for each of its files). Any difference
stops the run.

`full` (local only, needs the raw files): reload every source into `data/processed/external.db`
with the reconciliation checks, export the subset and compare it with the committed fingerprint.
The subset file is replaced, so a refreshed public dataset shows up as a diff to review; the run
reports whether the fingerprint changed and does not fail on that. After an intended refresh,
accept the new fingerprint with `--update-fingerprint`.

    PYTHONPATH=src python -m oa_market_intelligence.public_data --verify
    PYTHONPATH=src python -m oa_market_intelligence.public_data --update-fingerprint
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.common import DEFAULT_EXTERNAL_DB, REFERENCE_DIR
from oa_market_intelligence.external.export import DEFAULT_SUBSET, META_TABLE, export_subset
from oa_market_intelligence.external.schema import PUBLISHED_TABLES
from oa_market_intelligence.golden import _content_hash

FINGERPRINT_JSON = REFERENCE_DIR / "external_subset_fingerprint.json"
MANIFEST_JSON = REFERENCE_DIR / "external_manifest.jsonl"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PublicDataError(RuntimeError):
    """The public-data stage found something wrong; the run stops."""


def subset_fingerprint(subset_db: Path | str, tables: tuple[str, ...] = PUBLISHED_TABLES) -> dict:
    """Row count and content hash of every published table in a subset file. The meta table is
    left out (it holds the export time)."""
    engine = create_engine(f"sqlite:///{Path(subset_db).resolve().as_posix()}")
    out: dict = {}
    try:
        with engine.connect() as conn:
            for table in tables:
                frame = pd.read_sql(text(f"SELECT * FROM {table}"), conn)
                out[table] = {"rows": int(len(frame)), "sha256": _content_hash(frame)}
    finally:
        engine.dispose()
    return {"tables": out}


def write_fingerprint(
    subset_db: Path | str = DEFAULT_SUBSET,
    path: Path | str = FINGERPRINT_JSON,
    tables: tuple[str, ...] = PUBLISHED_TABLES,
) -> dict:
    fingerprint = subset_fingerprint(subset_db, tables)
    Path(path).write_text(json.dumps(fingerprint, indent=1, sort_keys=True) + "\n", "utf-8")
    return fingerprint


def compare_fingerprints(current: dict, committed: dict) -> list[str]:
    """What differs between two fingerprints, as readable lines (empty if identical)."""
    lines = []
    a, b = current["tables"], committed["tables"]
    for name in sorted(set(a) | set(b)):
        if name not in a:
            lines.append(f"{name}: in the committed fingerprint but not in the subset")
        elif name not in b:
            lines.append(f"{name}: in the subset but not in the committed fingerprint")
        else:
            if a[name]["rows"] != b[name]["rows"]:
                lines.append(f"{name}: rows {b[name]['rows']} -> {a[name]['rows']}")
            if a[name]["sha256"] != b[name]["sha256"]:
                lines.append(f"{name}: content differs (hash changed)")
    return lines


def _table_names(subset_db: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{subset_db.resolve().as_posix()}")
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _meta_counts(subset_db: Path) -> dict[str, int]:
    engine = create_engine(f"sqlite:///{subset_db.resolve().as_posix()}")
    try:
        with engine.connect() as conn:
            frame = pd.read_sql(text(f"SELECT table_name, n_rows FROM {META_TABLE}"), conn)
    finally:
        engine.dispose()
    return dict(zip(frame["table_name"], frame["n_rows"].astype(int), strict=True))


def check_manifest(path: Path | str = MANIFEST_JSON) -> int:
    """Every raw public file must be recorded with a SHA-256. Returns the number of files."""
    rows = [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line.strip()]
    if not rows:
        raise PublicDataError(f"{path} is empty")
    bad = [r.get("file", "?") for r in rows if not _SHA256.match(str(r.get("sha256", "")))]
    if bad:
        raise PublicDataError(f"{len(bad)} manifest rows lack a valid SHA-256, e.g. {bad[0]}")
    return len(rows)


def verify_committed(
    subset_db: Path | str = DEFAULT_SUBSET,
    fingerprint_json: Path | str = FINGERPRINT_JSON,
    manifest_json: Path | str = MANIFEST_JSON,
    tables: tuple[str, ...] = PUBLISHED_TABLES,
) -> dict:
    """The `committed` mode: raise `PublicDataError` naming every problem, else give a summary."""
    subset_db = Path(subset_db)
    if not subset_db.exists():
        raise PublicDataError(f"the committed public-data subset is missing: {subset_db}")
    present = _table_names(subset_db)
    missing = [name for name in (*tables, META_TABLE) if name not in present]
    if missing:
        raise PublicDataError(f"the public-data subset lacks tables: {', '.join(missing)}")
    problems: list[str] = []
    current = subset_fingerprint(subset_db, tables)
    meta = _meta_counts(subset_db)
    for name in tables:
        if name not in meta:
            problems.append(f"{name}: not listed in {META_TABLE}")
        elif meta[name] != current["tables"][name]["rows"]:
            problems.append(f"{name}: {META_TABLE} says {meta[name]} rows, the table has "
                            f"{current['tables'][name]['rows']}")
    committed = json.loads(Path(fingerprint_json).read_text("utf-8"))
    problems += compare_fingerprints(current, committed)
    manifest_files = check_manifest(manifest_json)
    if problems:
        raise PublicDataError(
            "the public-data subset does not match its record: " + "; ".join(problems)
        )
    return {
        "mode": "committed",
        "tables": len(tables),
        "rows": sum(t["rows"] for t in current["tables"].values()),
        "manifest_files": manifest_files,
    }


def rebuild_full(
    *,
    load_fn: Callable[[], int] | None = None,
    export_fn: Callable[[], dict] | None = None,
    subset_db: Path | str = DEFAULT_SUBSET,
    fingerprint_json: Path | str = FINGERPRINT_JSON,
    manifest_json: Path | str = MANIFEST_JSON,
    tables: tuple[str, ...] = PUBLISHED_TABLES,
) -> dict:
    """The `full` mode (local only): reload and verify every source, export the subset, compare it
    with the committed fingerprint. Does not fail on a changed fingerprint (a refresh), but
    reports it; a failed reconciliation check does fail."""
    if load_fn is None:
        from oa_market_intelligence.external.__main__ import main as external_main

        load_fn = lambda: external_main(["--verify"])  # noqa: E731
    if export_fn is None:
        export_fn = lambda: export_subset(DEFAULT_EXTERNAL_DB, subset_db)  # noqa: E731
    if load_fn():
        raise PublicDataError("a public-data reconciliation check failed (see the FAIL lines)")
    counts = export_fn()
    manifest_files = check_manifest(manifest_json)
    current = subset_fingerprint(subset_db, tables)
    committed = json.loads(Path(fingerprint_json).read_text("utf-8"))
    differences = compare_fingerprints(current, committed)
    return {
        "mode": "full",
        "tables": len(counts),
        "rows": sum(counts.values()),
        "manifest_files": manifest_files,
        "fingerprint_changed": bool(differences),
        "differences": differences[:20],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="check the committed subset")
    parser.add_argument("--update-fingerprint", action="store_true",
                        help="record the current subset as the committed fingerprint")
    args = parser.parse_args(argv)
    if args.update_fingerprint:
        fingerprint = write_fingerprint()
        print(f"wrote {FINGERPRINT_JSON} ({len(fingerprint['tables'])} tables)")
        return 0
    if args.verify:
        print(verify_committed())
        return 0
    parser.error("choose --verify or --update-fingerprint")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
