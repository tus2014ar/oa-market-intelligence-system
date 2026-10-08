"""Reference tables of the external database: states, calendar, billing codes, crosswalk, bridges,
and the download catalogue (DL-59, step 4a).

Every frame is built by a pure function, so each can be tested without a database.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

from oa_market_intelligence.external.codes import (
    CODE_GROUPS,
    DRUG_FAMILY,
    GROUP_OF,
)
from oa_market_intelligence.external.common import (
    REFERENCE_DIR,
    log_run,
    now,
    read_manifest,
    replace_table,
)

CPT_CODES = frozenset(CODE_GROUPS["C_denominator_procedures"])

# Product-name keywords for the IQVIA side of the drug bridge. Deliberately conservative: kits,
# combination products and topical forms are left unmapped rather than guessed.
_BRIDGE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("ZILRETTA", "zilretta_triamcinolone_er"),
    ("KENALOG", "triamcinolone_acetonide"),
    ("TRIAMCINOLONE ACTN", "triamcinolone_acetonide"),
    ("DEPO-MEDROL", "methylprednisolone_acetate"),
    ("METHYLPRED ACE", "methylprednisolone_acetate"),
    ("SOLU-MEDROL", "methylprednisolone_sodium_succinate"),
    ("METHYLPRED SOD SUC", "methylprednisolone_sodium_succinate"),
    ("A-METHAPRED", "methylprednisolone_sodium_succinate"),
    ("CELESTONE", "betamethasone"),
    ("BETAMETH ACE/SOD PHOS", "betamethasone"),
    ("DEXAMETH S PH", "dexamethasone_sodium_phosphate"),
    ("SOLU-CORTEF", "hydrocortisone_sodium_succinate"),
)


def states_frame(reference_dir: Path = REFERENCE_DIR) -> pd.DataFrame:
    return pd.read_csv(reference_dir / "us_states.csv", dtype=str, keep_default_na=False).assign(
        is_us_state_or_dc=lambda f: f["is_us_state_or_dc"].astype(int)
    )


def years_frame(first: int, last: int) -> pd.DataFrame:
    return pd.DataFrame({"year": list(range(first, last + 1))})


def quarters_frame(first_year: int, last_year: int) -> pd.DataFrame:
    rows = []
    for year in range(first_year, last_year + 1):
        for quarter in range(1, 5):
            first_month = (quarter - 1) * 3 + 1
            rows.append(
                {
                    "quarter_id": f"{year}Q{quarter}",
                    "year": year,
                    "quarter": quarter,
                    "first_month_id": year * 100 + first_month,
                    "last_month_id": year * 100 + first_month + 2,
                }
            )
    return pd.DataFrame(rows)


def hcpcs_frame(descriptions: pd.DataFrame) -> pd.DataFrame:
    """The 27 approved codes. `descriptions` has year, code, description from the Part B
    geography profile; only J-codes take wording (CPT descriptions are AMA copyright)."""
    years = descriptions.groupby("code")["year"].agg(["min", "max"])
    latest = descriptions.sort_values("year").groupby("code")["description"].last()
    rows = []
    for group, codes in CODE_GROUPS.items():
        for code in codes:
            is_cpt = code in CPT_CODES
            rows.append(
                {
                    "hcpcs_code": code,
                    "code_group": group,
                    "drug_family": DRUG_FAMILY[code],
                    "is_cpt": int(is_cpt),
                    "short_description": None if is_cpt else latest.get(code),
                    "first_year_seen": years["min"].get(code),
                    "last_year_seen": years["max"].get(code),
                }
            )
    frame = pd.DataFrame(rows)
    assert set(frame["hcpcs_code"]) == set(GROUP_OF)
    return frame


def crosswalk_frame(reference_dir: Path = REFERENCE_DIR) -> pd.DataFrame:
    return pd.read_csv(reference_dir / "specialty_crosswalk.csv", dtype=str, keep_default_na=False)


def drug_family_bridge(product_names: list[str]) -> pd.DataFrame:
    rows = []
    for name in sorted(set(product_names)):
        for keyword, family in _BRIDGE_KEYWORDS:
            if keyword in name.upper():
                rows.append({"drug_family": family, "iqvia_product_name": name})
                break
    return pd.DataFrame(rows, columns=["drug_family", "iqvia_product_name"])


def catalogue_frame(entries: list[dict]) -> pd.DataFrame:
    """One row per file: the latest manifest entry for each (reruns append new entries)."""
    latest: dict[str, dict] = {}
    for entry in entries:
        latest[entry["file"]] = entry
    rows = [
        {
            "dataset": e["dataset"],
            "relative_path": e["file"],
            "source_url": e.get("url"),
            "bytes": e.get("bytes"),
            "sha256": e.get("sha256"),
            "status": e.get("status"),
            "downloaded_at": e.get("at"),
            "rows_loaded": None,
            "loaded_at": None,
        }
        for e in latest.values()
    ]
    return pd.DataFrame(rows)


def load_reference(
    engine: Engine,
    *,
    raw_root: Path,
    descriptions_csv: Path,
    product_names: list[str],
    reference_dir: Path = REFERENCE_DIR,
    year_range: tuple[int, int] = (2014, 2026),
    quarter_range: tuple[int, int] = (2019, 2026),
) -> dict[str, int]:
    """Load every reference table. Safe to repeat: each table is replaced whole."""
    started = now()
    frames = {
        "dim_state": states_frame(reference_dir),
        "dim_year": years_frame(*year_range),
        "dim_quarter": quarters_frame(*quarter_range),
        "dim_hcpcs_code": hcpcs_frame(pd.read_csv(descriptions_csv)),
        "bridge_specialty_crosswalk": crosswalk_frame(reference_dir),
        "bridge_drug_family": drug_family_bridge(product_names),
        "bronze_external_files": catalogue_frame(read_manifest(raw_root)),
    }
    # keep what earlier loads recorded about each file
    with engine.connect() as conn:
        previous = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                text("SELECT relative_path, rows_loaded, loaded_at FROM bronze_external_files")
            )
        }
    catalogue = frames["bronze_external_files"]
    catalogue["rows_loaded"] = [
        previous.get(p, (None, None))[0] for p in catalogue["relative_path"]
    ]
    catalogue["loaded_at"] = [previous.get(p, (None, None))[1] for p in catalogue["relative_path"]]
    counts = {table: replace_table(engine, table, frame) for table, frame in frames.items()}
    log_run(
        engine,
        "reference",
        None,
        rows_read=sum(counts.values()),
        rows_loaded=sum(counts.values()),
        status="ok",
        note=", ".join(f"{t}={n}" for t, n in counts.items()),
        started_at=started,
    )
    return counts
