"""The data-quality stage (R3): dataset-level checks after validation and before anything is built.

The per-row schema validation (Pandera) rejects malformed rows. These checks look at the extract as
a whole and against what was seen before:

- **errors stop the run** (the last good database stays live): a gap in the months, or a disease
  area missing;
- **warnings are recorded** (the run goes on, a person looks): a category not seen before, a month's
  volume far outside the baseline range, place-of-service months that differ from the visit months,
  and history that changed since the previous published database.

The baseline (`data/reference/iqvia_baseline.json`) holds the categories and the monthly ranges of
the extracts we have profiled. Regenerate it on purpose with
`python -m oa_market_intelligence.quality --write-baseline`; a changed baseline is a visible diff.
Tolerances are in the baseline file: a month is flagged below 0.5 times the lowest or above 1.5
times the highest month seen, and history is flagged when a month's value moves by more than 0.5%.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, delete

from oa_market_intelligence.warehouse.schema import dq_report

BASELINE_JSON = Path(__file__).resolve().parents[2] / "data" / "reference" / "iqvia_baseline.json"
DEFAULT_TOLERANCES = {"low": 0.5, "high": 1.5, "restatement": 0.005}
DISEASE_AREAS = ("OA", "RA")
CATEGORY_CHECKS = (
    ("new_specialties", "specialty", "visits"),
    ("new_products", "product", "visits"),
    ("new_age_bands", "age_band", "visits"),
    ("new_genders", "gender", "visits"),
    ("new_place_of_service", "place_of_service", "pos"),
)
GOLD_COLUMNS = (
    "branded_injectable_visits",
    "generic_corticosteroid_visits",
    "nsaid_otc_visits",
)


class DataQualityError(ValueError):
    """One or more error-level quality checks failed; the run must stop."""


def month_ids(frame: pd.DataFrame) -> list[int]:
    stamps = pd.to_datetime(frame["month"])
    return sorted({int(v) for v in (stamps.dt.year * 100 + stamps.dt.month).unique()})


def _add_month(month: int) -> int:
    return month + 1 if month % 100 < 12 else (month // 100 + 1) * 100 + 1


def _row(check_id, severity, passed, observed, expected, note=""):
    return {
        "check_id": check_id,
        "severity": severity,
        "status": "pass" if passed else "fail",
        "observed": str(observed),
        "expected": str(expected),
        "note": note,
    }


def _skipped(check_id, severity, note):
    return {
        "check_id": check_id,
        "severity": severity,
        "status": "skipped",
        "observed": "",
        "expected": "",
        "note": note,
    }


# ---------------------------------------------------------------- the baseline


def _spread(series: pd.Series) -> dict:
    return {
        "min": int(series.min()),
        "median": float(series.median()),
        "max": int(series.max()),
    }


def build_baseline(
    visits: pd.DataFrame, place_of_service: pd.DataFrame, reference: pd.DataFrame
) -> dict:
    """The categories and monthly ranges of the extracts in hand (plain JSON)."""
    sets = {
        column: sorted(visits[column].dropna().astype(str).unique().tolist())
        for column in ("specialty", "product", "age_band", "gender", "manufacturer")
    }
    sets["disease_area"] = sorted(visits["disease_area"].unique().tolist())
    sets["place_of_service"] = sorted(place_of_service["place_of_service"].astype(str).unique())
    monthly, pos_monthly = {}, {}
    for area in sorted(visits["disease_area"].unique()):
        part = visits[visits["disease_area"] == area]
        key = pd.to_datetime(part["month"])
        grouped = part.groupby(key.dt.year * 100 + key.dt.month)
        monthly[area] = {
            "rows": _spread(grouped.size()),
            "visits_sum": _spread(grouped["patient_visits"].sum()),
        }
    for area in sorted(place_of_service["disease_area"].unique()):
        part = place_of_service[place_of_service["disease_area"] == area]
        key = pd.to_datetime(part["month"])
        grouped = part.groupby(key.dt.year * 100 + key.dt.month)
        pos_monthly[area] = {"visits_sum": _spread(grouped["patient_visits"].sum())}
    months = month_ids(visits)
    return {
        "note": "Baseline for the data-quality stage; regenerate on purpose (see quality.py).",
        "months": [months[0], months[-1]],
        "created_from": {
            "visit_rows": int(len(visits)),
            "place_of_service_rows": int(len(place_of_service)),
            "reference_rows": int(len(reference)),
        },
        "sets": sets,
        "monthly": monthly,
        "pos_monthly": pos_monthly,
        "tolerances": dict(DEFAULT_TOLERANCES),
    }


def load_baseline(path: Path | str = BASELINE_JSON) -> dict | None:
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------------------------------------------------------------- the checks


def _monthly_ranges(frame: pd.DataFrame, baseline: dict, kind: str, tolerances: dict) -> list[str]:
    """Months outside [low x lowest, high x highest] per area, for `rows` or `visits_sum`."""
    outside: list[str] = []
    for area, ranges in baseline["monthly"].items():
        part = frame[frame["disease_area"] == area]
        if part.empty:
            continue
        key = pd.to_datetime(part["month"])
        grouped = part.groupby(key.dt.year * 100 + key.dt.month)
        values = grouped.size() if kind == "rows" else grouped["patient_visits"].sum()
        low = ranges[kind]["min"] * tolerances["low"]
        high = ranges[kind]["max"] * tolerances["high"]
        outside += [f"{area} {int(m)}" for m, v in values.items() if v < low or v > high]
    return outside


def run_quality_checks(
    visits: pd.DataFrame,
    place_of_service: pd.DataFrame,
    reference: pd.DataFrame,
    baseline: dict | None = None,
) -> list[dict]:
    """Every pre-build check as a list of rows (check_id, severity, status, observed, expected,
    note).
    Without a baseline the baseline checks are `skipped`, never passed."""
    report: list[dict] = []

    months = month_ids(visits)
    missing, month = [], months[0]
    while month < months[-1]:
        month = _add_month(month)
        if month not in months:
            missing.append(month)
    report.append(
        _row(
            "month_continuity",
            "error",
            not missing,
            f"{months[0]} to {months[-1]}, {len(months)} months",
            "no month missing",
            f"missing months: {', '.join(map(str, missing))}" if missing else "",
        )
    )

    absent = [
        f"{name}: {area}"
        for name, frame in (("visits", visits), ("reference", reference))
        for area in DISEASE_AREAS
        if area not in set(frame["disease_area"])
    ]
    report.append(
        _row(
            "disease_areas_present",
            "error",
            not absent,
            ", ".join(sorted(set(visits["disease_area"]))),
            "OA and RA in the visits and in the reference table",
            f"missing: {'; '.join(absent)}" if absent else "",
        )
    )

    frames = {"visits": visits, "pos": place_of_service}
    for check_id, column, source in CATEGORY_CHECKS:
        if baseline is None:
            report.append(_skipped(check_id, "warning", "no baseline"))
            continue
        seen = set(frames[source][column].dropna().astype(str))
        new = sorted(seen - set(baseline["sets"][column]))
        shown = ", ".join(new[:10]) + (f" (and {len(new) - 10} more)" if len(new) > 10 else "")
        report.append(
            _row(
                check_id,
                "warning",
                not new,
                f"{len(new)} not in the baseline",
                "no new category",
                f"new: {shown}" if new else "",
            )
        )

    for check_id, kind, frame in (
        ("visits_per_month_in_range", "visits_sum", visits),
        ("rows_per_month_in_range", "rows", visits),
    ):
        if baseline is None:
            report.append(_skipped(check_id, "warning", "no baseline"))
            continue
        outside = _monthly_ranges(frame, baseline, kind, baseline["tolerances"])
        report.append(
            _row(
                check_id,
                "warning",
                not outside,
                f"{len(outside)} months outside",
                f"each month within {baseline['tolerances']['low']} x lowest and "
                f"{baseline['tolerances']['high']} x highest month seen",
                f"outside: {', '.join(outside[:12])}" if outside else "",
            )
        )

    visit_months, pos_months = set(months), set(month_ids(place_of_service))
    differ = sorted(visit_months ^ pos_months)
    report.append(
        _row(
            "pos_months_match_visit_months",
            "warning",
            not differ,
            f"{len(differ)} months differ",
            "the same months in the visit and place-of-service extracts",
            f"differ: {', '.join(map(str, differ[:12]))}" if differ else "",
        )
    )
    return report


def revision_check(
    previous: pd.DataFrame | None,
    current: pd.DataFrame,
    tolerance: float = DEFAULT_TOLERANCES["restatement"],
) -> dict:
    """Has the history of the monthly Gold table changed since the previous published database?
    Compares the months both have; new months are not a restatement."""
    if previous is None:
        return _skipped("history_restated", "warning", "no previous database")
    both = previous.merge(current, on="month_id", suffixes=("_old", "_new"))
    restated: list[str] = []
    for _, row in both.iterrows():
        for column in GOLD_COLUMNS:
            old, new = float(row[f"{column}_old"]), float(row[f"{column}_new"])
            if abs(new - old) / max(abs(old), 1.0) > tolerance:
                restated.append(f"{int(row['month_id'])} {column}")
    return _row(
        "history_restated",
        "warning",
        not restated,
        f"{len(restated)} month-values changed over {len(both)} common months",
        f"no month-value moves by more than {tolerance:.1%}",
        f"changed: {', '.join(restated[:12])}" if restated else "",
    )


# ---------------------------------------------------------------- policy, summary, storage


def raise_on_errors(report: list[dict]) -> None:
    failed = [r for r in report if r["severity"] == "error" and r["status"] == "fail"]
    if failed:
        raise DataQualityError(
            "data-quality errors: "
            + "; ".join(f"{r['check_id']} ({r['note'] or r['observed']})" for r in failed)
        )


def summarise_quality(report: list[dict]) -> dict:
    failed = [r for r in report if r["status"] == "fail"]
    return {
        "n_checks": len(report),
        "n_errors": sum(1 for r in failed if r["severity"] == "error"),
        "n_warnings": sum(1 for r in failed if r["severity"] == "warning"),
        "warnings": [
            {"check_id": r["check_id"], "note": r["note"]}
            for r in failed
            if r["severity"] == "warning"
        ],
    }


def refresh_dq_report(engine: Engine, report: list[dict]) -> int:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{**row, "checked_at": stamp} for row in report]
    with engine.begin() as conn:
        conn.execute(delete(dq_report))
        conn.execute(dq_report.insert(), rows)
    return len(rows)


# ---------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="parse the real extracts and write data/reference/iqvia_baseline.json",
    )
    args = parser.parse_args(argv)
    if not args.write_baseline:
        parser.print_help()
        return 0
    from oa_market_intelligence.pipeline import DEFAULT_RAW_DIR, ingest

    baseline = build_baseline(*ingest(DEFAULT_RAW_DIR))
    BASELINE_JSON.write_text(json.dumps(baseline, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {BASELINE_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
