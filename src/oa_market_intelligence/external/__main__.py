"""Command line for the external-data loaders.

    PYTHONPATH=src python -m oa_market_intelligence.external --only reference --verify

Loads the public datasets into `data/processed/external.db` (git-ignored), separate from the IQVIA
warehouse. Each source is replaced whole or by year, so reruns give identical results.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from oa_market_intelligence.external.common import (
    DEFAULT_EXTERNAL_DB,
    DEFAULT_RAW_ROOT,
    REFERENCE_DIR,
    open_external_engine,
)
from oa_market_intelligence.external.loaders.reference import load_reference
from oa_market_intelligence.external.verify import verify_reference

SOURCES = ("reference",)
DESCRIPTIONS_CSV = REFERENCE_DIR / "external_profile" / "partb_geo_code_descriptions.csv"
DEFAULT_WAREHOUSE = REFERENCE_DIR.parent / "published" / "warehouse.db"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--external-db", type=Path, default=DEFAULT_EXTERNAL_DB)
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE)
    parser.add_argument("--only", nargs="*", choices=SOURCES, default=list(SOURCES))
    parser.add_argument("--verify", action="store_true", help="run the reconciliation checks")
    return parser.parse_args(argv)


def _product_names(warehouse: Path) -> list[str]:
    connection = sqlite3.connect(f"file:{warehouse.as_posix()}?mode=ro", uri=True)
    try:
        return [r[0] for r in connection.execute("SELECT product_name FROM dim_product")]
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = open_external_engine(args.external_db)
    if "reference" in args.only:
        counts = load_reference(
            engine,
            raw_root=args.raw,
            descriptions_csv=DESCRIPTIONS_CSV,
            product_names=_product_names(args.warehouse),
        )
        print("reference:", ", ".join(f"{t}={n}" for t, n in counts.items()))
    failed = 0
    if args.verify:
        for name, ok, detail in verify_reference(engine, args.raw):
            print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
            failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
