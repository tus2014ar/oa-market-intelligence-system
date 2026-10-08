"""NPPES provider registry, September 2026 snapshot (DL-59, step 4c).

Counts individual providers (entity type 1) by state and primary taxonomy code. Every row is
accounted for: not an individual, deactivated, no taxonomy code, a state that cannot be mapped to a
two-letter code, or counted. The tally is stored in the run log.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from oa_market_intelligence.external.common import finish_partition, now, verified_entry
from oa_market_intelligence.external.loaders.reference import states_frame
from oa_market_intelligence.external.profile import iter_zip_csv

FOLDER = "NPPES"
SOURCE = "nppes"
STATE_COLUMN = "Provider Business Practice Location Address State Name"
TAXONOMY_COLUMN = "Healthcare Provider Taxonomy Code_1"
USECOLS = [
    "NPI",
    "Entity Type Code",
    STATE_COLUMN,
    TAXONOMY_COLUMN,
    "NPI Deactivation Date",
]


def snapshot_date_from_member(name: str) -> str:
    """'npidata_pfile_20050523-20260913.csv' -> '2026-09-13'."""
    found = re.search(r"-(\d{4})(\d{2})(\d{2})\.csv$", name)
    if not found:
        raise ValueError(f"cannot read a snapshot date from {name!r}")
    return "-".join(found.groups())


def clean_state(
    series: pd.Series, valid_codes: set[str], name_to_code: dict[str, str]
) -> pd.Series:
    """Two-letter code from a code or a full state name (any case); else missing."""
    upper = series.astype(str).str.strip().str.upper()
    by_code = upper.where(upper.isin(valid_codes))
    return by_code.fillna(upper.map(name_to_code))


def aggregate_nppes_chunk(chunk: pd.DataFrame, states: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    codes = set(states["state_code"])
    names = dict(zip(states["state_name"].str.upper(), states["state_code"], strict=True))
    stats = {"rows_read": len(chunk)}
    individual = chunk["Entity Type Code"].str.strip().eq("1")
    stats["not_individual"] = int((~individual).sum())
    chunk = chunk[individual]
    active = chunk["NPI Deactivation Date"].str.strip().eq("")
    stats["deactivated"] = int((~active).sum())
    chunk = chunk[active]
    has_taxonomy = chunk[TAXONOMY_COLUMN].str.strip().ne("")
    stats["blank_taxonomy"] = int((~has_taxonomy).sum())
    chunk = chunk[has_taxonomy]
    state = clean_state(chunk[STATE_COLUMN], codes, names)
    stats["unmapped_state"] = int(state.isna().sum())
    kept = pd.DataFrame(
        {"state_code": state, "taxonomy_code": chunk[TAXONOMY_COLUMN].str.strip()}
    ).dropna(subset=["state_code"])
    stats["counted"] = len(kept)
    counts = kept.groupby(["state_code", "taxonomy_code"]).size().rename("n").reset_index()
    return counts, stats


def load_nppes(engine: Engine, raw_root: Path) -> int:
    path = next((Path(raw_root) / FOLDER).glob("*.zip"))
    started = now()
    verified_entry(raw_root, path)
    with zipfile.ZipFile(path) as archive:
        member = next(
            n for n in archive.namelist() if n.startswith("npidata_pfile") and n.endswith(".csv")
        )
    snapshot = snapshot_date_from_member(member)
    states = states_frame()
    parts, totals = [], {}
    for chunk in iter_zip_csv(
        path, member_contains="npidata_pfile", usecols=USECOLS, chunksize=500_000
    ):
        counts, stats = aggregate_nppes_chunk(chunk, states)
        parts.append(counts)
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.groupby(["state_code", "taxonomy_code"], as_index=False)["n"].sum()
    frame = merged.rename(columns={"n": "n_individual_providers"}).assign(snapshot_date=snapshot)
    frame = frame[["snapshot_date", "state_code", "taxonomy_code", "n_individual_providers"]]
    return finish_partition(
        engine,
        raw_root,
        source=SOURCE,
        year=int(snapshot[:4]),
        path=path,
        table="fact_ext_provider_counts",
        frame=frame,
        rows_read=totals["rows_read"],
        where={"snapshot_date": snapshot},
        started_at=started,
        note=json.dumps(totals),
    )
