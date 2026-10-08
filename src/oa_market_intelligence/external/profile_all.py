"""Raw-data profiling of every public source (DL-59, step 0).

Run:  PYTHONPATH=src python -m oa_market_intelligence.external.profile_all [--only name ...]

Reports data quality only: structure, empties, distinct values, numeric and date ranges, naming
variants and coverage. It never computes a share, trend or ranking, and it never writes per-year
Zilretta counts. Results go to data/reference/external_profile/ (small files, committed).
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from oa_market_intelligence.external.codes import (
    ALL_CODES,
    GROUP_OF,
    HYALURONIC_PRODUCT_PATTERN,
    NSAID_NAME_PATTERN,
    ORAL_STEROID_NAME_PATTERN,
    STEROID_PRODUCT_PATTERN,
    ZILRETTA_PATTERN,
)
from oa_market_intelligence.external.profile import (
    iter_csv,
    iter_zip_csv,
    profile_chunks,
    sha256_of,
)

RAW_DEFAULT = Path("data/raw/New Datasets")
OUT_DEFAULT = Path("data/reference/external_profile")
MONTH_TO_QUARTER = {"jan": 1, "apr": 2, "jul": 3, "oct": 4}
OP_PRODUCT_COLUMNS = [
    f"Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_{i}" for i in range(1, 6)
]
OP_USECOLS = [
    "Change_Type",
    "Covered_Recipient_Type",
    "Covered_Recipient_NPI",
    "Covered_Recipient_Specialty_1",
    "Recipient_State",
    "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
    "Total_Amount_of_Payment_USDollars",
    "Date_of_Payment",
    "Number_of_Payments_Included_in_Total_Amount",
    "Form_of_Payment_or_Transfer_of_Value",
    "Nature_of_Payment_or_Transfer_of_Value",
    "Dispute_Status_for_Publication",
    "Related_Product_Indicator",
    "Program_Year",
    "Indicate_Drug_or_Biological_or_Device_or_Medical_Supply_1",
    *OP_PRODUCT_COLUMNS,
]


# ---------------------------------------------------------------- pure helpers (tested)


def asp_quarter(filename: str) -> str | None:
    """'january-2021-asp-pricing-file.zip' -> '2021Q1'; 'jul_2019_...' -> '2019Q3'."""
    name = filename.lower()
    year = re.search(r"(20\d\d)", name)
    month = next((m for m in MONTH_TO_QUARTER if re.search(rf"(?<![a-z]){m}", name)), None)
    if not (year and month):
        return None
    return f"{year.group(1)}Q{MONTH_TO_QUARTER[month]}"


def find_asp_header(lines: list[str]) -> int | None:
    """Index of the line that starts the ASP table, whatever title lines precede it."""
    for index, line in enumerate(lines):
        first = (next(csv.reader([line]), None) or [""])[0]
        if first.strip().lower() in ("hcpcs code", "hcpcs"):
            return index
    return None


def sec_period(text: str) -> str | None:
    """The 'period ended' date of a 10-K or 10-Q cover page, as written."""
    flat = re.sub(r"<[^>]+>", " ", text[:400_000])
    flat = re.sub(r"&#?\w+;", " ", flat)
    flat = re.sub(r"\s+", " ", flat)
    found = re.search(
        r"(?:quarterly period ended|fiscal year ended|period ended)\s*:?\s*"
        r"([A-Z][a-z]+\s+\d{1,2},\s*\d{4})",
        flat,
    )
    return found.group(1) if found else None


def normalise_specialty(name: str) -> str:
    text = name.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def crosswalk_candidates(
    left: Iterable[str], right: Iterable[str], *, n: int = 3, cutoff: float = 0.5
) -> list[dict]:
    """For each left name, up to n nearest right names (a suggestion list for human review)."""
    right = sorted(set(right))
    by_norm = {normalise_specialty(r): r for r in right}
    rows = []
    for name in sorted(set(left)):
        scored = difflib.get_close_matches(
            normalise_specialty(name), list(by_norm), n=n, cutoff=cutoff
        )
        exact = normalise_specialty(name) in by_norm
        suggestions = [by_norm[s] for s in scored]
        rows.append(
            {
                "left_name": name,
                "exact_match": exact,
                "suggestion_1": suggestions[0] if len(suggestions) > 0 else "",
                "suggestion_2": suggestions[1] if len(suggestions) > 1 else "",
                "suggestion_3": suggestions[2] if len(suggestions) > 2 else "",
                "decision": "",
            }
        )
    return rows


def matching_names(series: pd.Series, pattern: str) -> pd.Series:
    """The non-empty values of a text column that match a case-insensitive pattern."""
    values = series[series.astype(str).str.strip().ne("")]
    return values[values.str.contains(pattern, case=False, regex=True, na=False)]


class DuplicateCounter:
    """Counts repeated keys across chunks using 8-byte row hashes, so a 25-million-row file needs
    about 200 MB instead of a Python set of tuples."""

    def __init__(self) -> None:
        self._parts: list[np.ndarray] = []

    def add(self, frame: pd.DataFrame) -> None:
        self._parts.append(pd.util.hash_pandas_object(frame, index=False).to_numpy())

    def duplicates(self) -> int:
        if not self._parts:
            return 0
        values = np.concatenate(self._parts)
        return int(len(values) - len(np.unique(values)))


def is_ndc11(series: pd.Series) -> pd.Series:
    """True where a drug code is exactly eleven digits (leading zeros kept, so read as text)."""
    return series.astype(str).str.fullmatch(r"\d{11}")


def observe(chunks: Iterable[pd.DataFrame], callback: Callable[[pd.DataFrame], None]) -> Iterator:
    """Pass chunks through unchanged while a callback inspects each one."""
    for chunk in chunks:
        callback(chunk)
        yield chunk


def _write_json(out: Path, name: str, payload) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(
        json.dumps(payload, indent=1, allow_nan=False, default=str), encoding="utf-8"
    )


def _write_csv(out: Path, name: str, rows: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / f"{name}.csv", index=False)


def _year_of(path: Path, pattern: str) -> int:
    found = re.search(pattern, path.name)
    if not found:
        raise ValueError(f"no year in {path.name}")
    value = int(found.group(1))
    return value + 2000 if value < 100 else value


# ---------------------------------------------------------------- per-source profiles


def profile_partb_geo(raw: Path, out: Path) -> None:
    folder = raw / "Medicare Physician & Other Practitioners - by Geography and Service"
    result, descriptions = {}, []
    for path in sorted(folder.rglob("*_Geo.csv")):
        year = _year_of(path, r"_D(\d{2})_Geo")
        result[year] = profile_chunks(
            iter_csv(path),
            track=(
                "Rndrng_Prvdr_Geo_Lvl",
                "Place_Of_Srvc",
                "HCPCS_Drug_Ind",
                "Rndrng_Prvdr_Geo_Desc",
            ),
            numeric=("Tot_Benes", "Tot_Srvcs", "Avg_Mdcr_Pymt_Amt"),
            key=("Rndrng_Prvdr_Geo_Lvl", "Rndrng_Prvdr_Geo_Cd", "HCPCS_Cd", "Place_Of_Srvc"),
        )
        pairs = set()
        for chunk in iter_csv(
            path, usecols=["HCPCS_Cd", "HCPCS_Desc"], keep=lambda f: f["HCPCS_Cd"].isin(ALL_CODES)
        ):
            pairs |= set(zip(chunk["HCPCS_Cd"], chunk["HCPCS_Desc"], strict=True))
        descriptions += [{"year": year, "code": c, "description": d} for c, d in sorted(pairs)]
    _write_json(out, "partb_geo", result)
    _write_csv(out, "partb_geo_code_descriptions", descriptions)


def profile_partb_provider(raw: Path, out: Path) -> None:
    folder = raw / "Medicare Physician & Other Practitioners - by Provider and Service"
    track = (
        "Rndrng_Prvdr_Type",
        "Rndrng_Prvdr_Ent_Cd",
        "Place_Of_Srvc",
        "HCPCS_Cd",
        "Rndrng_Prvdr_State_Abrvtn",
        "Rndrng_Prvdr_Mdcr_Prtcptg_Ind",
        "code_group",
    )
    sources = {
        int(p.name.split("_")[2]): p for p in sorted(folder.glob("*/PartB_ProviderService_*.csv"))
    }
    full_2024 = next(folder.rglob("PHY_R26_*Prov_Svc.csv"))
    result, specialties = {}, defaultdict(Counter)
    for year in (*sorted(sources), 2024):
        names = specialties[year]

        def tally(chunk, names=names):
            names.update(chunk["Rndrng_Prvdr_Type"].value_counts().to_dict())

        if year == 2024:
            columns = list(pd.read_csv(sources[2023], nrows=0).columns)
            wanted = [c for c in columns if c != "code_group"]
            chunks = iter_csv(
                full_2024, usecols=wanted, keep=lambda f: f["HCPCS_Cd"].isin(ALL_CODES)
            )
            chunks = (c.assign(code_group=c["HCPCS_Cd"].map(GROUP_OF)) for c in chunks)
        else:
            chunks = iter_csv(sources[year])
        result[year] = profile_chunks(
            observe(chunks, tally),
            track=track,
            numeric=("Tot_Benes", "Tot_Srvcs", "Avg_Mdcr_Pymt_Amt"),
            key=("Rndrng_NPI", "HCPCS_Cd", "Place_Of_Srvc"),
        )
    _write_json(out, "partb_provider", result)
    _write_csv(
        out,
        "partb_provider_specialty_names",
        [
            {"year": y, "specialty": s, "n_rows_all_approved_codes": n}
            for y, counter in sorted(specialties.items())
            for s, n in sorted(counter.items())
        ],
    )


def profile_partd_geo(raw: Path, out: Path) -> None:
    folder = raw / "Medicare Part D Prescribers - by Geography and Drug"
    result, drugs = {}, defaultdict(set)
    for path in sorted(folder.rglob("*_Geo*.csv")):
        year = _year_of(path, r"_DY(\d{2})_Geo")
        result[year] = profile_chunks(
            iter_csv(path),
            track=("Prscrbr_Geo_Lvl",),
            numeric=("Tot_Clms", "Tot_Benes", "Tot_Drug_Cst"),
            key=("Prscrbr_Geo_Lvl", "Prscrbr_Geo_Cd", "Brnd_Name", "Gnrc_Name"),
        )
        for chunk in iter_csv(path, usecols=["Brnd_Name", "Gnrc_Name"]):
            pairs = chunk.drop_duplicates()
            text = pairs["Brnd_Name"] + " " + pairs["Gnrc_Name"]
            for family, pattern in (
                ("nsaid", NSAID_NAME_PATTERN),
                ("steroid", ORAL_STEROID_NAME_PATTERN),
            ):
                hit = pairs[text.str.contains(pattern, case=False, regex=True, na=False)]
                for brand, generic in zip(hit["Brnd_Name"], hit["Gnrc_Name"], strict=True):
                    drugs[(family, brand, generic)].add(year)
    _write_json(out, "partd_geo", result)
    _write_csv(
        out,
        "partd_candidate_drugs",
        [
            {
                "family": f,
                "brand_name": b,
                "generic_name": g,
                "years_present": ",".join(map(str, sorted(y))),
            }
            for (f, b, g), y in sorted(drugs.items())
        ],
    )


CHUNK = 250_000  # rows per chunk for the two large optional files (tests shrink it)
PARTD_PROVIDER_ID_COLUMNS = ("Prscrbr_Last_Org_Name", "Prscrbr_First_Name", "Prscrbr_City")


def profile_partd_provider(raw: Path, out: Path) -> None:
    """Medicare Part D by Provider and Drug (optional, not loaded). Structure only: prescriber
    names and cities are counted for empties and never listed, and there are no volumes by drug."""
    folder = raw / "Medicare Part D Prescribers - by Provider and Drug"
    key = ["Prscrbr_NPI", "Brnd_Name", "Gnrc_Name"]
    track = (
        "Prscrbr_State_Abrvtn",
        "Prscrbr_Type_Src",
        "GE65_Sprsn_Flag",
        "GE65_Bene_Sprsn_Flag",
    )
    numeric = (
        "Tot_Clms",
        "Tot_30day_Fills",
        "Tot_Day_Suply",
        "Tot_Drug_Cst",
        "Tot_Benes",
        "GE65_Tot_Clms",
        "GE65_Tot_Drug_Cst",
    )
    result: dict = {}
    types_by_year: dict[int, Counter] = {}
    for path in sorted(folder.rglob("*_NPIBN.csv")):
        year = _year_of(path, r"_DY(\d{2})_NPIBN")
        prescribers: set[str] = set()
        pairs: set[tuple[str, str]] = set()
        types: Counter = Counter()
        duplicates = DuplicateCounter()

        def inspect(
            chunk,
            prescribers=prescribers,
            pairs=pairs,
            types=types,
            duplicates=duplicates,
        ):
            prescribers.update(chunk["Prscrbr_NPI"].unique())
            pairs.update(zip(chunk["Brnd_Name"], chunk["Gnrc_Name"], strict=True))
            types.update(chunk["Prscrbr_Type"].value_counts().to_dict())
            duplicates.add(chunk[key])

        profile = profile_chunks(
            observe(iter_csv(path, chunksize=CHUNK), inspect), track=track, numeric=numeric
        )
        names = pd.Series([f"{b} {g}" for b, g in pairs], dtype="string")
        profile.update(
            n_distinct_prescribers=len(prescribers),
            n_distinct_drug_names=len(pairs),
            key=key,
            duplicate_keys=duplicates.duplicates(),
            n_prescriber_types=len(types),
            n_nsaid_name_pairs=int(names.str.contains(NSAID_NAME_PATTERN, case=False).sum()),
            n_oral_steroid_name_pairs=int(
                names.str.contains(ORAL_STEROID_NAME_PATTERN, case=False).sum()
            ),
            zilretta_name_matches=int(names.str.contains(ZILRETTA_PATTERN, case=False).sum()),
            zilretta_name_pairs=sorted(
                [b, g] for b, g in pairs if re.search(ZILRETTA_PATTERN, f"{b} {g}", re.IGNORECASE)
            ),
            identifying_columns_not_listed=list(PARTD_PROVIDER_ID_COLUMNS),
        )
        result[year] = profile
        types_by_year[year] = types
    _write_json(out, "partd_provider", result)
    _write_csv(
        out,
        "partd_provider_prescriber_types",
        [
            {"year": year, "prescriber_type": name, "n_rows": count}
            for year, counter in sorted(types_by_year.items())
            for name, count in sorted(counter.items())
        ],
    )


def profile_sdud(raw: Path, out: Path) -> None:
    """Medicaid State Drug Utilization (optional, not loaded). Structure only: no volumes or
    amounts by product; product names are listed only where they match the approved name lists."""
    key = ["Utilization Type", "State", "NDC", "Year", "Quarter"]
    patterns = {
        "zilretta": ZILRETTA_PATTERN,
        "hyaluronic": HYALURONIC_PRODUCT_PATTERN,
        "steroid": STEROID_PRODUCT_PATTERN,
    }
    result: dict = {}
    for path in sorted(raw.glob("sdud*.csv")):
        year = _year_of(path, r"sdud(\d{4})")
        ndcs: set[str] = set()
        products: set[str] = set()
        bad_ndc = [0]

        def inspect(chunk, ndcs=ndcs, products=products, bad_ndc=bad_ndc):
            ndcs.update(chunk["NDC"].unique())
            products.update(chunk["Product Name"].str.strip().unique())
            bad_ndc[0] += int((~is_ndc11(chunk["NDC"])).sum())

        profile = profile_chunks(
            observe(iter_csv(path, chunksize=CHUNK), inspect),
            track=("Utilization Type", "State", "Year", "Quarter", "Suppression Used"),
            numeric=(
                "Units Reimbursed",
                "Number of Prescriptions",
                "Total Amount Reimbursed",
                "Medicaid Amount Reimbursed",
                "Non Medicaid Amount Reimbursed",
            ),
            key=tuple(key),
        )
        names = pd.Series(sorted(products), dtype="string")
        profile.update(
            n_distinct_ndc=len(ndcs),
            n_ndc_not_eleven_digits=bad_ndc[0],
            n_distinct_product_names=len(products),
            matching_product_names={
                label: sorted(names[names.str.contains(pattern, case=False)].tolist())[:50]
                for label, pattern in patterns.items()
            },
        )
        result[year] = profile
    _write_json(out, "medicaid_sdud", result)


def profile_asp(raw: Path, out: Path) -> None:
    rows = []
    for path in sorted((raw / "ASP Pricing Files").rglob("*.zip")):
        with zipfile.ZipFile(path) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            lines = archive.read(member).decode("latin-1").splitlines()
        header_at = find_asp_header(lines)
        header = next(csv.reader([lines[header_at]])) if header_at is not None else []
        codes = {
            (next(csv.reader([ln]), None) or [""])[0].strip()
            for ln in lines[(header_at or 0) + 1 :]
        }
        present = [c for c in ALL_CODES if c in codes]
        rows.append(
            {
                "file": path.name,
                "quarter": asp_quarter(path.name),
                "n_title_lines": header_at,
                "n_codes": len(codes - {""}),
                "n_columns": len(header),
                "header": "|".join(h.strip() for h in header),
                "approved_codes_present": " ".join(present),
                "n_approved_codes_present": len(present),
            }
        )
    _write_csv(out, "asp_quarters", rows)


def _op_sources(raw: Path) -> list[tuple[int, Path, bool]]:
    sources = [
        (int(p.parent.name), p, True) for p in sorted((raw / "Open Payments").glob("*/PGYR*.zip"))
    ]
    sources += [(2025, p, False) for p in sorted(raw.glob("OP_DTL_GNRL_PGYR2025_*.csv"))]
    return sources


def _profile_openpay_source(args: tuple[int, str, bool]) -> dict:
    year, path_text, zipped = args
    path = Path(path_text)
    union = "|".join((ZILRETTA_PATTERN, HYALURONIC_PRODUCT_PATTERN, STEROID_PRODUCT_PATTERN))
    names: Counter = Counter()
    makers: Counter = Counter()
    state = {"zilretta_rows": 0}

    def inspect(chunk: pd.DataFrame) -> None:
        zilretta = pd.Series(False, index=chunk.index)
        for column in OP_PRODUCT_COLUMNS:
            hit = matching_names(chunk[column], union)
            names.update(hit.value_counts().to_dict())
            zilretta |= chunk[column].str.contains(
                ZILRETTA_PATTERN, case=False, regex=True, na=False
            )
        state["zilretta_rows"] += int(zilretta.sum())
        maker = chunk.loc[zilretta, "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name"]
        makers.update(maker.value_counts().to_dict())

    reader = (
        iter_zip_csv(path, member_contains="DTL_GNRL", usecols=OP_USECOLS, chunksize=250_000)
        if zipped
        else iter_csv(path, usecols=OP_USECOLS, chunksize=250_000)
    )
    profile = profile_chunks(
        observe(reader, inspect),
        track=(
            "Change_Type",
            "Covered_Recipient_Type",
            "Nature_of_Payment_or_Transfer_of_Value",
            "Form_of_Payment_or_Transfer_of_Value",
            "Program_Year",
            "Dispute_Status_for_Publication",
            "Related_Product_Indicator",
            "Indicate_Drug_or_Biological_or_Device_or_Medical_Supply_1",
            "Covered_Recipient_Specialty_1",
            "Recipient_State",
        ),
        numeric=(
            "Total_Amount_of_Payment_USDollars",
            "Number_of_Payments_Included_in_Total_Amount",
        ),
        dates=("Date_of_Payment",),
        date_format="%m/%d/%Y",
    )
    return {
        "year": year,
        "profile": profile,
        "names": dict(names),
        "makers": dict(makers),
        "zilretta_rows_seen": state["zilretta_rows"],  # kept only for the reconciliation check
    }


def profile_openpayments(raw: Path, out: Path) -> None:
    jobs = [(y, str(p), z) for y, p, z in _op_sources(raw)]
    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_profile_openpay_source, jobs))
    names: dict[str, set] = defaultdict(set)
    totals: Counter = Counter()
    makers: Counter = Counter()
    for r in results:
        for name, n in r["names"].items():
            names[name].add(r["year"])
            totals[name] += n
        makers.update(r["makers"])
    _write_json(out, "openpayments", {str(r["year"]): r["profile"] for r in results})
    _write_json(
        out,
        "openpayments_coverage",
        {
            str(r["year"]): {"zilretta_product_rows_present": r["zilretta_rows_seen"] > 0}
            for r in results
        },
    )
    _write_csv(
        out,
        "openpayments_product_name_variants",
        [
            {
                "product_name": n,
                "rows_all_years": totals[n],
                "years_present": ",".join(map(str, sorted(y))),
            }
            for n, y in sorted(names.items())
        ],
    )
    _write_csv(
        out,
        "openpayments_zilretta_manufacturers",
        [{"manufacturer": m, "rows_all_years": n} for m, n in sorted(makers.items())],
    )
    # reconciliation target for the 2025 file only (the earlier text search found 3,275 lines)
    last = next(r for r in results if r["year"] == 2025)
    _write_json(
        out, "openpayments_reconciliation_2025", {"zilretta_rows_seen": last["zilretta_rows_seen"]}
    )


def profile_geovar(raw: Path, out: Path) -> None:
    path = next((raw / "Medicare Geographic Variation").glob("*.csv"))
    profile = profile_chunks(
        iter_csv(path),
        track=("YEAR", "BENE_GEO_LVL", "BENE_AGE_LVL"),
        numeric=("BENES_TOTAL_CNT", "BENES_MA_CNT", "MA_PRTCPTN_RATE"),
        key=("YEAR", "BENE_GEO_LVL", "BENE_GEO_CD", "BENE_AGE_LVL"),
    )
    _write_json(out, "geographic_variation", profile)
    ma = next((raw / "Medicare Advantage Geographic Variation").glob("*.csv"))
    _write_json(
        out,
        "ma_geographic_variation",
        profile_chunks(
            iter_csv(ma),
            track=("YEAR", "STATE"),
            numeric=("BENES_MA_CNT",),
            key=("YEAR", "STATE", "BENE_GEO_CD"),
        ),
    )


def profile_places(raw: Path, out: Path) -> None:
    path = next((raw / "CDC PLACES").glob("*.csv"))
    _write_json(
        out,
        "cdc_places",
        profile_chunks(
            iter_csv(path),
            track=(
                "Year",
                "Measure",
                "MeasureId",
                "Data_Value_Type",
                "DataSource",
                "Category",
                "Data_Value_Footnote_Symbol",
            ),
            numeric=("Data_Value", "Low_Confidence_Limit", "High_Confidence_Limit"),
            key=("LocationID", "MeasureId", "DataValueTypeID", "Year"),
        ),
    )


def profile_nppes(raw: Path, out: Path) -> None:
    path = next((raw / "NPPES").glob("*.zip"))
    with zipfile.ZipFile(path) as archive:
        member = next(
            n for n in archive.namelist() if n.startswith("npidata_pfile") and n.endswith(".csv")
        )
    usecols = [
        "NPI",
        "Entity Type Code",
        "Provider Business Practice Location Address State Name",
        "Healthcare Provider Taxonomy Code_1",
        "Provider Enumeration Date",
        "NPI Deactivation Date",
        "Last Update Date",
    ]
    profile = profile_chunks(
        iter_zip_csv(path, member_contains=member, usecols=usecols, chunksize=500_000),
        track=(
            "Entity Type Code",
            "Provider Business Practice Location Address State Name",
            "Healthcare Provider Taxonomy Code_1",
        ),
        dates=("Provider Enumeration Date", "NPI Deactivation Date", "Last Update Date"),
        max_distinct=20_000,
        date_format="%m/%d/%Y",
    )
    _write_json(out, "nppes", profile)


def profile_sec(raw: Path, out: Path) -> None:
    rows = []
    for path in sorted((raw / "SEC Filings").rglob("*.htm")):
        text = path.read_text(encoding="utf-8", errors="replace")
        filed, form = path.name.split("_")[0], path.name.split("_")[1]
        rows.append(
            {
                "company": path.parent.name,
                "file": path.name,
                "form": form,
                "filed": filed,
                "period_end": sec_period(text),
                "size_kb": round(path.stat().st_size / 1024),
                "n_zilretta_mentions": len(re.findall(r"zilretta", text, flags=re.I)),
                "n_viscosupplement_mentions": len(
                    re.findall(r"durolane|orthovisc|monovisc|euflexxa|synvisc", text, flags=re.I)
                ),
            }
        )
    _write_csv(out, "sec_filings", rows)


def profile_manifest(raw: Path, out: Path) -> None:
    entries = [
        json.loads(ln) for ln in (raw / "_download_manifest.jsonl").read_text("utf-8").splitlines()
    ]
    missing, size_bad, sha_bad, sha_checked = [], [], [], 0
    for entry in entries:
        path = raw / entry["file"]
        if not path.exists():
            missing.append(entry["file"])
            continue
        if entry.get("bytes") and path.stat().st_size != entry["bytes"]:
            size_bad.append(entry["file"])
        elif entry.get("sha256") and path.stat().st_size < 200_000_000:
            sha_checked += 1
            if sha256_of(path) != entry["sha256"]:
                sha_bad.append(entry["file"])
    _write_json(
        out,
        "manifest_check",
        {
            "entries": len(entries),
            "missing": missing,
            "size_mismatch": size_bad,
            "sha256_checked": sha_checked,
            "sha256_mismatch": sha_bad,
        },
    )


def profile_specialty_names(raw: Path, out: Path, warehouse: Path) -> None:
    connection = sqlite3.connect(f"file:{warehouse.as_posix()}?mode=ro", uri=True)
    try:
        iqvia = [r[0] for r in connection.execute("SELECT specialty_name FROM dim_specialty")]
    finally:
        connection.close()
    cms = pd.read_csv(out / "partb_provider_specialty_names.csv")["specialty"].unique().tolist()
    _write_csv(out, "specialty_crosswalk_candidates", crosswalk_candidates(iqvia, cms))


SOURCES: dict[str, Callable[[Path, Path], None]] = {
    "manifest": profile_manifest,
    "partb_geo": profile_partb_geo,
    "partb_provider": profile_partb_provider,
    "partd_geo": profile_partd_geo,
    "partd_provider": profile_partd_provider,
    "sdud": profile_sdud,
    "asp": profile_asp,
    "geovar": profile_geovar,
    "places": profile_places,
    "sec": profile_sec,
    "nppes": profile_nppes,
    "openpayments": profile_openpayments,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW_DEFAULT)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--warehouse", type=Path, default=Path("data/published/warehouse.db"))
    parser.add_argument("--only", nargs="*", choices=[*SOURCES, "specialties"])
    args = parser.parse_args(argv)
    for name in args.only or [*SOURCES, "specialties"]:
        print(f"profiling {name} ...", flush=True)
        if name == "specialties":
            profile_specialty_names(args.raw, args.out, args.warehouse)
        else:
            SOURCES[name](args.raw, args.out)
        print(f"  done: {name}", flush=True)


if __name__ == "__main__":
    main()
