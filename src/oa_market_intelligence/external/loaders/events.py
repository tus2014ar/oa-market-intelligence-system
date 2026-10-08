"""Dated events (DL-59, step 5): the hand-built `data/reference/events.csv` into `dim_event`.

Each row carries its source document and a `verified` flag: 1 only when the date was read from a
primary document we hold, 0 when it came from a secondary source or is an observation of our own.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.common import (
    REFERENCE_DIR,
    log_run,
    now,
    replace_table,
)

COLUMNS = ["event_date", "event_end_date", "event_type", "description", "source", "verified"]


def events_frame(reference_dir: Path = REFERENCE_DIR) -> pd.DataFrame:
    frame = pd.read_csv(Path(reference_dir) / "events.csv", dtype=str, keep_default_na=False)
    if list(frame.columns) != COLUMNS:
        raise ValueError(f"events.csv columns must be {COLUMNS}")
    for column in ("event_date", "event_end_date"):
        parsed = pd.to_datetime(
            frame[column].where(frame[column].ne("")), format="%Y-%m-%d", errors="coerce"
        )
        bad = frame[column].ne("") & parsed.isna()
        if bad.any():
            raise ValueError(f"{column}: not an ISO date: {frame.loc[bad, column].iloc[0]!r}")
    ends = frame["event_end_date"].ne("")
    if (frame.loc[ends, "event_end_date"] < frame.loc[ends, "event_date"]).any():
        raise ValueError("an event ends before it starts (event_end_date)")
    if not frame["verified"].isin(["0", "1"]).all():
        raise ValueError("verified must be 0 or 1")
    for column in ("description", "source", "event_type"):
        if frame[column].str.strip().eq("").any():
            raise ValueError(f"{column} must not be empty")
    frame = frame.assign(
        verified=frame["verified"].astype(int),
        event_end_date=frame["event_end_date"].replace("", None),
    )
    return frame.sort_values(["event_date", "event_type"]).reset_index(drop=True)


def load_events(engine: Engine, reference_dir: Path = REFERENCE_DIR) -> int:
    started = now()
    frame = events_frame(reference_dir)
    loaded = replace_table(engine, "dim_event", frame)
    log_run(
        engine,
        "events",
        None,
        rows_read=len(frame),
        rows_loaded=loaded,
        status="ok",
        note=f"{int(frame['verified'].sum())} verified",
        started_at=started,
    )
    return loaded
