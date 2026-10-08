"""Pull the approved HCPCS codes from the Medicare Part B Provider-and-Service datasets, 2019-2023.

Filtered API pull, no provider names or street addresses (NPI, specialty, state, setting and
volumes only).
Writes one CSV per year, a per-code/per-year count table, and a manifest record per file.
"""

import csv
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "New Datasets"  # git-ignored
OUT = ROOT / "Medicare Physician & Other Practitioners - by Provider and Service"
MANIFEST = ROOT / "_download_manifest.jsonl"
UA = {"User-Agent": "Mozilla/5.0"}

DATASETS = {
    2019: "867b8ac7-ccb7-4cc9-873d-b24340d89e32",
    2020: "c957b49e-1323-49e7-8678-c09da387551d",
    2021: "31dc2c47-f297-4948-bfb4-075e1bec3a02",
    2022: "e650987d-01b7-4f09-b75e-b0b075afbf98",
    2023: "0e9f2f2b-7bf9-451a-912c-e02e654dd725",
}
CODES = {
    "A_primary": ["J3304", "J3301", "J1010", "J1020", "J1030", "J1040", "J0702", "J1100"],
    "B_context_hyaluronic": [
        "J7318",
        "J7320",
        "J7321",
        "J7322",
        "J7323",
        "J7324",
        "J7325",
        "J7326",
        "J7327",
        "J7328",
        "J7329",
        "J7331",
        "J7332",
    ],
    "C_denominator_procedures": ["20610", "20611"],
    "D_sensitivity_iv_steroids": ["J2919", "J2920", "J2930", "J1720"],
}
GROUP_OF = {code: group for group, codes in CODES.items() for code in codes}
COLUMNS = [
    "Rndrng_NPI",
    "Rndrng_Prvdr_Ent_Cd",
    "Rndrng_Prvdr_State_Abrvtn",
    "Rndrng_Prvdr_State_FIPS",
    "Rndrng_Prvdr_Type",
    "Rndrng_Prvdr_Mdcr_Prtcptg_Ind",
    "HCPCS_Cd",
    "HCPCS_Desc",
    "HCPCS_Drug_Ind",
    "Place_Of_Srvc",
    "Tot_Benes",
    "Tot_Srvcs",
    "Tot_Bene_Day_Srvcs",
    "Avg_Sbmtd_Chrg",
    "Avg_Mdcr_Alowd_Amt",
    "Avg_Mdcr_Pymt_Amt",
    "Avg_Mdcr_Stdzd_Amt",
]
PAGE = 5000


def get(url):
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return json.load(r)
        except Exception as error:  # noqa: BLE001
            if attempt == 4:
                raise
            print(f"  retry {attempt} after {error}", flush=True)
            time.sleep(4 * attempt)


counts = []
for year, uuid in DATASETS.items():
    base = f"https://data.cms.gov/data-api/v1/dataset/{uuid}/data"
    folder = OUT / str(year)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"PartB_ProviderService_{year}_approved_codes.csv"
    total = 0
    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS + ["code_group"])
        writer.writeheader()
        for code in (c for codes in CODES.values() for c in codes):
            flt = urllib.parse.quote("filter[HCPCS_Cd]", safe="") + "=" + code
            expected = get(f"{base}/stats?{flt}")["found_rows"]
            got = 0
            offset = 0
            while offset < expected:
                url = f"{base}?{flt}&size={PAGE}&offset={offset}&column={','.join(COLUMNS)}"
                rows = get(url)
                if not rows:
                    break
                for row in rows:
                    row["code_group"] = GROUP_OF[code]
                    writer.writerow(row)
                got += len(rows)
                offset += PAGE
                time.sleep(0.15)
            counts.append(
                {
                    "year": year,
                    "code": code,
                    "group": GROUP_OF[code],
                    "expected_rows": expected,
                    "fetched_rows": got,
                }
            )
            if got != expected:
                print(f"  MISMATCH {year} {code}: expected {expected}, got {got}", flush=True)
            total += got
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    with open(MANIFEST, "a", encoding="utf-8") as m:
        m.write(
            json.dumps(
                {
                    "dataset": (
                        "Medicare Physician & Other Practitioners - by Provider and Service "
                        "(filtered API pull)"
                    ),
                    "year": year,
                    "url": base + "?filter[HCPCS_Cd]=<code>&column=<selected columns>",
                    "file": str(dest.relative_to(ROOT)),
                    "bytes": dest.stat().st_size,
                    "sha256": digest,
                    "rows": total,
                    "status": "downloaded (filtered)",
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
            + "\n"
        )
    print(f"{year}: {total} rows, {dest.stat().st_size / 1e6:.1f} MB", flush=True)

with open(OUT / "approved_codes_row_counts.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["year", "code", "group", "expected_rows", "fetched_rows"])
    w.writeheader()
    w.writerows(counts)
print("DONE", flush=True)
