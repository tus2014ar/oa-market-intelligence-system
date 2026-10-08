"""Restore the raw public-data folder from the committed manifest.

    PYTHONPATH=src python -m oa_market_intelligence.external.fetch --dry-run
    PYTHONPATH=src python -m oa_market_intelligence.external.fetch

`data/reference/external_manifest.jsonl` lists every raw file (source link, size, SHA-256). This
tool downloads each file that is missing from `data/raw/New Datasets/` and checks it against the
manifest. A file that differs (a source can revise a file after we recorded it) is kept and
reported, never deleted. Files that cannot be fetched by a plain link are listed with how to get
them: the hand-downloaded ones (licence pages), the filtered API pull (a script) and, without a
contact address, the SEC filings. Nothing here changes a file that already matches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from oa_market_intelligence.external.common import (
    DEFAULT_RAW_ROOT,
    REFERENCE_DIR,
    ChecksumMismatch,
    verify_file,
)

MANIFEST_NAME = "_download_manifest.jsonl"
DEFAULT_MANIFEST = REFERENCE_DIR / "external_manifest.jsonl"
BROWSER_AGENT = "Mozilla/5.0"
SEC_HOST = "www.sec.gov"
SEC_PAUSE_SECONDS = 0.25  # well under the SEC's limit of 10 requests a second
ATTEMPTS = 3


def read_entries(manifest: Path | str) -> list[dict]:
    lines = Path(manifest).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def latest_entries(entries: list[dict]) -> list[dict]:
    """One entry per file, the last one winning; file paths use forward slashes."""
    latest: dict[str, dict] = {}
    for item in entries:
        item = {**item, "file": item["file"].replace("\\", "/")}
        latest[item["file"]] = item
    return list(latest.values())


def classify(entry: dict) -> str:
    """How a file can be restored: http (a plain link), sec (needs a contact), api_pull (the
    filtered pull script) or manual (downloaded by hand from a licence page)."""
    url = str(entry.get("url", ""))
    if "manually" in str(entry.get("status", "")) or not url.startswith("http"):
        return "manual"
    if "filtered API pull" in str(entry.get("dataset", "")) or "(filtered)" in str(
        entry.get("status", "")
    ):
        return "api_pull"
    if urlparse(url).hostname == SEC_HOST:
        return "sec"
    return "http"


def _state(root: Path, entry: dict) -> str:
    try:
        verify_file(root, entry)
    except FileNotFoundError:
        return "missing"
    except ChecksumMismatch:
        return "differs"
    return "ok"


def download(url: str, dest: Path, headers: dict, opener, sleep) -> tuple[int, str]:
    """Download to a `.part` file, retrying; returns (size, sha256). The `.part` file is removed
    on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            digest = hashlib.sha256()
            size = 0
            with opener(urllib.request.Request(url, headers=headers), timeout=180) as response:
                expected = int(response.headers.get("Content-Length") or -1)
                with open(part, "wb") as out:
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            if size == 0:
                raise OSError("empty download")
            if expected > 0 and size != expected:
                raise OSError(f"size mismatch: got {size}, expected {expected}")
            part.replace(dest)
            return size, digest.hexdigest()
        except Exception:
            part.unlink(missing_ok=True)
            if attempt == ATTEMPTS:
                raise
            sleep(4 * attempt)
    raise AssertionError("unreachable")


def restore(
    raw_root: Path | str,
    manifest: Path | str = DEFAULT_MANIFEST,
    *,
    only: str | None = None,
    dry_run: bool = False,
    sec_contact: str | None = None,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
) -> dict:
    root = Path(raw_root)
    entries = latest_entries(read_entries(manifest))
    if only:
        wanted = only.lower()
        entries = [
            e for e in entries if wanted in e["dataset"].lower() or wanted in e["file"].lower()
        ]
    report: dict = {
        "ok": [],
        "downloaded": [],
        "differs": [],
        "failed": [],
        "manual": [],
        "api_pull": [],
        "sec_skipped": [],
        "would_download": [],
    }
    for entry in entries:
        name = entry["file"]
        state = _state(root, entry)
        if state == "ok":
            report["ok"].append(name)
            continue
        if state == "differs":
            report["differs"].append(name)
            continue
        kind = classify(entry)
        if kind == "manual":
            report["manual"].append(entry)
        elif kind == "api_pull":
            report["api_pull"].append(name)
        elif kind == "sec" and not sec_contact:
            report["sec_skipped"].append(name)
        elif dry_run:
            report["would_download"].append(name)
        else:
            agent = BROWSER_AGENT
            if kind == "sec":
                agent = f"Penn State DAAN888 capstone research {sec_contact}"
            try:
                size, digest = download(
                    entry["url"], root / name, {"User-Agent": agent}, opener, sleep
                )
            except Exception:  # noqa: BLE001 - one bad file must not stop the rest
                report["failed"].append(name)
                continue
            matches = size == entry.get("bytes") and digest == entry.get("sha256")
            report["downloaded" if matches else "differs"].append(name)
            if kind == "sec":
                sleep(SEC_PAUSE_SECONDS)
    if not dry_run:
        target = root / MANIFEST_NAME
        if not target.exists():
            root.mkdir(parents=True, exist_ok=True)
            target.write_text(Path(manifest).read_text(encoding="utf-8"), encoding="utf-8")
    pending = ("differs", "failed", "manual", "api_pull", "sec_skipped", "would_download")
    report["complete"] = not any(report[key] for key in pending)
    return report


def _print(report: dict) -> None:
    print(f"matching already: {len(report['ok'])}; downloaded now: {len(report['downloaded'])}")
    if report["would_download"]:
        print(f"would download ({len(report['would_download'])}):")
        for name in report["would_download"]:
            print("  ", name)
    for key, label in (
        ("differs", "differ from the manifest (kept; the source may have been revised)"),
        ("failed", "failed to download"),
        ("sec_skipped", "SEC files skipped (set SEC_CONTACT_EMAIL to fetch them)"),
    ):
        if report[key]:
            print(f"{label} ({len(report[key])}):")
            for name in report[key]:
                print("  ", name)
    if report["api_pull"]:
        print(
            f"filtered API pull ({len(report['api_pull'])} files): run "
            "scripts/external_data/pull_partb_provider.py"
        )
    if report["manual"]:
        print(
            f"download by hand ({len(report['manual'])} files; see docs/external_data_refresh.md):"
        )
        for item in report["manual"]:
            print("  ", item["file"], "<-", item["url"])
    print("COMPLETE" if report["complete"] else "NOT COMPLETE")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--only", help="restore only datasets or files containing this text")
    parser.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = restore(
        args.raw,
        args.manifest,
        only=args.only,
        dry_run=args.dry_run,
        sec_contact=os.environ.get("SEC_CONTACT_EMAIL"),
    )
    _print(report)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
