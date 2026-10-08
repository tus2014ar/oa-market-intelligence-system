"""Second batch of public downloads for DL-57: Geographic Variation, MA, NPPES, CDC PLACES,
SEC filings, FDA and timeline PDFs.

Writes into data/raw/New Datasets/ (git-ignored) and appends to _download_manifest.jsonl.
"""

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "New Datasets"  # git-ignored
MANIFEST = ROOT / "_download_manifest.jsonl"
BROWSER = {"User-Agent": "Mozilla/5.0"}
# The SEC requires a contact in the User-Agent. It comes from the environment, never from
# the repository: set SEC_CONTACT_EMAIL to your own address before running.
CONTACT = os.environ.get("SEC_CONTACT_EMAIL")
if not CONTACT:
    raise SystemExit("Set SEC_CONTACT_EMAIL (the SEC requires a contact in the User-Agent).")
SEC = {"User-Agent": f"Penn State DAAN888 capstone research {CONTACT}"}

jobs = []  # (dataset, year, url, dest, headers)

jobs += [
    (
        "Medicare Geographic Variation",
        2024,
        "https://data.cms.gov/sites/default/files/2026-04/cc600d1e-d475-4b0e-80dc-1f64c01ca68c/2014-2024%20Original%20Medicare%20Geographic%20Variation%20Public%20Use%20File.csv",
        ROOT
        / "Medicare Geographic Variation"
        / "2014-2024 Original Medicare Geographic Variation Public Use File.csv",
        BROWSER,
    ),
    (
        "Medicare Advantage Geographic Variation",
        2023,
        "https://data.cms.gov/sites/default/files/2026-07/3cb2bf86-12c3-4901-bec0-c931d381d1d7/MA%20GV%20PUF%202016-2023_RY_2026.csv",
        ROOT / "Medicare Advantage Geographic Variation" / "MA GV PUF 2016-2023_RY_2026.csv",
        BROWSER,
    ),
    (
        "NPPES",
        2026,
        "https://download.cms.gov/nppes/NPPES_Data_Dissemination_September_2026_V2.zip",
        ROOT / "NPPES" / "NPPES_Data_Dissemination_September_2026_V2.zip",
        BROWSER,
    ),
    (
        "CDC PLACES",
        2025,
        "https://data.cdc.gov/api/views/swc5-untb/rows.csv?accessType=DOWNLOAD",
        ROOT / "CDC PLACES" / "PLACES_Local_Data_County_2025_release.csv",
        BROWSER,
    ),
]

pdfs = [
    (
        "FDA Zilretta",
        2017,
        "https://www.accessdata.fda.gov/drugsatfda_docs/nda/2017/208845Orig1s000Approv.pdf",
        "208845Orig1s000Approv.pdf",
    ),
    (
        "FDA Zilretta",
        2017,
        "https://www.accessdata.fda.gov/drugsatfda_docs/label/2017/208845s000lbl.pdf",
        "208845s000lbl_2017_label.pdf",
    ),
    (
        "FDA Zilretta",
        2020,
        "https://www.accessdata.fda.gov/drugsatfda_docs/appletter/2020/208845Orig1s011ltr.pdf",
        "208845Orig1s011ltr_2020.pdf",
    ),
    (
        "FDA Zilretta",
        2024,
        "https://www.accessdata.fda.gov/drugsatfda_docs/appletter/2024/208845Orig1s021ltr.pdf",
        "208845Orig1s021ltr_2024.pdf",
    ),
    (
        "FDA Zilretta",
        2024,
        "https://www.accessdata.fda.gov/drugsatfda_docs/label/2024/208845Orig1s021lbl.pdf",
        "208845Orig1s021lbl_2024_label.pdf",
    ),
    (
        "Event timeline",
        2024,
        "https://www.aha.org/system/files/media/file/2024/08/Change-Healthcare-Cyberattack-Timeline.pdf",
        "AHA_Change_Healthcare_Cyberattack_Timeline.pdf",
    ),
]
for label, year, url, name in pdfs:
    folder = "FDA Zilretta" if label.startswith("FDA") else "Event Timeline"
    jobs.append((label, year, url, ROOT / folder / name, BROWSER))

# SEC filings: 10-K and 10-Q primary documents from the EDGAR submissions index
companies = {
    "Flexion Therapeutics": (1419600, "2019-01-01"),
    "Pacira BioSciences": (1396814, "2021-10-01"),
    "Bioventus": (1665988, "2021-01-01"),
    "Anika Therapeutics": (898437, "2019-01-01"),
}
for name, (cik, since) in companies.items():
    req = urllib.request.Request(
        f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=SEC
    )
    data = json.load(urllib.request.urlopen(req, timeout=60))
    pages = [data["filings"]["recent"]]
    for extra in data["filings"].get("files", []):
        r = urllib.request.Request(f"https://data.sec.gov/submissions/{extra['name']}", headers=SEC)
        pages.append(json.load(urllib.request.urlopen(r, timeout=60)))
        time.sleep(0.3)
    n = 0
    for page in pages:
        for form, date, acc, doc in zip(
            page["form"], page["filingDate"], page["accessionNumber"], page["primaryDocument"]
        ):
            if form in ("10-K", "10-Q") and date >= since and doc:
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
                dest = ROOT / "SEC Filings" / name / f"{date}_{form}_{acc}_{doc}"
                jobs.append((f"SEC {name}", int(date[:4]), url, dest, SEC))
                n += 1
    print(f"{name}: {n} filings (10-K/10-Q since {since})", flush=True)
    time.sleep(0.3)

print(f"{len(jobs)} files planned", flush=True)
ROOT.mkdir(parents=True, exist_ok=True)


def fetch(url, dest, headers):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    with (
        urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180) as r,
        open(part, "wb") as f,
    ):
        expected = int(r.headers.get("Content-Length") or -1)
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
    got = part.stat().st_size
    if expected > 0 and got != expected:
        raise IOError(f"size mismatch: got {got}, expected {expected}")
    if got == 0:
        raise IOError("empty download")
    part.replace(dest)
    return got, h.hexdigest()


failures = []
for i, (label, year, url, dest, headers) in enumerate(jobs, 1):
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[{i}/{len(jobs)}] exists, skipped: {dest.name}", flush=True)
        continue
    for attempt in range(1, 4):
        try:
            size, digest = fetch(url, dest, headers)
            rec = {
                "dataset": label,
                "year": year,
                "url": url,
                "file": str(dest.relative_to(ROOT)),
                "bytes": size,
                "sha256": digest,
                "status": "downloaded",
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            with open(MANIFEST, "a", encoding="utf-8") as m:
                m.write(json.dumps(rec) + "\n")
            print(
                f"[{i}/{len(jobs)}] downloaded: {label} {dest.name[:60]} ({size / 1e6:.2f} MB)",
                flush=True,
            )
            break
        except Exception as error:  # noqa: BLE001
            print(
                f"[{i}/{len(jobs)}] attempt {attempt} failed: {dest.name[:60]}: {error}", flush=True
            )
            time.sleep(4 * attempt)
    else:
        failures.append((label, url))
    if headers is SEC:
        time.sleep(0.25)  # stay well under the SEC's 10 requests per second limit
print("DONE. failures:", json.dumps(failures), flush=True)
