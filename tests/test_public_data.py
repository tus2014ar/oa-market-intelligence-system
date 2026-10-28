"""Tests for the public-data stage (src/.../public_data.py): the committed subset is checked against
its own record and the fingerprint, and the full mode reports a refresh without failing on it."""

import json
import sqlite3

import pytest

from oa_market_intelligence.external.export import DEFAULT_SUBSET
from oa_market_intelligence.public_data import (
    FINGERPRINT_JSON,
    MANIFEST_JSON,
    PublicDataError,
    check_manifest,
    compare_fingerprints,
    rebuild_full,
    subset_fingerprint,
    verify_committed,
    write_fingerprint,
)

TABLES = ("dim_a", "fact_b")
GOOD_SHA = "a" * 64


def _make_subset(path, *, extra_row=False):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE dim_a (k INTEGER, name TEXT)")
    con.execute("CREATE TABLE fact_b (k INTEGER, value REAL)")
    con.executemany("INSERT INTO dim_a VALUES (?, ?)", [(1, "x"), (2, "y")])
    con.executemany("INSERT INTO fact_b VALUES (?, ?)", [(1, 1.5), (2, 2.5), (2, 3.5)])
    if extra_row:
        con.execute("INSERT INTO fact_b VALUES (9, 9.5)")
    con.execute(
        "CREATE TABLE external_subset_meta (table_name TEXT, n_rows INTEGER, exported_at TEXT)"
    )
    con.executemany(
        "INSERT INTO external_subset_meta VALUES (?, ?, 'now')",
        [("dim_a", 2), ("fact_b", 4 if extra_row else 3)],
    )
    con.commit()
    con.close()


def _manifest(path, rows=None):
    default = [{"file": "f1.zip", "sha256": GOOD_SHA, "status": "downloaded"}]
    rows = rows if rows is not None else default
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")


@pytest.fixture
def files(tmp_path):
    subset, fingerprint = tmp_path / "subset.db", tmp_path / "fp.json"
    manifest = tmp_path / "m.jsonl"
    _make_subset(subset)
    write_fingerprint(subset, fingerprint, TABLES)
    _manifest(manifest)
    return subset, fingerprint, manifest


def _verify(files, **kwargs):
    subset, fingerprint, manifest = files
    return verify_committed(subset, fingerprint, manifest, TABLES, **kwargs)


def test_a_matching_subset_passes_and_is_summarised(files):
    out = _verify(files)
    assert out == {"mode": "committed", "tables": 2, "rows": 5, "manifest_files": 1}


def test_the_fingerprint_is_stable_and_row_order_does_not_matter(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _make_subset(a)
    _make_subset(b)
    con = sqlite3.connect(b)
    con.execute("DELETE FROM fact_b")
    con.executemany("INSERT INTO fact_b VALUES (?, ?)", [(2, 3.5), (1, 1.5), (2, 2.5)])
    con.commit()
    con.close()
    assert subset_fingerprint(a, TABLES) == subset_fingerprint(b, TABLES)
    assert subset_fingerprint(a, TABLES)["tables"]["fact_b"]["rows"] == 3


def test_a_changed_value_is_caught_by_the_fingerprint(files):
    subset, _, _ = files
    con = sqlite3.connect(subset)
    con.execute("UPDATE fact_b SET value = 99 WHERE k = 1")
    con.commit()
    con.close()
    with pytest.raises(PublicDataError, match="fact_b: content differs"):
        _verify(files)


def test_a_table_that_disagrees_with_the_files_own_record_is_caught(files):
    subset, fingerprint, _ = files
    con = sqlite3.connect(subset)
    con.execute("INSERT INTO fact_b VALUES (9, 9.5)")  # a row the meta table does not know about
    con.commit()
    con.close()
    with pytest.raises(PublicDataError) as error:
        _verify(files)
    assert "external_subset_meta says 3 rows, the table has 4" in str(error.value)
    assert "fact_b: rows 3 -> 4" in str(error.value)  # also differs from the fingerprint


def test_a_missing_table_or_file_is_an_error(tmp_path, files):
    subset, fingerprint, manifest = files
    con = sqlite3.connect(subset)
    con.execute("DROP TABLE dim_a")
    con.commit()
    con.close()
    with pytest.raises(PublicDataError, match="lacks tables: dim_a"):
        _verify(files)
    with pytest.raises(PublicDataError, match="missing"):
        verify_committed(tmp_path / "nope.db", fingerprint, manifest, TABLES)


def test_the_manifest_must_record_a_sha256_for_every_file(tmp_path):
    good = tmp_path / "good.jsonl"
    _manifest(good, [{"file": "a", "sha256": GOOD_SHA}, {"file": "b", "sha256": "b" * 64}])
    assert check_manifest(good) == 2
    bad = tmp_path / "bad.jsonl"
    _manifest(bad, [{"file": "a", "sha256": GOOD_SHA}, {"file": "b", "sha256": "xyz"}])
    with pytest.raises(PublicDataError, match="1 manifest rows lack"):
        check_manifest(bad)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(PublicDataError, match="empty"):
        check_manifest(empty)


def test_compare_fingerprints_names_what_differs():
    a = {"tables": {"t": {"rows": 1, "sha256": "1"}, "only_a": {"rows": 1, "sha256": "1"}}}
    b = {"tables": {"t": {"rows": 2, "sha256": "2"}, "only_b": {"rows": 1, "sha256": "1"}}}
    lines = compare_fingerprints(a, b)
    assert "t: rows 2 -> 1" in lines and "t: content differs (hash changed)" in lines
    assert any(line.startswith("only_a") for line in lines)
    assert any(line.startswith("only_b") for line in lines)
    assert compare_fingerprints(a, a) == []


def test_the_full_mode_reports_an_unchanged_or_a_refreshed_subset(tmp_path, files):
    subset, fingerprint, manifest = files
    kwargs = dict(
        subset_db=subset, fingerprint_json=fingerprint, manifest_json=manifest, tables=TABLES
    )
    same = rebuild_full(load_fn=lambda: 0, export_fn=lambda: {"dim_a": 2, "fact_b": 3}, **kwargs)
    assert same["mode"] == "full" and same["fingerprint_changed"] is False and same["rows"] == 5

    def refresh():
        _make_subset_again = sqlite3.connect(subset)
        _make_subset_again.execute("INSERT INTO fact_b VALUES (7, 7.5)")
        _make_subset_again.commit()
        _make_subset_again.close()
        return {"dim_a": 2, "fact_b": 4}

    changed = rebuild_full(load_fn=lambda: 0, export_fn=refresh, **kwargs)
    assert changed["fingerprint_changed"] is True
    assert any("fact_b" in line for line in changed["differences"])  # reported, not an error


def test_the_full_mode_stops_if_a_reconciliation_check_fails(files):
    subset, fingerprint, manifest = files
    exported = []
    with pytest.raises(PublicDataError, match="reconciliation"):
        rebuild_full(
            load_fn=lambda: 1,  # the loaders return 1 when a check fails
            export_fn=lambda: exported.append(1) or {},
            subset_db=subset, fingerprint_json=fingerprint, manifest_json=manifest, tables=TABLES,
        )
    assert not exported  # nothing is exported after a failed check


def test_the_real_committed_subset_matches_its_committed_fingerprint():
    assert DEFAULT_SUBSET.exists() and FINGERPRINT_JSON.exists() and MANIFEST_JSON.exists()
    out = verify_committed()
    assert out["tables"] == 23 and out["rows"] == 62339 and out["manifest_files"] == 171
