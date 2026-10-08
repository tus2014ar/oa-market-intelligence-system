"""Golden regression snapshots of the warehouse (R4).

    PYTHONPATH=src python -m oa_market_intelligence.golden --update

For every table of the warehouse built from the real extracts: the row count, the sum of each
numeric column and a content hash (SHA-256 of a canonical CSV, rows sorted, floats to 6 significant
digits), committed in `tests/golden/warehouse_golden.json`. A test rebuilds the warehouse and
compares; any difference names the table and what moved. Timestamps (`bronze_ingest_files`,
`dq_report`) are not snapshotted. An intended change is accepted by running `--update` and reviewing
the diff of that file in the pull request, so a change to the data or the tables is never silent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

GOLDEN_JSON = Path(__file__).resolve().parents[2] / "tests" / "golden" / "warehouse_golden.json"
TABLES = (
    "dim_month",
    "dim_product",
    "dim_specialty",
    "dim_demographics",
    "fact_product_visits",
    "fact_place_of_service_visits",
    "gold_visit_share_monthly",
    "gold_segment_adoption",
    "dim_source_availability",
    "mart_signal",
    "mart_signal_asof",
)
FLOAT_TOLERANCE = 1e-6


def fake_fda(name: str):
    """The approval-date lookup used for the golden build (no network): ZILRETTA only."""
    return date(2017, 10, 6) if name == "ZILRETTA" else None


def _content_hash(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(list(frame.columns), na_position="first").reset_index(drop=True)
    text_form = ordered.to_csv(index=False, float_format="%.6g", lineterminator="\n")
    return hashlib.sha256(text_form.encode("utf-8")).hexdigest()


def snapshot(engine: Engine, tables: tuple[str, ...] = TABLES) -> dict:
    out: dict = {}
    with engine.connect() as conn:
        for table in tables:
            frame = pd.read_sql(text(f"SELECT * FROM {table}"), conn)
            numeric = frame.select_dtypes("number")
            out[table] = {
                "rows": int(len(frame)),
                "sums": {c: round(float(numeric[c].sum()), 6) for c in numeric.columns},
                "sha256": _content_hash(frame),
            }
    return out


def compare(current: dict, golden: dict) -> list[str]:
    """What differs between a fresh snapshot and the golden one, as readable lines."""
    lines: list[str] = []
    for table in golden:
        if table not in current:
            lines.append(f"{table}: missing from the rebuilt warehouse")
            continue
        now, then = current[table], golden[table]
        if now["rows"] != then["rows"]:
            lines.append(f"{table}: rows {then['rows']} -> {now['rows']}")
        for column in sorted(set(now["sums"]) | set(then["sums"])):
            a, b = now["sums"].get(column), then["sums"].get(column)
            if a is None or b is None or abs(a - b) > FLOAT_TOLERANCE * max(1.0, abs(b)):
                lines.append(f"{table}: sum of {column} {b} -> {a}")
        if now["sha256"] != then["sha256"]:
            lines.append(f"{table}: content differs (hash changed)")
    for table in current:
        if table not in golden:
            lines.append(f"{table}: not in the golden file")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="rebuild and rewrite the golden file")
    args = parser.parse_args(argv)
    if not args.update:
        parser.print_help()
        return 0
    from oa_market_intelligence.pipeline import run_pipeline

    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "warehouse.db"
        run_pipeline(db_path=db, fetch_approval_date=fake_fda)
        engine = create_engine(f"sqlite:///{db.as_posix()}")
        snap = snapshot(engine)
        engine.dispose()
    GOLDEN_JSON.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_JSON.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN_JSON}: {', '.join(f'{t} {v['rows']}' for t, v in snap.items())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
