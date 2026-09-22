"""Orchestrates the Phase 2 pipeline stages built so far: ingest -> validate -> build
Silver -> build Gold, writing the result to a local SQLite warehouse file.

This is *not* yet the full production cycle PROPOSAL.md §19.1 describes end to end -
it runs stages 2-4 of that 10-step cycle (validate, Silver build, Gold build),
preceded by ingestion of the four raw extracts. The remaining stages don't exist yet
and aren't run here:

- Model retrain/promotion and writing predictions back into `gold_visit_share_monthly`
  (§17.2) - a later modeling phase.
- Evidently AI drift/accuracy monitoring and alerting (§17.4) - not yet built.
- `dvc pull`/`dvc push` of `warehouse.db` and `mlruns/` around the run (§19.6) -
  deliberately deferred until an actual DVC remote is stood up, per §19.6's own stated
  principle ("cloud infrastructure stood up only once there's something ready to
  actually demo publicly", not provisioned upfront). Running this script on a GitHub
  Actions runner today does not persist `warehouse.db` between runs; the scheduled
  workflow uploads it as a build artifact instead (see .github/workflows/), a
  documented, honest stand-in until the real DVC remote exists.

What this script demonstrates today, per §751's framing, is that the pipeline "runs
automatically ... and completes successfully" on a schedule - a designed and
demonstrated capability, not a claim that it has run unattended in production for
months (there is no real monthly IQVIA extract arriving during this project; one
static historical extract is all there is).

Invocation (no package install exists yet - `src` is put on the path the same way the
test suite does, via `PYTHONPATH`, not a `pip install -e .`):

    PYTHONPATH=src python -m oa_market_intelligence.pipeline
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine

from oa_market_intelligence.ingestion.nmta_loader import parse_pivot_sheet
from oa_market_intelligence.ingestion.openfda_client import earliest_approval_date
from oa_market_intelligence.ingestion.place_of_service_loader import parse_place_of_service
from oa_market_intelligence.ingestion.reference_loader import parse_reference_table
from oa_market_intelligence.ingestion.validation import (
    validate_nmta_visits,
    validate_place_of_service,
    validate_reference_table,
)
from oa_market_intelligence.warehouse.build_gold import build_gold
from oa_market_intelligence.warehouse.build_silver import FdaLookupFn, build_silver
from oa_market_intelligence.warehouse.schema import create_schema

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_REFERENCE_DIR = REPO_ROOT / "data" / "reference"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "processed" / "warehouse.db"

OA_PIVOT_FILE = "Team1_M15_19_OA.xlsx"
RA_PIVOT_FILE = "Team1_M04_RA.xlsx"
OA_REFERENCE_FILE = "Branded Generic - OA.xlsx"
RA_REFERENCE_FILE = "Branded Generic - RA.xlsx"
TAXONOMY_FILE = "product_taxonomy.csv"


def ingest(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Parses all four raw extracts. Returns (visits, place_of_service, reference) -
    each already concatenated across OA and RA, exactly as every prior step's real-
    data tests build them (tests/test_pipeline_integration.py's `raw_extracts`)."""
    logger.info("Parsing NMTA pivot extracts (%s, %s)...", OA_PIVOT_FILE, RA_PIVOT_FILE)
    visits = pd.concat(
        [
            parse_pivot_sheet(raw_dir / OA_PIVOT_FILE),
            parse_pivot_sheet(raw_dir / RA_PIVOT_FILE),
        ],
        ignore_index=True,
    )
    logger.info("Parsing Place-of-Service extracts...")
    place_of_service = pd.concat(
        [
            parse_place_of_service(raw_dir / OA_PIVOT_FILE),
            parse_place_of_service(raw_dir / RA_PIVOT_FILE),
        ],
        ignore_index=True,
    )
    logger.info(
        "Parsing Branded/Generic reference tables (%s, %s)...",
        OA_REFERENCE_FILE,
        RA_REFERENCE_FILE,
    )
    reference = pd.concat(
        [
            parse_reference_table(raw_dir / OA_REFERENCE_FILE),
            parse_reference_table(raw_dir / RA_REFERENCE_FILE),
        ],
        ignore_index=True,
    )
    return visits, place_of_service, reference


def validate(
    visits: pd.DataFrame, place_of_service: pd.DataFrame, reference: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Runs the Step 6 Pandera gate. Raises ExtractValidationError - stopping the run
    before anything reaches Silver - on bad data (PROPOSAL.md §9.5)."""
    logger.info("Validating extracts against the Pandera schemas...")
    return (
        validate_nmta_visits(visits),
        validate_place_of_service(place_of_service),
        validate_reference_table(reference),
    )


def _make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # .as_posix() avoids a malformed sqlite URL from Windows backslashes.
    return create_engine(f"sqlite:///{db_path.resolve().as_posix()}")


def run_pipeline(
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
    db_path: Path = DEFAULT_DB_PATH,
    fetch_approval_date: FdaLookupFn = earliest_approval_date,
) -> dict:
    """Runs ingest -> validate -> Silver build -> Gold build against `db_path`,
    creating the schema first if it doesn't already exist (safe to call every run -
    schema.py's create_schema is checkfirst). Returns the combined build_silver/
    build_gold summaries plus wall-clock timing; logs progress as it goes rather than
    only reporting at the end, since a scheduled run's only visibility is its logs."""
    start = time.monotonic()
    engine = _make_engine(db_path)

    visits, place_of_service, reference = ingest(raw_dir)
    visits, place_of_service, reference = validate(visits, place_of_service, reference)
    taxonomy = pd.read_csv(reference_dir / TAXONOMY_FILE)

    logger.info("Creating schema (if not already present) at %s...", db_path)
    create_schema(engine)

    logger.info("Building Silver tables...")
    silver_summary = build_silver(
        engine,
        visits=visits,
        reference_table=reference,
        taxonomy=taxonomy,
        place_of_service=place_of_service,
        fetch_approval_date=fetch_approval_date,
    )
    if silver_summary["unmapped_products"]:
        # PROPOSAL.md §18.10: the pipeline does not fail the run and does not guess a
        # category for a genuinely new product - it flags the gap and continues. There
        # is no alerting channel yet (§17.4 is later infrastructure), so today "flag"
        # means this log line, which a scheduled run's logs make visible.
        logger.warning(
            "Unmapped products (treatment_category='unclassified' - needs a "
            "product_taxonomy.csv entry before the next monthly cycle): %s",
            silver_summary["unmapped_products"],
        )

    logger.info("Building Gold tables...")
    gold_summary = build_gold(engine)

    elapsed = time.monotonic() - start
    summary = {"silver": silver_summary, "gold": gold_summary, "elapsed_seconds": elapsed}
    logger.info("Pipeline run complete in %.1fs: %s", elapsed, summary)
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OA/RA warehouse pipeline.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    run_pipeline(raw_dir=args.raw_dir, reference_dir=args.reference_dir, db_path=args.db_path)


if __name__ == "__main__":
    main()
