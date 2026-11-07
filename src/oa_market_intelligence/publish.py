"""Rebuild and publish the warehouse, without ever damaging the last good one.

One run, in this order, each stage recorded in the run log with its status and duration:

1. `public_data`: check the committed public-data subset (or, with `--public-data full`, reload and
   re-export it from the raw files, locally only)
2. `warehouse`: build the whole warehouse into a staging file from the raw IQVIA extracts
3. `coverage_check`: the staged database must not have fewer months than the published one
4. `feature_tables`, `model_tables`, `scoring`: add the feature tables, the model-result tables and
   the monthly predictions (`pred_batch`, from the adopted models) to the staged database
5. `promotion`: the promotion gate (DL-126): the refitted models are compared with the ones in
   service and a failed check stops the run before the swap (a written reason can override it)
6. `monitoring`: data freshness and drift of the previous run's predictions (reported, never stops
   the run)
7. `swap`: replace the published database with an atomic rename

Any failure stops the run and leaves the published database exactly as it was. Every run, good or
bad, appends a line to `run_log.jsonl`; a failed run's line names the stage that failed.

    python -m oa_market_intelligence.publish [--public-data committed|full]
"""

from __future__ import annotations

import argparse
import hashlib
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
from oa_market_intelligence.monitoring import run_monitoring
from oa_market_intelligence.pipeline import (
    DEFAULT_RAW_DIR,
    DEFAULT_REFERENCE_DIR,
    run_pipeline,
)
from oa_market_intelligence.promotion import run_promotion
from oa_market_intelligence.public_data import rebuild_full, verify_committed
from oa_market_intelligence.versions import run_versions

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
        stages.append(
            {
                "stage": name,
                "status": "failed",
                "seconds": round(time.monotonic() - started, 2),
                "error": f"{type(error).__name__}: {error}",
            }
        )
        raise
    stages.append(
        {
            "stage": name,
            "status": "ok",
            "seconds": round(time.monotonic() - started, 2),
            "detail": result if keep_detail and isinstance(result, dict) else None,
        }
    )
    return result


def _skipped(stages: list[dict], name: str) -> None:
    stages.append({"stage": name, "status": "skipped", "seconds": 0.0, "detail": None})


def extracts_fingerprint(raw_dir: Path | str) -> str | None:
    """A short fingerprint of the extracts (the .xlsx files) directly in a folder: names, sizes and
    modification times. It changes when an extract is added, replaced or changed, so the
    orchestrator can ask for a run exactly once per distinct set. None if there are no extracts."""
    files = sorted(Path(raw_dir).glob("*.xlsx"))
    if not files:
        return None
    parts = [f"{f.name}|{f.stat().st_size}|{f.stat().st_mtime_ns}" for f in files]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


# The stages, in the order they run (DL-128). `publish()` and the Dagster job both walk this list,
# so the order, the names in the run log and the graph in the orchestrator can never disagree.
STAGE_ORDER = (
    "public_data", "warehouse", "coverage_check", "feature_tables", "model_tables", "scoring",
    "promotion", "monitoring", "swap",
)  # fmt: skip


