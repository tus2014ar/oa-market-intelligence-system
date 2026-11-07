"""Releasing a published warehouse to S3, and checking one before it is used (DL-124).

The monthly job publishes `data/published/warehouse.db`. To reach the API without rebuilding its
image, the same file is uploaded to a private S3 bucket under a name made from its data version and
its checksum, and a small pointer, `latest.json`, is written last. The pointer says which file is
current, how big it must be, its SHA-256, which data version it holds and the last run record (so
the API's status page describes the data it serves). Because the pointer is written only after the
file is in place, a reader never sees a pointer to a missing or half-written file.

This module uses the standard library only. It builds the pointer (`prepare`, run by the monthly
workflow before the upload) and it checks a pointer and a downloaded file (`validate_pointer`,
`verify_database`, used by the API before it switches to a new warehouse). Nothing here talks to
AWS; the upload is `aws s3 cp` in the workflow and the download is the API's loader.

    python -m oa_market_intelligence.data_release prepare warehouse.db run_log.jsonl out/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

POINTER_KEY = "latest.json"
SCHEMA = 1
MAX_DATABASE_BYTES = 200 * 1024 * 1024
MAX_POINTER_BYTES = 64 * 1024
KEY_PATTERN = re.compile(r"^published/[0-9a-f]{12}-[0-9a-f]{12}/warehouse\.db$")
VERSION_PATTERN = re.compile(r"^[0-9a-f]{12}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_TABLES = ("pred_batch", "model_artifacts", "pred_inputs")


class ReleaseError(ValueError):
    """A pointer or a downloaded file that must not be used. The message is safe to log."""


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(path: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def database_facts(path: Path | str) -> dict:
    """What a warehouse file holds that the release depends on: its integrity, the three tables
    the API serves from, the one data version its forecasts carry and the month they start from.
    Raises ReleaseError if the file is not a usable published warehouse."""
    try:
        con = _connect(path)
    except sqlite3.Error:
        raise ReleaseError("the file is not a readable database") from None
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ReleaseError("the database failed its integrity check")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        if missing:
            raise ReleaseError(f"the database lacks the table(s): {', '.join(missing)}")
        versions = [r[0] for r in con.execute("SELECT DISTINCT data_version FROM pred_batch")]
        if len(versions) != 1 or not VERSION_PATTERN.match(str(versions[0])):
            raise ReleaseError("the forecasts do not carry exactly one data version")
        month = con.execute("SELECT MAX(run_month_id) FROM pred_batch").fetchone()[0]
        rows = con.execute("SELECT COUNT(*) FROM pred_batch").fetchone()[0]
        if not rows or month is None:
            raise ReleaseError("the database holds no forecasts")
        return {"data_version": str(versions[0]), "run_month_id": int(month), "rows": int(rows)}
    except sqlite3.Error:
        raise ReleaseError("the database could not be read") from None
    finally:
        con.close()


def last_run_record(run_log: Path | str) -> dict:
    """The most recent record of the run log (an empty dict if there is none)."""
    path = Path(run_log)
    if not path.exists():
        return {}
    lines = [ln for ln in path.read_text("utf-8").splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else {}


def build_pointer(db_path: Path | str, run_log: Path | str, *, now: datetime | None = None) -> dict:
    """The pointer for a warehouse file: where it will live in the bucket and how to check it."""
    facts = database_facts(db_path)
    digest = sha256_file(db_path)
    size = Path(db_path).stat().st_size
    if size > MAX_DATABASE_BYTES:
        raise ReleaseError("the database is larger than a release may be")
    pointer = {
        "schema": SCHEMA,
        "key": f"published/{facts['data_version']}-{digest[:12]}/warehouse.db",
        "sha256": digest,
        "size": size,
        "data_version": facts["data_version"],
        "run_month_id": facts["run_month_id"],
        "created_at": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": last_run_record(run_log),
    }
    return validate_pointer(pointer)


def validate_pointer(obj: object) -> dict:
    """The pointer, if it is one this code understands and can trust; otherwise ReleaseError.
    The key is matched against a fixed pattern, so a pointer can never name any other object."""
    if not isinstance(obj, dict):
        raise ReleaseError("the pointer is not an object")
    if obj.get("schema") != SCHEMA:
        raise ReleaseError("the pointer has an unknown schema")
    key = obj.get("key")
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise ReleaseError("the pointer names an object it may not")
    sha = obj.get("sha256")
    if not isinstance(sha, str) or not SHA_PATTERN.match(sha):
        raise ReleaseError("the pointer's checksum is not a SHA-256")
    version = obj.get("data_version")
    if not isinstance(version, str) or not VERSION_PATTERN.match(version):
        raise ReleaseError("the pointer's data version is not valid")
    if key != f"published/{version}-{sha[:12]}/warehouse.db":
        raise ReleaseError("the pointer's key does not match its version and checksum")
    size = obj.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DATABASE_BYTES:
        raise ReleaseError("the pointer's size is not valid")
    month = obj.get("run_month_id")
    if not isinstance(month, int) or isinstance(month, bool) or not 190001 <= month <= 299912:
        raise ReleaseError("the pointer's month is not valid")
    if not isinstance(obj.get("created_at"), str):
        raise ReleaseError("the pointer has no creation time")
    run = obj.get("run", {})
    if not isinstance(run, dict):
        raise ReleaseError("the pointer's run record is not an object")
    if len(json.dumps(run)) > MAX_POINTER_BYTES // 2:
        raise ReleaseError("the pointer's run record is too large")
    return obj


def verify_database(path: Path | str, pointer: dict) -> None:
    """A downloaded file must be exactly what the pointer promises: its size, its checksum, a
    readable warehouse and the data version the pointer names. Raises ReleaseError otherwise."""
    p = Path(path)
    if not p.exists() or p.stat().st_size != pointer["size"]:
        raise ReleaseError("the downloaded file is not the size the pointer promises")
    if sha256_file(p) != pointer["sha256"]:
        raise ReleaseError("the downloaded file does not match the pointer's checksum")
    facts = database_facts(p)
    if facts["data_version"] != pointer["data_version"]:
        raise ReleaseError("the downloaded file holds a different data version from the pointer")
    if facts["run_month_id"] != pointer["run_month_id"]:
        raise ReleaseError("the downloaded file starts from a different month from the pointer")


def prepare(db_path: Path | str, run_log: Path | str, out_dir: Path | str) -> dict:
    """Write `latest.json` for a warehouse into `out_dir` and return the pointer."""
    pointer = build_pointer(db_path, run_log)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / POINTER_KEY).write_text(json.dumps(pointer, indent=2, sort_keys=True), encoding="utf-8")
    return pointer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("prepare", help="write latest.json and print the key and size")
    cmd.add_argument("db", type=Path)
    cmd.add_argument("run_log", type=Path)
    cmd.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        pointer = prepare(args.db, args.run_log, args.out_dir)
    except ReleaseError as error:
        print(f"release refused: {error}", file=sys.stderr)
        return 1
    print(f"key={pointer['key']}")
    print(f"size={pointer['size']}")
    print(f"data_version={pointer['data_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
