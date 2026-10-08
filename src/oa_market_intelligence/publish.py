"""Rebuild and publish the warehouse the website serves, without ever damaging the last good one.

One run: build the whole warehouse into a staging file, check it, run the model stage, store
the model panel inside it, then swap it in with an atomic rename. Any failure leaves the
published database exactly as it was. Every run, good or bad, appends a line to
`run_log.jsonl`.

    python -m oa_market_intelligence.publish
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from oa_market_intelligence.modeling.monitoring import write_backtest_predictions
from oa_market_intelligence.pipeline import (
    DEFAULT_RAW_DIR,
    DEFAULT_REFERENCE_DIR,
    run_pipeline,
)
from oa_market_intelligence.serving.model_panel import model_panel, store_panel
from oa_market_intelligence.serving.results import compute_all, store_result

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


def _write_backtest(engine, direction: dict) -> None:
    """Record the served direction classifier's backtest predictions in the Gold columns."""
    served = direction["decision"]["serving"]
    predictions = pd.DataFrame(direction["predictions"])
    frame = pd.DataFrame(
        {
            "month_id": predictions["month_id"],
            "predicted": predictions[served],
            "actual": predictions["y_true"],
            "probability": np.nan,  # the served classifier is a rule with no probability
        }
    )
    write_backtest_predictions(engine, frame, f"backtest walk-forward: {served}")


def publish(
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
    published_dir: Path = DEFAULT_PUBLISHED_DIR,
    run_pipeline_fn: Callable[..., dict] = run_pipeline,
    panel_fn: Callable[..., dict] = model_panel,
    results_fn: Callable[..., dict] = compute_all,
    precision: str = "full",
) -> dict:
    """Run the full rebuild. Returns the run record; never raises for a failed run.

    After the tables are built and checked, the model panel and every Phase 4 result are
    computed and stored in the staging file, and the backtest predictions are written to Gold.
    Any failure in these stages fails the whole run and leaves the last good database live."""
    published_dir = Path(published_dir)
    published_dir.mkdir(parents=True, exist_ok=True)
    published_db = published_dir / DB_NAME
    record: dict = {"status": "failed", "started_at": _now(), "error": None, "precision": precision}

    try:
        previous = _previous_coverage(published_db)
        with tempfile.TemporaryDirectory(dir=published_dir, prefix=".staging-") as staging:
            staged_db = Path(staging) / DB_NAME
            summary = run_pipeline_fn(
                raw_dir=raw_dir, reference_dir=reference_dir, db_path=staged_db
            )

            staged = _coverage(staged_db)
            _check(staged, previous)

            engine = create_engine(f"sqlite:///{staged_db.as_posix()}")
            try:
                panel = panel_fn(engine)
                store_panel(engine, panel)
                results = results_fn(engine, precision)
                for key, payload in results.items():
                    store_result(engine, key, payload, precision=precision)
                _write_backtest(engine, results["direction"])
            finally:
                engine.dispose()

            os.replace(staged_db, published_db)  # atomic: readers see old or new, never half

        record.update(
            status="ok",
            n_months=staged[0],
            last_month_id=staged[1],
            serving=panel["decision"]["serving"],
            promoted=panel["decision"]["promoted"],
            results=sorted(results),
            monitoring_status=results["monitoring"]["status"],
            # a product with no taxonomy entry is loaded as 'unclassified' and does not stop the
            # run (PROPOSAL 18.10); recording it here makes the gap visible in the run log
            # the extracts behind this database: file, role, SHA-256, size, rows parsed
            ingest_files=(summary or {}).get("ingest_files", []),
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
    parser = argparse.ArgumentParser(description="Rebuild and publish the website's warehouse.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--published-dir", type=Path, default=DEFAULT_PUBLISHED_DIR)
    parser.add_argument(
        "--precision",
        choices=("full", "fast"),
        default="full",
        help="'full' uses the draws of the notebooks (about 40 minutes of model fitting); "
        "'fast' uses small ones for quick checks and is recorded in the stored results",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    record = publish(
        raw_dir=args.raw_dir,
        reference_dir=args.reference_dir,
        published_dir=args.published_dir,
        precision=args.precision,
    )
    print(json.dumps(record, indent=2))
    sys.exit(0 if record["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
