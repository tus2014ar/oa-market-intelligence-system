"""Reconciliation checks for the external database (protocol section 5, rule R13).

Each check returns (name, passed, detail). Targets are the ones fixed in the protocol; a load that
drifts from them fails here.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, text

from oa_market_intelligence.external.common import read_manifest, verify_file

Check = tuple[str, bool, str]


def _count(engine: Engine, sql: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar() or 0)


def verify_reference(engine: Engine, raw_root: Path) -> list[Check]:
    manifest = read_manifest(raw_root)
    unique_files = {entry["file"] for entry in manifest}
    unreadable = []
    for entry in {e["file"]: e for e in manifest}.values():
        try:
            verify_file(raw_root, entry)
        except (FileNotFoundError, RuntimeError) as error:
            unreadable.append(f"{entry['file']}: {error}")
    expected = {
        "dim_state rows": ("SELECT count(*) FROM dim_state", 61),
        "dim_state US states and DC": (
            "SELECT count(*) FROM dim_state WHERE is_us_state_or_dc = 1",
            51,
        ),
        "dim_hcpcs_code rows": ("SELECT count(*) FROM dim_hcpcs_code", 27),
        "dim_hcpcs_code CPT rows without text": (
            "SELECT count(*) FROM dim_hcpcs_code WHERE is_cpt = 1 AND short_description IS NULL",
            2,
        ),
        "dim_quarter rows": ("SELECT count(*) FROM dim_quarter", 32),
        "crosswalk groups": (
            "SELECT count(DISTINCT iqvia_group) FROM bridge_specialty_crosswalk",
            12,
        ),
        "catalogue rows equal manifest files": (
            "SELECT count(*) FROM bronze_external_files",
            len(unique_files),
        ),
    }
    checks: list[Check] = []
    for name, (sql, target) in expected.items():
        got = _count(engine, sql)
        checks.append((name, got == target, f"{got} (target {target})"))
    checks.append(
        (
            "every catalogued file matches its manifest entry on disk",
            not unreadable,
            "all match" if not unreadable else "; ".join(unreadable[:3]),
        )
    )
    return checks
