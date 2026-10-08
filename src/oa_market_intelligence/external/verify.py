"""Reconciliation checks for the external database (protocol section 5, rule R13).

Each check returns (name, passed, detail). Targets are the ones fixed in the protocol; a load that
drifts from them fails here.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from sqlalchemy import Engine, text

from oa_market_intelligence.external.codes import ALL_CODES, classify_partd_drug
from oa_market_intelligence.external.common import REFERENCE_DIR, read_manifest, verify_file
from oa_market_intelligence.external.loaders import asp, geovar, partb_geo, partd_geo
from oa_market_intelligence.external.profile_all import asp_quarter, find_asp_header

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


# ---------------------------------------------------------------- the small sources (4b)
#
# Each check recounts the raw file with the plain `csv` module, a different code path from the
# loaders (which use pandas), then compares with what was loaded.


def _rows(path: Path):
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        yield from csv.DictReader(handle)


def _loaded_by(engine: Engine, table: str, column: str) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {column}, count(*) FROM {table} GROUP BY {column}"))
        return {key: n for key, n in rows}


def _compare(name: str, expected: dict, got: dict) -> Check:
    keys = set(expected) | set(got)
    bad = {k: (expected.get(k), got.get(k)) for k in keys if expected.get(k) != got.get(k)}
    if bad:
        return (name, False, f"differences: {dict(list(bad.items())[:4])}")
    return (name, True, f"{sum(got.values())} rows in {len(got)} groups")


def _scalar(engine: Engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).first()


def _check_partb_geo(engine: Engine, raw_root: Path) -> list[Check]:
    expected = {
        year: sum(1 for r in _rows(path) if r["HCPCS_Cd"] in ALL_CODES)
        for year, path in partb_geo.files(raw_root)
    }
    row = _scalar(
        engine,
        "SELECT n_providers, benes, services FROM fact_ext_partb_geo WHERE year = 2024 "
        "AND geo_level = 'National' AND hcpcs_code = 'J3304' AND setting = 'O'",
    )
    return [
        _compare(
            "Part B geography rows per year",
            expected,
            _loaded_by(engine, "fact_ext_partb_geo", "year"),
        ),
        (
            "Zilretta national office row, 2024 (4,878 providers, 47,150 patients)",
            row is not None and tuple(row) == (4878, 47150, 3431291.6),
            str(tuple(row) if row else None),
        ),
    ]


def _check_partd_geo(engine: Engine, raw_root: Path) -> list[Check]:
    expected = {
        year: sum(1 for r in _rows(path) if classify_partd_drug(r["Gnrc_Name"]))
        for year, path in partd_geo.files(raw_root)
    }
    return [
        _compare(
            "Part D geography rows per year",
            expected,
            _loaded_by(engine, "fact_ext_partd_geo", "year"),
        )
    ]


def _check_asp(engine: Engine, raw_root: Path) -> list[Check]:
    expected = {}
    for path in sorted((raw_root / asp.FOLDER).rglob("*.zip")):
        with zipfile.ZipFile(path) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            lines = archive.read(member).decode("latin-1").splitlines()
        start = find_asp_header(lines)
        reader = csv.reader(io.StringIO("\n".join(lines[start + 1 :])))
        expected[asp_quarter(path.name)] = sum(1 for r in reader if r and r[0].strip() in ALL_CODES)
    with_limit = _scalar(
        engine,
        "SELECT count(DISTINCT quarter_id) FROM fact_ext_asp_price "
        "WHERE hcpcs_code = 'J3304' AND payment_limit IS NOT NULL",
    )[0]
    return [
        _compare(
            "price-file rows per quarter",
            expected,
            _loaded_by(engine, "fact_ext_asp_price", "quarter_id"),
        ),
        (
            "Zilretta has a payment limit in all 26 quarters",
            with_limit == 26 == len(expected),
            f"{with_limit} of {len(expected)}",
        ),
    ]


def _check_geovar(engine: Engine, raw_root: Path) -> list[Check]:
    path = next((raw_root / geovar.FOLDER).glob("*.csv"))
    expected = sum(
        1
        for r in _rows(path)
        if r["BENE_GEO_LVL"] == "National"
        or (r["BENE_GEO_LVL"] == "State" and r["BENE_GEO_CD"].strip())
    )
    got = _count(engine, "SELECT count(*) FROM fact_ext_geo_variation")
    outside = _count(
        engine,
        "SELECT count(*) FROM fact_ext_geo_variation "
        "WHERE ma_participation_rate < 0 OR ma_participation_rate > 1",
    )
    checks = [
        (
            "Geographic Variation national and state rows",
            got == expected,
            f"{got} (recount {expected})",
        ),
        ("Advantage participation rates all between 0 and 1", outside == 0, f"{outside} outside"),
    ]
    ma_files = list((raw_root / "Medicare Advantage Geographic Variation").glob("*.csv"))
    if ma_files:
        with engine.connect() as conn:
            loaded = {
                (int(y), code): n
                for y, code, n in conn.execute(
                    text(
                        "SELECT year, geo_code, benes_ma FROM fact_ext_geo_variation "
                        "WHERE geo_level = 'State' AND age_level = 'All' AND benes_ma IS NOT NULL"
                    )
                )
            }
        joined = mismatched = 0
        for row in _rows(ma_files[0]):
            key = (int(row["YEAR"]), row["BENE_GEO_CD"].strip())
            if key in loaded and row["BENES_MA_CNT"].strip().isdigit():
                joined += 1
                mismatched += int(loaded[key] != int(row["BENES_MA_CNT"]))
        checks.append(
            (
                "Advantage counts agree between the two Medicare files (state-years)",
                joined >= 400 and mismatched == 0,
                f"{joined} compared, {mismatched} differ",
            )
        )
    return checks


_SMALL_CHECKS = {
    "partb_geo": _check_partb_geo,
    "partd_geo": _check_partd_geo,
    "asp": _check_asp,
    "geovar": _check_geovar,
}


def verify_small_sources(
    engine: Engine, raw_root: Path, sources: list[str] | None = None
) -> list[Check]:
    checks: list[Check] = []
    for source in sources if sources is not None else list(_SMALL_CHECKS):
        checks += _SMALL_CHECKS[source](engine, Path(raw_root))
    return checks


# ---------------------------------------------------------------- the provider-level sources (4c)
#
# References are independent of the loaders: the committed profiling outputs (made by a different
# program) and plain recounts of the raw files.

PROFILE_DIR = REFERENCE_DIR / "external_profile"
J3304_PROVIDER_ROWS = {2019: 912, 2020: 1088, 2021: 1294, 2022: 1262, 2023: 1181}


def _profile(name: str) -> dict:
    return json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _check_partb_provider(engine: Engine, raw_root: Path) -> list[Check]:
    expected = {int(y): v["n_rows"] for y, v in _profile("partb_provider").items()}
    zilretta = {
        int(y): n
        for y, n in _loaded_by(
            engine, "fact_ext_partb_provider WHERE hcpcs_code = 'J3304'", "year"
        ).items()
    }
    return [
        _compare(
            "provider rows per year equal the profile",
            expected,
            _loaded_by(engine, "fact_ext_partb_provider", "year"),
        ),
        _compare(
            "Zilretta provider rows 2019 to 2023 (912, 1,088, 1,294, 1,262, 1,181)",
            J3304_PROVIDER_ROWS,
            {y: n for y, n in zilretta.items() if y in J3304_PROVIDER_ROWS},
        ),
    ]


def _check_nppes(engine: Engine, raw_root: Path) -> list[Check]:
    profile = _profile("nppes")
    row = _scalar(
        engine,
        "SELECT note, rows_read, rows_loaded FROM external_load_runs "
        "WHERE source = 'nppes' AND status = 'ok' ORDER BY run_id DESC",
    )
    if row is None:
        return [("registry load recorded", False, "no run in the log")]
    stats = json.loads(row[0])
    dropped = (
        stats["not_individual"]
        + stats["deactivated"]
        + stats["blank_taxonomy"]
        + stats["unmapped_state"]
    )
    individuals = int(profile["columns"]["Entity Type Code"]["top_values"]["1"])
    counted = _count(
        engine, "SELECT coalesce(sum(n_individual_providers), 0) FROM fact_ext_provider_counts"
    )
    covered = _count(
        engine,
        "SELECT coalesce(sum(n_individual_providers), 0) FROM fact_ext_provider_counts "
        "WHERE taxonomy_code IN (SELECT taxonomy_code FROM bridge_taxonomy_specialty)",
    )
    return [
        (
            "registry rows read equal the profile",
            stats["rows_read"] == profile["n_rows"],
            f"{stats['rows_read']} (profile {profile['n_rows']})",
        ),
        (
            "registry individuals equal the profile's entity type 1 count",
            stats["rows_read"] - stats["not_individual"] == individuals,
            f"{stats['rows_read'] - stats['not_individual']} (profile {individuals})",
        ),
        (
            "every registry row is accounted for",
            stats["rows_read"] == dropped + stats["counted"] and stats["counted"] == counted,
            f"read {stats['rows_read']} = dropped {dropped} + counted {stats['counted']}",
        ),
        (
            "at least 99% of counted providers have a taxonomy code in the NUCC list",
            counted > 0 and covered / counted >= 0.99,
            f"{covered / counted:.4f}" if counted else "no rows",
        ),
    ]


def _check_places(engine: Engine, raw_root: Path) -> list[Check]:
    path = next((raw_root / "CDC PLACES").glob("*.csv"))
    expected = len(
        {
            r["LocationID"]
            for r in _rows(path)
            if r["MeasureId"] == "ARTHRITIS"
            and r["Data_Value_Type"] == "Age-adjusted prevalence"
            and r["StateAbbr"].strip() != "US"
        }
    )
    got = _count(engine, "SELECT count(*) FROM fact_ext_arthritis_prevalence")
    outside = _count(
        engine,
        "SELECT count(*) FROM fact_ext_arthritis_prevalence "
        "WHERE prevalence_pct < 0 OR prevalence_pct > 100",
    )
    in_dim_state = _count(
        engine,
        "SELECT count(*) FROM fact_ext_arthritis_prevalence "
        "WHERE state_code NOT IN (SELECT state_code FROM dim_state)",
    )
    return [
        ("arthritis locations equal a recount", got == expected, f"{got} (recount {expected})"),
        ("every PLACES state is a known state", in_dim_state == 0, f"{in_dim_state} unknown"),
        ("prevalence between 0 and 100 percent", outside == 0, f"{outside} outside"),
    ]


def _check_nucc(engine: Engine, raw_root: Path) -> list[Check]:
    path = next((raw_root / "NUCC Taxonomy").glob("*.csv"))
    expected = sum(1 for _ in _rows(path))
    got = _count(engine, "SELECT count(*) FROM src_nucc_taxonomy")
    bridge = _count(engine, "SELECT count(*) FROM bridge_taxonomy_specialty")
    return [
        ("taxonomy codes equal a recount", got == expected, f"{got} (recount {expected})"),
        ("one bridge row per taxonomy code", bridge == got, f"{bridge} bridge rows, {got} codes"),
    ]


def _check_openpay(engine: Engine, raw_root: Path) -> list[Check]:
    profile = _profile("openpayments")
    reconcile = _profile("openpayments_reconciliation_2025")
    with engine.connect() as conn:
        runs = conn.execute(
            text(
                "SELECT data_year, note FROM external_load_runs "
                "WHERE source = 'openpay' AND status = 'ok' ORDER BY run_id"
            )
        ).all()
    latest = {year: json.loads(note) for year, note in runs}
    read = {year: stats["rows_read"] for year, stats in latest.items()}
    expected = {int(y): v["n_rows"] for y, v in profile.items()}
    unmapped = {
        " ".join(name.upper().split())  # the source has doubled spaces inside names
        for stats in latest.values()
        for name in stats["unmapped_names"]
    }
    month_sum = _scalar(
        engine, "SELECT sum(n_records), sum(total_amount_usd) FROM fact_ext_openpay_month"
    )
    nature_sum = _scalar(
        engine, "SELECT sum(n_records), sum(total_amount_usd) FROM fact_ext_openpay_nature"
    )
    outside = _count(
        engine,
        "SELECT count(*) FROM fact_ext_openpay_month "
        "WHERE month_id < 201901 OR month_id > 202512 OR month_id % 100 NOT BETWEEN 1 AND 12",
    )
    years_with_zilretta = _count(
        engine,
        "SELECT count(DISTINCT month_id / 100) FROM fact_ext_openpay_month "
        "WHERE product = 'Zilretta'",
    )
    return [
        _compare("Open Payments rows read per year equal the profile", expected, read),
        (
            "2025 Zilretta records equal the independent count (3,275)",
            latest.get(2025, {}).get("zilretta_records") == reconcile["zilretta_rows_seen"],
            f"{latest.get(2025, {}).get('zilretta_records')} "
            f"(profile {reconcile['zilretta_rows_seen']})",
        ),
        (
            "the only hyaluronic name matching no approved product is the generic bucket",
            unmapped <= {"BIOLOGICS CONSUMABLES HYALURONIC ACID OTHER"},
            ", ".join(sorted(unmapped)) or "none",
        ),
        (
            "month and nature tables agree on records and dollars",
            month_sum is not None
            and month_sum[0] == nature_sum[0]
            and abs(month_sum[1] - nature_sum[1]) < 0.01,
            f"{month_sum[0]} records, ${month_sum[1]:,.2f}" if month_sum else "empty",
        ),
        ("every month falls in 2019 to 2025", outside == 0, f"{outside} outside"),
        (
            "Zilretta has payment records in all 7 years",
            years_with_zilretta == 7,
            f"{years_with_zilretta} years",
        ),
    ]


_PROVIDER_CHECKS = {
    "openpay": _check_openpay,
    "nucc": _check_nucc,
    "places": _check_places,
    "partb_provider": _check_partb_provider,
    "nppes": _check_nppes,
}


def verify_provider_sources(
    engine: Engine, raw_root: Path, sources: list[str] | None = None
) -> list[Check]:
    checks: list[Check] = []
    for source in sources if sources is not None else list(_PROVIDER_CHECKS):
        checks += _PROVIDER_CHECKS[source](engine, Path(raw_root))
    return checks
