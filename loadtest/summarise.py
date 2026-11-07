"""Turn Locust's CSV results into a table and check them against budgets (DL-127).

    python loadtest/summarise.py results/local_u5 results/local_u20 ...   (the --csv prefixes)

For each run it prints the requests, the failures, the median, 95th and 99th percentile response
time and the requests per second, for the whole run and for every endpoint. Then it checks the
whole-run row of each run against the budgets below and prints PASS or FAIL for each. Standard
library only. The budgets are the ones in docs/loadtest.md.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BUDGET_P95_MS = 500.0  # a warm request should answer within half a second 19 times in 20
BUDGET_FAILURE_RATE = 0.01  # at most 1% failed


def read_stats(prefix: str) -> list[dict]:
    path = Path(f"{prefix}_stats.csv")
    if not path.exists():
        raise SystemExit(f"no results at {path}")
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(row: dict, column: str) -> float:
    try:
        return float(row[column])
    except (KeyError, ValueError):
        return float("nan")


def table(prefix: str, rows: list[dict]) -> list[str]:
    out = [
        f"### {Path(prefix).name}",
        "",
        "| Endpoint | Requests | Failures | Median ms | p95 ms | p99 ms | Requests/s |",
        "|---|---|---|---|---|---|---|",
    ]
    ordered = sorted(rows, key=lambda r: (r["Name"] == "Aggregated", -number(r, "Request Count")))
    for r in ordered:
        count, failed = int(number(r, "Request Count")), int(number(r, "Failure Count"))
        out.append(
            f"| {r['Name']} | {count} | {failed} "
            f"| {number(r, '50%'):.0f} | {number(r, '95%'):.0f} | {number(r, '99%'):.0f} "
            f"| {number(r, 'Requests/s'):.1f} |"
        )
    return out


def check(prefix: str, rows: list[dict]) -> tuple[list[str], bool]:
    total = next(r for r in rows if r["Name"] == "Aggregated")
    requests, failures = number(total, "Request Count"), number(total, "Failure Count")
    rate = failures / requests if requests else 1.0
    p95 = number(total, "95%")
    name = Path(prefix).name
    results = [
        (p95 <= BUDGET_P95_MS, f"{name}: p95 {p95:.0f} ms against a budget of {BUDGET_P95_MS:.0f}"),
        (
            rate <= BUDGET_FAILURE_RATE,
            f"{name}: failure rate {100 * rate:.2f}% against a budget of "
            f"{100 * BUDGET_FAILURE_RATE:.0f}%",
        ),
    ]
    return [("PASS " if ok else "FAIL ") + text for ok, text in results], all(
        ok for ok, _ in results
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    lines, ok = [], True
    for prefix in argv:
        rows = read_stats(prefix)
        lines += table(prefix, rows) + [""]
        verdict, passed = check(prefix, rows)
        lines += verdict + [""]
        ok = ok and passed
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
