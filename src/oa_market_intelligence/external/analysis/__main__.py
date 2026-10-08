"""Command line for the external-data analyses.

    PYTHONPATH=src python -m oa_market_intelligence.external.analysis --only 6a 6b 6c gap

Reads `data/processed/external.db` and the IQVIA warehouse (read only), writes the Gold analysis
tables and one verdict row per rule, all under a run identifier.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import create_engine

from oa_market_intelligence.external.analysis.run_6a import run_6a
from oa_market_intelligence.external.analysis.run_6b import run_6b
from oa_market_intelligence.external.analysis.run_6c import run_6c
from oa_market_intelligence.external.analysis.run_gap import run_gap
from oa_market_intelligence.external.analysis.store import latest_verdicts, new_run_id
from oa_market_intelligence.external.common import DEFAULT_EXTERNAL_DB, REPO_ROOT

DEFAULT_WAREHOUSE = REPO_ROOT / "data" / "published" / "warehouse.db"
STEPS = ("6a", "6b", "6c", "gap")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-db", type=Path, default=DEFAULT_EXTERNAL_DB)
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE)
    parser.add_argument("--only", nargs="*", choices=STEPS, default=list(STEPS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    external = create_engine(f"sqlite:///{args.external_db.as_posix()}")
    iqvia = create_engine(f"sqlite:///{args.warehouse.as_posix()}")  # only ever read
    run_id = new_run_id()
    if "6a" in args.only:
        run_6a(external, iqvia, run_id)
    if "6b" in args.only:
        run_6b(external, run_id)
    if "6c" in args.only:
        run_6c(external, iqvia, run_id)
    if "gap" in args.only:
        run_gap(external, iqvia, run_id)
    print(f"run {run_id}")
    for row in latest_verdicts(external).itertuples():
        value = "" if row.value != row.value or row.value is None else f"{row.value:.4g}"
        print(f"{row.check_id:4s} {row.metric:26s} {value:>8s}  {row.verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
