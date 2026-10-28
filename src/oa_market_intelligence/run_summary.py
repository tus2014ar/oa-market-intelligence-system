"""A short markdown summary of the latest publish run, for the GitHub Actions job summary.

    python -m oa_market_intelligence.run_summary >> "$GITHUB_STEP_SUMMARY"

Reads the last line of `data/published/run_log.jsonl`. A product with no taxonomy entry does not
stop the run (it loads as "unclassified"), so the summary is where it becomes visible.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_LOG = Path("data/published/run_log.jsonl")


def summarise(log_path: Path | str = DEFAULT_LOG) -> str:
    path = Path(log_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.strip()]
    if not lines:
        return "No run log was written."
    run = json.loads(lines[-1])
    out = [f"**Publish: {run.get('status')}**"]
    if run.get("error"):
        out.append(f"Error: `{run['error']}`")
    if run.get("status") == "ok":
        out.append(f"Months: {run.get('n_months')}, latest month: {run.get('last_month_id')}")
        monitoring = run.get("monitoring") or {}
        if monitoring:
            fresh, drift = monitoring.get("freshness") or {}, monitoring.get("drift") or {}
            out.append(
                f"Monitoring: {monitoring.get('status')}; data freshness: {fresh.get('status')} "
                f"(latest month is {fresh.get('age_days')} days old, "
                f"limit {fresh.get('max_age_days')}); "
                f"forecast drift: {drift.get('status')}"
            )
        versions = run.get("versions") or {}
        if versions:
            out.append(
                f"Versions: data `{versions.get('data')}`, code `{versions.get('code')}`, "
                f"environment `{versions.get('environment')}`"
            )
    stages = run.get("stages") or []
    if stages:
        table = ["| Stage | Status | Seconds |", "|---|---|---|"]
        table += [f"| {s['stage']} | {s['status']} | {s['seconds']} |" for s in stages]
        out.append("\n".join(table))
        failed = [s["stage"] for s in stages if s["status"] == "failed"]
        if failed:
            out.append(f"**Failed stage:** `{failed[0]}`; the last good database is unchanged.")
    files = {}
    for item in run.get("ingest_files") or []:
        files.setdefault(item["file_name"], item["sha256"])
    if files:
        listing = ", ".join(f"{name} ({digest[:12]})" for name, digest in files.items())
        out.append(f"Input extracts (SHA-256 prefix): {listing}")
    quality = run.get("quality") or {}
    if quality:
        out.append(
            f"Data-quality checks: {quality.get('n_checks')} run, "
            f"{quality.get('n_errors')} errors, {quality.get('n_warnings')} warnings"
        )
        for warning in quality.get("warnings", []):
            out.append(f"Warning `{warning['check_id']}`: {warning['note']}")
    unmapped = run.get("unmapped_products") or []
    if unmapped:
        out.append(
            "**Products with no taxonomy entry (loaded as unclassified; add them to "
            "`data/reference/product_taxonomy.csv`):** " + ", ".join(unmapped)
        )
    return "\n\n".join(out)


def main() -> None:
    print(summarise())


if __name__ == "__main__":
    main()
