"""Record files downloaded by hand (for example the AMA-licence ASP pages) in the manifest.

Run after placing the files under data/raw/New Datasets/; it adds an entry (size and
SHA-256) for every file the manifest does not know yet."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "New Datasets"  # git-ignored
MANIFEST = ROOT / "_download_manifest.jsonl"

known = set()
for line in MANIFEST.read_text(encoding="utf-8").splitlines():
    if line.strip():
        known.add(json.loads(line)["file"].replace("\\", "/"))

SOURCE_OF = {
    "Medicare Physician & Other Practitioners - by Geography and Service": "https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-geography-and-service",
    "Medicare Physician & Other Practitioners - by Provider and Service": "https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider-and-service",
    "Medicare Part D Prescribers - by Provider and Drug": "https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/medicare-part-d-prescribers-by-provider-and-drug",
    "october_2026_medicare_part_b_payment_limit_file_091626-final_file": "https://www.cms.gov/medicare/payment/part-b-drugs/asp-pricing-files",
}
SINGLE = {
    "OP_DTL_GNRL_PGYR2025_P06302026_06032026.csv": (
        "Open Payments general payments 2025",
        "https://openpaymentsdata.cms.gov/datasets/download",
    ),
    "sdud2024_updatedJuly2026.csv": (
        "Medicaid State Drug Utilization 2024",
        "https://data.medicaid.gov/dataset/61729e5a-7aa8-448c-8903-ba3e0cd0ea3c",
    ),
}

todo = []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or path.name == "_download_manifest.jsonl" or path.suffix == ".part":
        continue
    rel = path.relative_to(ROOT).as_posix()
    if rel not in known:
        todo.append((rel, path))
print(len(todo), "files to record", flush=True)

for i, (rel, path) in enumerate(todo, 1):
    top = rel.split("/")[0]
    if rel in SINGLE:
        dataset, url = SINGLE[rel]
    else:
        dataset = top
        url = SOURCE_OF.get(top, "")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    entry = {
        "dataset": dataset,
        "year": None,
        "url": url,
        "file": rel,
        "bytes": path.stat().st_size,
        "sha256": h.hexdigest(),
        "status": "downloaded manually by owner (recorded afterwards)",
        "at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    with open(MANIFEST, "a", encoding="utf-8") as m:
        m.write(json.dumps(entry) + "\n")
    print(f"[{i}/{len(todo)}] {rel[:90]} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)
print("DONE", flush=True)
