"""Tests for the load-test tooling (loadtest/summarise.py, DL-127).

The results of a load test are only as good as the table and the budget check made from them, so
those are tested on made-up Locust CSV files. (The load itself is run by hand, see
docs/loadtest.md; it needs a running service.)"""

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("summarise", ROOT / "loadtest" / "summarise.py")
summarise = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarise)

COLUMNS = ["Name", "Request Count", "Failure Count", "50%", "95%", "99%", "Requests/s"]


def write_stats(tmp_path, name, rows):
    path = tmp_path / f"{name}_stats.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    return str(tmp_path / name)


def run(tmp_path, name, total, endpoints=()):
    return write_stats(tmp_path, name, [*endpoints, ("Aggregated", *total)])


def test_the_table_lists_each_endpoint_busiest_first_then_the_whole_run(tmp_path):
    prefix = run(
        tmp_path, "r",
        (1000, 3, 20, 80, 120, 25.5),
        [("GET /a", 100, 0, 10, 30, 40, 2.0), ("GET /b", 900, 3, 22, 90, 130, 23.5)],
    )  # fmt: skip
    lines = summarise.table(prefix, summarise.read_stats(prefix))
    names = [
        line.split("|")[1].strip()
        for line in lines
        if line.startswith("| GET") or "Aggregated" in line
    ]
    assert names == ["GET /b", "GET /a", "Aggregated"]
    assert "| 1000 | 3 | 20 | 80 | 120 | 25.5 |" in "\n".join(lines)


def test_a_run_inside_the_budgets_passes(tmp_path):
    prefix = run(tmp_path, "good", (1000, 5, 20, 480, 900, 30.0))  # 0.5% failed, p95 480 ms
    verdict, ok = summarise.check(prefix, summarise.read_stats(prefix))
    assert ok and all(line.startswith("PASS") for line in verdict)


def test_a_slow_run_fails_on_p95_and_names_the_numbers(tmp_path):
    prefix = run(tmp_path, "slow", (1000, 0, 100, 501, 900, 30.0))
    verdict, ok = summarise.check(prefix, summarise.read_stats(prefix))
    assert not ok
    assert verdict[0] == "FAIL slow: p95 501 ms against a budget of 500"
    assert verdict[1].startswith("PASS")


def test_a_run_with_too_many_failures_fails_and_so_does_a_run_with_no_requests(tmp_path):
    flaky = run(tmp_path, "flaky", (1000, 11, 20, 100, 200, 30.0))  # 1.1% failed
    verdict, ok = summarise.check(flaky, summarise.read_stats(flaky))
    assert not ok and verdict[1].startswith("FAIL") and "1.10%" in verdict[1]
    empty = run(tmp_path, "empty", (0, 0, 0, 0, 0, 0.0))
    _, ok_empty = summarise.check(empty, summarise.read_stats(empty))
    assert not ok_empty  # a run that made no requests proves nothing


def test_the_command_exits_nonzero_if_any_run_misses_a_budget_and_prints_every_table(
    tmp_path, capsys
):
    good = run(tmp_path, "good", (100, 0, 20, 80, 120, 10.0))
    slow = run(tmp_path, "slow", (100, 0, 20, 900, 1200, 10.0))
    assert summarise.main([good]) == 0
    assert summarise.main([good, slow]) == 1
    out = capsys.readouterr().out
    assert "### good" in out and "### slow" in out and "FAIL slow" in out


def test_a_missing_result_file_stops_with_a_clear_message(tmp_path):
    with pytest.raises(SystemExit) as stop:
        summarise.read_stats(str(tmp_path / "nothing"))
    assert "no results at" in str(stop.value)
    assert summarise.main([]) == 2


def test_the_budgets_are_the_documented_ones():
    assert summarise.BUDGET_P95_MS == 500.0 and summarise.BUDGET_FAILURE_RATE == 0.01
    text = (ROOT / "loadtest" / "locustfile.py").read_text(encoding="utf-8")
    assert "LOAD_ALLOW_REMOTE" in text and "LOAD_API_KEY" in text
    assert "print(" not in text  # the key is read from the environment and never printed