class PublishRun:
    """One publish run, one method per stage (DL-128).

    `publish()` calls the stages in order in a plain loop; the Dagster job (`orchestration.py`)
    calls the very same methods, one per step, so there is one implementation of the pipeline and
    two ways to drive it. A stage method records itself in `stages` (ok, failed or skipped) and
    re-raises a failure, so whoever drives the run stops at the first one. `finish()` then writes
    the run record: status ok only if the swap happened, otherwise the first failed stage's error.

    The staged database lives in a temporary folder inside the published folder and is swapped in
    only by the last stage; `finish()` removes the folder, so a failed run leaves the last good
    database exactly as it was."""

    def __init__(
        self,
        *,
        raw_dir: Path = DEFAULT_RAW_DIR,
        reference_dir: Path = DEFAULT_REFERENCE_DIR,
        published_dir: Path = DEFAULT_PUBLISHED_DIR,
        run_pipeline_fn: Callable[..., dict] = run_pipeline,
        public_data_fn: Callable[..., dict] = verify_committed,
        features_fn: Callable[..., dict] = write_adopted_feature_tables,
        model_tables_fn: Callable[..., dict] = write_model_tables,
        scoring_fn: Callable[..., dict] | None = write_predictions,
        monitoring_fn: Callable[..., dict] | None = run_monitoring,
        promotion_fn: Callable[..., dict] | None = run_promotion,
        accept_challenger: str | None = None,
    ):
        self.raw_dir, self.reference_dir = raw_dir, reference_dir
        self.published_dir = Path(published_dir)
        self.published_db = self.published_dir / DB_NAME
        self.run_pipeline_fn, self.public_data_fn = run_pipeline_fn, public_data_fn
        self.features_fn, self.model_tables_fn = features_fn, model_tables_fn
        self.scoring_fn, self.monitoring_fn = scoring_fn, monitoring_fn
        self.promotion_fn, self.accept_challenger = promotion_fn, accept_challenger
        self.stages: list[dict] = []
        self.record: dict = {
            "status": "failed", "started_at": _now(), "error": None, "stages": self.stages,
        }  # fmt: skip
        self.results: dict = {}
        self.summary: dict | None = None
        self.coverage: dict | None = None
        self._previous: tuple[int, int | None] | None = None
        self._staging: tempfile.TemporaryDirectory | None = None
        self._staged_db: Path | None = None
        self._engine = None
        self._finished = False

    # ---- set-up and the stages

    def start(self) -> None:
        self.published_dir.mkdir(parents=True, exist_ok=True)
        self._previous = _previous_coverage(self.published_db)

    def public_data(self) -> None:
        self.results["public_data"] = _stage(self.stages, "public_data", self.public_data_fn)

    def warehouse(self) -> None:
        self._staging = tempfile.TemporaryDirectory(dir=self.published_dir, prefix=".staging-")
        self._staged_db = Path(self._staging.name) / DB_NAME
        self.summary = _stage(
            self.stages, "warehouse",
            lambda: self.run_pipeline_fn(
                raw_dir=self.raw_dir,
                reference_dir=self.reference_dir,
                db_path=self._staged_db,
                previous_db=self.published_db if self.published_db.exists() else None,
            ),
            keep_detail=False,
        )  # fmt: skip

    def coverage_check(self) -> None:
        def check() -> dict:
            found = _coverage(self._staged_db)
            _check(found, self._previous)
            return {"n_months": found[0], "last_month_id": found[1]}

        self.coverage = _stage(self.stages, "coverage_check", check)

    def _db(self):
        if self._engine is None:
            self._engine = create_engine(f"sqlite:///{self._staged_db.as_posix()}")
        return self._engine

    def _previous_db(self) -> Path | None:
        return self.published_db if self.published_db.exists() else None

    def feature_tables(self) -> None:
        self.results["feature_tables"] = _stage(
            self.stages, "feature_tables", lambda: self.features_fn(self._db()) or {}
        )

    def model_tables(self) -> None:
        self.results["model_tables"] = _stage(
            self.stages, "model_tables", lambda: self.model_tables_fn(self._db()) or {}
        )

    def scoring(self) -> None:
        if self.scoring_fn is None:
            self.results["scoring"] = {}
            _skipped(self.stages, "scoring")
        else:
            self.results["scoring"] = _stage(
                self.stages, "scoring", lambda: self.scoring_fn(self._db()) or {}
            )

    def promotion(self) -> None:
        if self.scoring_fn is None or self.promotion_fn is None:
            self.results["promotion"] = {}
            _skipped(self.stages, "promotion")
        else:
            previous = self._previous_db()
            self.results["promotion"] = _stage(
                self.stages,
                "promotion",
                lambda: self.promotion_fn(self._db(), previous, self.accept_challenger) or {},
            )

    def monitoring(self) -> None:
        if self.monitoring_fn is None:
            self.results["monitoring"] = {}
            _skipped(self.stages, "monitoring")
        else:
            previous = self._previous_db()
            self.results["monitoring"] = _stage(
                self.stages, "monitoring", lambda: self.monitoring_fn(self._db(), previous) or {}
            )

    def swap(self) -> None:
        if self._engine is not None:  # release the staged file before it is renamed
            self._engine.dispose()
            self._engine = None
        _stage(
            self.stages, "swap", lambda: os.replace(self._staged_db, self.published_db)
        )  # atomic: readers see the old or the new database, never half

    # ---- the record

    def finish(self, error: BaseException | str | None = None) -> dict:
        """Write the run record (once) and clean up. A run is ok only if its swap happened."""
        if self._finished:
            return self.record
        self._finished = True
        if error is None and not self.stages:
            # no stage ran and nothing went wrong: not a run, so no record. An orchestrator may
            # set the run object up again after the steps have finished (Dagster does, for its
            # end-of-run bookkeeping); that must not add an empty "failed" line to the run log.
            return self.record
        if self._engine is not None:
            self._engine.dispose()
        if self._staging is not None:
            self._staging.cleanup()
        swapped = any(s["stage"] == "swap" and s["status"] == "ok" for s in self.stages)
        failed = next((s for s in self.stages if s["status"] == "failed"), None)
        if error is not None:
            message = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        elif failed is not None:
            message = failed.get("error")
        elif not swapped:
            message = "the run did not reach the swap"
        else:
            message = None
        if message is None and swapped:
            summary = self.summary or {}
            self.record.update(
                status="ok",
                n_months=self.coverage["n_months"],
                feature_tables=self.results.get("feature_tables", {}),
                model_tables=self.results.get("model_tables", {}),
                scoring=self.results.get("scoring", {}),
                promotion=self.results.get("promotion", {}),
                monitoring=self.results.get("monitoring", {}),
                versions=run_versions(summary.get("ingest_files", [])),
                public_data=self.results.get("public_data"),
                last_month_id=self.coverage["last_month_id"],
                # a product with no taxonomy entry is loaded as 'unclassified' and does not stop
                # the run (PROPOSAL 18.10); recording it here makes the gap visible in the run log
                # the extracts behind this database: file, role, SHA-256, size, rows parsed
                ingest_files=summary.get("ingest_files", []),
                quality=summary.get("quality"),
                unmapped_products=sorted(summary.get("silver", {}).get("unmapped_products", [])),
            )
        else:
            self.record["error"] = message
        self.record["finished_at"] = _now()
        with open(self.published_dir / LOG_NAME, "a", encoding="utf-8") as log:
            log.write(json.dumps(self.record) + "\n")
        return self.record


