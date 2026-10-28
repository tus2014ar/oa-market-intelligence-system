"""Versions of what a run was built from (DL-84): the data, the code and the environment.

Every publish run records them in `run_log.jsonl`, and every row of `pred_batch` carries the data
version, so a prediction can be traced to the extracts and the code behind it. A model's own version
(`pred_batch.model_version`) is made by the scoring job.

- **data**: a short SHA-256 over the (file name, SHA-256) of every input extract, in name order, so
  it changes when any extract changes and not otherwise;
- **code**: the git commit of the checkout (the GitHub Actions commit when set), else `unknown`;
- **environment**: a short SHA-256 of `requirements.txt`, the fully pinned package set.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNKNOWN = "unknown"


def data_version_from_files(files: Iterable[dict]) -> str:
    """From rows with `file_name` and `sha256` (a file listed twice for two roles counts once)."""
    pairs = sorted({(f["file_name"], f["sha256"]) for f in files})
    if not pairs:
        return UNKNOWN
    text = "\n".join(f"{name}:{digest}" for name, digest in pairs)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def data_version(engine) -> str:
    """The data version of a warehouse, from `bronze_ingest_files` (`unknown` if there is none)."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT file_name, sha256 FROM bronze_ingest_files")).all()
    except Exception:  # noqa: BLE001 - a warehouse without the table has no data version
        return UNKNOWN
    return data_version_from_files({"file_name": r[0], "sha256": r[1]} for r in rows)


def code_version(root: Path = REPO_ROOT) -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:10]
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=10", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip() or UNKNOWN
    except Exception:  # noqa: BLE001 - no git, or not a checkout (a container)
        return UNKNOWN


def environment_version(requirements: Path | None = None) -> str:
    path = requirements or REPO_ROOT / "requirements.txt"
    if not path.exists():
        return UNKNOWN
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def run_versions(ingest_files: Iterable[dict]) -> dict:
    return {
        "data": data_version_from_files(ingest_files),
        "code": code_version(),
        "environment": environment_version(),
    }
