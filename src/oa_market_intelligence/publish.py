"""Rebuild and publish the warehouse, without ever damaging the last good one.

One run, in this order, each stage recorded in the run log with its status and duration:

1. `public_data`: check the committed public-data subset (or, with `--public-data full`, reload and
   re-export it from the raw files, locally only)
2. `warehouse`: build the whole warehouse into a staging file from the raw IQVIA extracts
3. `coverage_check`: the staged database must not have fewer months than the published one
4. `feature_tables`, `model_tables`, `scoring`: add the feature tables, the model-result tables and
   the monthly predictions (`pred_batch`, from the adopted models) to the staged database
5. `swap`: replace the published database with an atomic rename

Any failure stops the run and leaves the published database exactly as it was. Every run, good or
bad, appends a line to `run_log.jsonl`; a failed run's line names the stage that failed.

    python -m oa_market_intelligence.publish [--public-data committed|full]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from oa_market_intelligence.features.build import write_adopted_feature_tables
from oa_market_intelligence.modeling.scoring import write_predictions
from oa_market_intelligence.modeling.store import write_model_tables
from oa_market_intelligence.pipeline import (
    DEFAULT_RAW_DIR,
    DEFAULT_REFERENCE_DIR,
    run_pipeline,
)
from oa_market_intelligence.public_data import rebuild_full, verify_committed

logger = logging.getLogger(__name__)

DEFAULT_PUBLISHED_DIR = Path("data/published")
DB_NAME = "warehouse.db"
LOG_NAME = "run_log.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coverage(db_path: Path) -> tuple[int, int | None]:
    """(number of months, latest month_id) in a published or staged database."""
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            count, last = conn.execute(
                text("SELECT COUNT(*), MAX(month_id) FROM gold_visit_share_monthly")
            ).one()
    finally:
        engine.dispose()
    return int(count), last


def _previous_coverage(published_db: Path) -> tuple[int, int | None] | None:
    if not published_db.exists():
        return None
    try:
        return _coverage(published_db)
    except Exception:  # noqa: BLE001 - an unreadable old file must not block a good rebuild
        logger.warning("Could not read the published database; treating it as absent.")
        return None


def _check(staged: tuple[int, int | None], previous: tuple[int, int | None] | None) -> None:
    count, last = staged
    if count == 0:
        raise ValueError("The rebuilt warehouse has no months of data.")
    if previous is None:
        return
    prev_count, prev_last = previous
    if last is not None and prev_last is not None and last < prev_last:
        raise ValueError(
            f"The rebuilt warehouse moved backwards: latest month {last} is before the "
            f"published {prev_last}."
        )
    if count < prev_count:
        raise ValueError(
            f"The rebuilt warehouse has fewer months ({count}) than the published one "
            f"({prev_count})."
        )


def _stage(stages: list[dict], name: str, fn: Callable[[], object], *, keep_detail: bool = True):
    """Run one stage and record it. A failure is recorded and re-raised, so the run stops."""
    started = time.monotonic()
    try:
        result = fn()
    except Exception as error:
        stages.append({"stage": name, "status": "failed",
                       "seconds": round(time.monotonic() - started, 2),
                       "error": f"{type(error).__name__}: {error}"})
        raise
    stages.append({"stage": name, "status": "ok", "seconds": round(time.monotonic() - started, 2),
                   "detail": result if keep_detail and isinstance(result, dict) else None})
    return result


def _skipped(stages: list[dict], name: str) -> None:
    stages.append({"stage": name, "status": "skipped", "seconds": 0.0, "detail": None})


def publish(
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
    published_dir: Path = DEFAULT_PUBLISHED_DIR,
    run_pipeline_fn: Callable[..., dict] = run_pipeline,
    public_data_fn: Callable[..., dict] = verify_committed,
    features_fn: Callable[..., dict] = write_adopted_feature_tables,
    model_tables_fn: Callable[..., dict] = write_model_tables,
    scoring_fn: Callable[..., dict] | None = write_predictions,
) -> dict:
    """Run the full rebuild. Returns the run record; never raises for a failed run.

    A failure at any stage fails the whole run and leaves the last good database live. The record
    has a `stages` list (name, status, seconds, detail or error) in the order they ran."""
    published_dir = Path(published_dir)
    published_dir.mkdir(parents=True, exist_ok=True)
    published_db = published_dir / DB_NAME
    stages: list[dict] = []
    record: dict = {"status": "failed", "started_at": _now(), "error": None, "stages": stages}

    try:
        previous = _previous_coverage(published_db)
        public_rows = _stage(stages, "public_data", public_data_fn)
        with tempfile.TemporaryDirectory(dir=published_dir, prefix=".staging-") as staging:
            staged_db = Path(staging) / DB_NAME
            summary = _stage(
                stages, "warehouse",
                lambda: run_pipeline_fn(
                    raw_dir=raw_dir,
                    reference_dir=reference_dir,
                    db_path=staged_db,
                    previous_db=published_db if published_db.exists() else None,
                ),
                keep_detail=False,
            )

            def check_coverage() -> dict:
                found = _coverage(staged_db)
                _check(found, previous)
                return {"n_months": found[0], "last_month_id": found[1]}

            coverage = _stage(stages, "coverage_check", check_coverage)

            engine = create_engine(f"sqlite:///{staged_db.as_posix()}")
            try:
                feature_rows = _stage(stages, "feature_tables", lambda: features_fn(engine) or {})
                model_rows = _stage(stages, "model_tables", lambda: model_tables_fn(engine) or {})
                if scoring_fn is None:
                    scoring_rows = {}
                    _skipped(stages, "scoring")
                else:
                    scoring_rows = _stage(stages, "scoring", lambda: scoring_fn(engine) or {})
            finally:
                engine.dispose()

            _stage(stages, "swap", lambda: os.replace(staged_db, published_db))  # atomic: readers
            # see the old or the new database, never half

        record.update(
            status="ok",
            n_months=coverage["n_months"],
            feature_tables=feature_rows,
            model_tables=model_rows,
            scoring=scoring_rows,
            public_data=public_rows,
            last_month_id=coverage["last_month_id"],
            # a product with no taxonomy entry is loaded as 'unclassified' and does not stop the
            # run (PROPOSAL 18.10); recording it here makes the gap visible in the run log
            # the extracts behind this database: file, role, SHA-256, size, rows parsed
            ingest_files=(summary or {}).get("ingest_files", []),
            quality=(summary or {}).get("quality"),
            unmapped_products=sorted(
                (summary or {}).get("silver", {}).get("unmapped_products", [])
            ),
        )
    except Exception as error:  # noqa: BLE001 - the last good database stays live
        logger.exception("Publish failed; the previously published database is unchanged.")
        record["error"] = f"{type(error).__name__}: {error}"

    record["finished_at"] = _now()
    with open(published_dir / LOG_NAME, "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    return record


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild and publish the warehouse.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--published-dir", type=Path, default=DEFAULT_PUBLISHED_DIR)
    parser.add_argument(
        "--public-data", choices=("committed", "full"), default="committed",
        help="committed: check the committed public-data subset (default; CI, containers); "
        "full: reload and re-export it from the raw public files (local only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    record = publish(
        raw_dir=args.raw_dir,
        reference_dir=args.reference_dir,
        published_dir=args.published_dir,
        public_data_fn=rebuild_full if args.public_data == "full" else verify_committed,
    )
    print(json.dumps(record, indent=2))
    sys.exit(0 if record["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
