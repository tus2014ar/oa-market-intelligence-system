"""Tests for the job-summary text of the latest publish run."""

import json

from oa_market_intelligence.run_summary import summarise


def _log(tmp_path, *records):
    path = tmp_path / "run_log.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_a_good_run_reports_months_and_status(tmp_path):
    text = summarise(
        _log(
            tmp_path,
            {
                "status": "ok",
                "n_months": 72,
                "last_month_id": 202507,
                "serving": "seasonal",
                "monitoring_status": "ok",
                "unmapped_products": [],
            },
        )
    )
    assert "Publish: ok" in text and "72" in text and "202507" in text
    assert "taxonomy" not in text


def test_unmapped_products_are_listed_with_what_to_do(tmp_path):
    text = summarise(
        _log(
            tmp_path,
            {
                "status": "ok",
                "n_months": 73,
                "last_month_id": 202508,
                "unmapped_products": ["NEWDRUG A", "NEWDRUG B"],
            },
        )
    )
    assert "NEWDRUG A, NEWDRUG B" in text and "product_taxonomy.csv" in text


def test_a_failed_run_shows_the_error_and_only_the_last_run_counts(tmp_path):
    text = summarise(
        _log(
            tmp_path,
            {"status": "ok", "n_months": 72, "last_month_id": 202507},
            {"status": "failed", "error": "PivotStructureError: header changed"},
        )
    )
    assert "Publish: failed" in text and "PivotStructureError: header changed" in text
    assert "202507" not in text


def test_a_missing_or_empty_log_is_said_plainly(tmp_path):
    assert summarise(tmp_path / "nope.jsonl") == "No run log was written."
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    assert summarise(empty) == "No run log was written."