def publish(**kwargs) -> dict:
    """Run the full rebuild. Returns the run record; never raises for a failed run.

    A failure at any stage fails the whole run and leaves the last good database live. The record
    has a `stages` list (name, status, seconds, detail or error) in the order they ran.
    `accept_challenger` is a written reason that lets a refit the promotion gate refused be
    published anyway; it is recorded. The arguments are those of `PublishRun`."""
    run = PublishRun(**kwargs)
    error = None
    try:
        run.start()
        for name in STAGE_ORDER:
            getattr(run, name)()
    except Exception as exc:  # noqa: BLE001 - the last good database stays live
        logger.exception("Publish failed; the previously published database is unchanged.")
        error = exc
    return run.finish(error)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild and publish the warehouse.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--published-dir", type=Path, default=DEFAULT_PUBLISHED_DIR)
    parser.add_argument(
        "--public-data",
        choices=("committed", "full"),
        default="committed",
        help="committed: check the committed public-data subset (default; CI, containers); "
        "full: reload and re-export it from the raw public files (local only)",
    )
    parser.add_argument(
        "--accept-challenger",
        metavar="REASON",
        default=None,
        help="publish the refit even if the promotion gate refused it; the reason is recorded "
        "(a deliberate, written decision, never a default)",
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
        accept_challenger=(args.accept_challenger or "").strip() or None,
    )
    print(json.dumps(record, indent=2))
    sys.exit(0 if record["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
