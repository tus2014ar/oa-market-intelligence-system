"""Download the public datasets approved for DL-57 into data/raw/New Datasets/ (git-ignored).

Resumable (skips files already complete), writes a manifest of URL, size and SHA-256 per file.

Usage:  python scripts/external_data/download_cms_and_open_payments.py <catalogue.json>
where the catalogue is the CMS data catalogue (https://data.cms.gov/data.json) saved to a
file. See docs/external_data_refresh.md.
"""

import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "New Datasets"  # git-ignored
CATALOG = Path(sys.argv[1])
UA = {"User-Agent": "Mozilla/5.0"}
MANIFEST = ROOT / "_download_manifest.jsonl"

jobs = []  # (dataset label, year, url, destination)

# 1. ASP price files, 2021 to October 2025
asp = {
    2021: [
        "january-2021-asp-pricing-file",
        "april-2021-asp-pricing-file",
        "july-2021-asp-pricing-file",
        "october-2021-asp-pricing-file",
    ],
    2022: [
        "january-2022-asp-pricing-file",
        "april-2022-asp-pricing-file",
        "july-2022-asp-pricing-file",
        "october-2022-asp-pricing-file",
    ],
    2023: [
        "january-2023-asp-pricing-file",
        "april-2023-asp-pricing-file",
        "july-2023-asp-pricing-file",
        "october-2023-asp-pricing-file",
    ],
    2024: [
        "january-2024-asp-pricing-file",
        "april-2024-asp-pricing-file",
        "july-2024-asp-pricing-file",
        "october-2024-asp-pricing-file",
    ],
    2025: [
        "january-2025-asp-pricing-file-03/11/25-final-file",
        "april-2025-asp-pricing-file",
        "july-2025-asp-pricing-file",
        "october-2025-asp-pricing-final-file",
    ],
}
for year, names in asp.items():
    for name in names:
        fname = name.replace("/", "-") + ".zip"
        jobs.append(
            (
                "ASP Pricing Files",
                year,
                f"https://www.cms.gov/files/zip/{name}.zip",
                ROOT / "ASP Pricing Files" / str(year) / fname,
            )
        )

# 2 and 3. Catalog CSVs: Part B by Geography and Service 2019-2023,
# Part D by Geography and Drug 2019-2024
catalog = json.load(open(CATALOG, encoding="utf-8"))
wanted = {
    "Medicare Physician & Other Practitioners - by Geography and Service": range(2019, 2024),
    "Medicare Part D Prescribers - by Geography and Drug": range(2019, 2025),
}
seen = set()
for ds in catalog["dataset"]:
    title = ds.get("title", "")
    for base, years in wanted.items():
        if not title.startswith(base):
            continue
        year = int(title.split(":")[-1].strip()[:4])
        if year not in years:
            continue
        for dist in ds.get("distribution", []):
            url = dist.get("downloadURL") or ""
            if dist.get("format") == "CSV" and url.endswith(".csv") and (base, year) not in seen:
                seen.add((base, year))
                jobs.append(
                    (base, year, url, ROOT / base / base / str(year) / url.rsplit("/", 1)[-1])
                )

# 4. Open Payments yearly zips, 2019 to 2024 (same release stamp as the 2025 file already held)
for year in range(2019, 2025):
    fname = f"PGYR{year}_P06302026_06032026.zip"
    jobs.append(
        (
            "Open Payments",
            year,
            f"https://download.cms.gov/openpayments/{fname}",
            ROOT / "Open Payments" / str(year) / fname,
        )
    )

print(f"{len(jobs)} files planned", flush=True)
ROOT.mkdir(parents=True, exist_ok=True)


def remote_size(url):
    req = urllib.request.Request(url, method="HEAD", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers.get("Content-Length", -1))


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = remote_size(url)
    if dest.exists() and dest.stat().st_size == size:
        return "skipped (already complete)", size, None
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
    got = part.stat().st_size
    if size != -1 and got != size:
        raise IOError(f"size mismatch: got {got}, expected {size}")
    part.replace(dest)
    return "downloaded", got, h.hexdigest()


failures = []
for i, (label, year, url, dest) in enumerate(jobs, 1):
    for attempt in range(1, 4):
        try:
            status, size, digest = fetch(url, dest)
            record = {
                "dataset": label,
                "year": year,
                "url": url,
                "file": str(dest.relative_to(ROOT)),
                "bytes": size,
                "sha256": digest,
                "status": status,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            with open(MANIFEST, "a", encoding="utf-8") as m:
                m.write(json.dumps(record) + "\n")
            print(
                f"[{i}/{len(jobs)}] {status}: {label} {year} {dest.name} ({size / 1e6:.1f} MB)",
                flush=True,
            )
            break
        except Exception as error:  # noqa: BLE001
            print(f"[{i}/{len(jobs)}] attempt {attempt} failed: {dest.name}: {error}", flush=True)
            time.sleep(5 * attempt)
    else:
        failures.append(str(dest))
print("DONE. failures:", failures, flush=True)
