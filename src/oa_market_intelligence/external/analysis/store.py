"""Writing analysis results to the Gold tables, with a run identifier on every verdict.

Verdicts are appended under a `run_id`, never overwritten, so an earlier run stays on record. The
Gold analysis tables hold the latest run.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import Engine, text

from oa_market_intelligence.external.common import replace_partition
from oa_market_intelligence.external.schema import VERDICTS


def new_run_id() -> str:
    """`YYYYMMDDTHHMMSSZ-<git commit>`: when it ran and on which code."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - no git is fine; the hash of the time stands in
        sha = hashlib.sha1(stamp.encode()).hexdigest()[:7]
    return f"{stamp}-{sha}"


def write_verdicts(engine: Engine, run_id: str, rows: list[dict]) -> int:
    """Append one row per rule. Each row needs check_id, metric, rule and verdict."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        if row["verdict"] not in VERDICTS:
            raise ValueError(f"unknown verdict label {row['verdict']!r}")
    frame = pd.DataFrame(
        [
            {
                "run_id": run_id,
                "check_id": r["check_id"],
                "metric": r["metric"],
                "value": r.get("value"),
                "threshold": r.get("threshold"),
                "rule": r["rule"],
                "verdict": r["verdict"],
                "note": r.get("note"),
                "created_at": now,
            }
            for r in rows
        ]
    )
    with engine.begin() as conn:
        frame.to_sql("gold_ext_verdicts", conn, if_exists="append", index=False)
    return len(frame)


def latest_verdicts(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                "SELECT * FROM gold_ext_verdicts WHERE run_id = "
                "(SELECT max(run_id) FROM gold_ext_verdicts) ORDER BY check_id, metric"
            ),
            conn,
        )


def replace_gold(engine: Engine, table: str, frame: pd.DataFrame, where: dict | None = None) -> int:
    """Replace a Gold analysis table (or one partition of it) with the latest results."""
    return replace_partition(engine, table, frame, where or {})
