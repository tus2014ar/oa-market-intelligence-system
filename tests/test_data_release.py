"""Tests for releasing a warehouse to S3 and checking one before use (src/.../data_release.py,
DL-124): the pointer is built from the file, a pointer can name only one fixed shape of key, and a
downloaded file is accepted only if it is exactly what the pointer promises."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from oa_market_intelligence import data_release as dr
from oa_market_intelligence.data_release import ReleaseError
from oa_market_intelligence.serving import data

VERSION = "28b9a7bd3234"
NOW = datetime(2026, 11, 7, 3, 0, 0, tzinfo=UTC)


def make_db(path, *, version=VERSION, month=202507, tables=dr.REQUIRED_TABLES, extra_versions=()):
    """A tiny warehouse with the three tables the API serves from."""
    con = sqlite3.connect(path)
    for table in tables:
        if table == "pred_batch":
            con.execute("CREATE TABLE pred_batch (run_month_id INT, data_version TEXT, x REAL)")
            con.execute("INSERT INTO pred_batch VALUES (?, ?, 1.0)", (month, version))
            for other in extra_versions:
                con.execute("INSERT INTO pred_batch VALUES (?, ?, 2.0)", (month, other))
        else:
            con.execute(f"CREATE TABLE {table} (a INT)")
            con.execute(f"INSERT INTO {table} VALUES (1)")
    con.commit()
    con.close()
    return path


def make_log(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def db(tmp_path):
    return make_db(tmp_path / "warehouse.db")


@pytest.fixture()
def log(tmp_path):
    return make_log(tmp_path / "run_log.jsonl", [{"status": "old"}, {"status": "ok", "n": 7}])


# ------------------------------------------------------------------ building the pointer


def test_the_pointer_describes_the_file_and_carries_the_last_run_record(db, log):
    pointer = dr.build_pointer(db, log, now=NOW)
    assert pointer["schema"] == 1 and pointer["data_version"] == VERSION
    assert pointer["run_month_id"] == 202507 and pointer["created_at"] == "2026-11-07T03:00:00Z"
    assert pointer["size"] == db.stat().st_size and pointer["sha256"] == dr.sha256_file(db)
    assert pointer["key"] == f"published/{VERSION}-{pointer['sha256'][:12]}/warehouse.db"
    assert pointer["run"] == {"status": "ok", "n": 7}  # the last line, not the first


def test_the_same_file_always_gets_the_same_key_and_a_changed_file_a_new_one(db, log, tmp_path):
    first = dr.build_pointer(db, log, now=NOW)
    again = dr.build_pointer(db, log, now=NOW)
    assert first == again
    other = make_db(tmp_path / "other.db", month=202508)  # same version, different bytes
    assert dr.build_pointer(other, log)["key"] != first["key"]


def test_a_missing_run_log_gives_an_empty_run_record(db, tmp_path):
    assert dr.build_pointer(db, tmp_path / "nope.jsonl", now=NOW)["run"] == {}
    assert dr.build_pointer(db, make_log(tmp_path / "e.jsonl", []), now=NOW)["run"] == {}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tables": ("pred_batch", "pred_inputs")}, "lacks the table"),
        ({"extra_versions": ("aaaaaaaaaaaa",)}, "exactly one data version"),
        ({"version": "UPPERCASE123"}, "exactly one data version"),
        ({"version": "short"}, "exactly one data version"),
    ],
)
def test_a_warehouse_the_api_could_not_serve_from_is_refused(tmp_path, log, kwargs, message):
    bad = make_db(tmp_path / "bad.db", **kwargs)
    with pytest.raises(ReleaseError, match=message):
        dr.build_pointer(bad, log)


def test_a_file_that_is_not_a_database_or_is_damaged_is_refused(tmp_path, log):
    text = tmp_path / "text.db"
    text.write_text("this is not a database", encoding="utf-8")
    with pytest.raises(ReleaseError):
        dr.build_pointer(text, log)
    with pytest.raises(ReleaseError):
        dr.build_pointer(tmp_path / "missing.db", log)
    empty = make_db(tmp_path / "empty.db")
    con = sqlite3.connect(empty)
    con.execute("DELETE FROM pred_batch")
    con.commit()
    con.close()
    with pytest.raises(ReleaseError, match="exactly one data version"):
        dr.build_pointer(empty, log)


# ------------------------------------------------------------------ what a pointer may say


def good(db, log):
    return dr.build_pointer(db, log, now=NOW)


def test_a_valid_pointer_passes_unchanged(db, log):
    pointer = good(db, log)
    assert dr.validate_pointer(pointer) is pointer


@pytest.mark.parametrize(
    "key",
    [
        "latest.json",
        "published/../../etc/passwd",
        "../published/aaaaaaaaaaaa-bbbbbbbbbbbb/warehouse.db",
        "published/aaaaaaaaaaaa-bbbbbbbbbbbb/warehouse.db/../x",
        "published/AAAAAAAAAAAA-BBBBBBBBBBBB/warehouse.db",
        "published/aaaaaaaaaaaa-bbbbbbbbbbbb/other.db",
        "other/aaaaaaaaaaaa-bbbbbbbbbbbb/warehouse.db",
        "published/aaaaaaaaaaaa-bbbbbbbbbbbb/warehouse.db\n",
        "s3://bucket/published/aaaaaaaaaaaa-bbbbbbbbbbbb/warehouse.db",
        "",
        None,
        123,
    ],
)
def test_a_pointer_can_name_only_one_shape_of_key(db, log, key):
    pointer = {**good(db, log), "key": key}
    with pytest.raises(ReleaseError):
        dr.validate_pointer(pointer)


def test_the_key_must_match_the_version_and_the_checksum_it_claims(db, log):
    pointer = good(db, log)
    other_version = {**pointer, "data_version": "aaaaaaaaaaaa"}
    with pytest.raises(ReleaseError, match="does not match"):
        dr.validate_pointer(other_version)
    other_sha = {**pointer, "sha256": "f" * 64}
    with pytest.raises(ReleaseError, match="does not match"):
        dr.validate_pointer(other_sha)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", 2), ("schema", None), ("sha256", "xyz"), ("sha256", "A" * 64),
        ("size", 0), ("size", -5), ("size", True), ("size", "100"),
        ("size", dr.MAX_DATABASE_BYTES + 1), ("run_month_id", 20250), ("run_month_id", "202507"),
        ("run_month_id", True), ("created_at", None), ("run", []), ("run", "text"),
        ("data_version", "NOTHEX123456"), ("data_version", None),
    ],
)  # fmt: skip
def test_a_pointer_with_a_bad_field_is_refused(db, log, field, value):
    with pytest.raises(ReleaseError):
        dr.validate_pointer({**good(db, log), field: value})


def test_a_pointer_that_is_not_an_object_or_has_a_huge_run_record_is_refused(db, log):
    for bad in (None, [], "text", 7):
        with pytest.raises(ReleaseError, match="not an object"):
            dr.validate_pointer(bad)
    huge = {**good(db, log), "run": {"x": "y" * dr.MAX_POINTER_BYTES}}
    with pytest.raises(ReleaseError, match="too large"):
        dr.validate_pointer(huge)


# ------------------------------------------------------------------ checking a downloaded file


def test_the_exact_file_passes_verification(db, log):
    dr.verify_database(db, good(db, log))


def test_a_file_of_the_wrong_size_or_missing_fails(db, log, tmp_path):
    pointer = good(db, log)
    truncated = tmp_path / "t.db"
    truncated.write_bytes(db.read_bytes()[:-10])
    with pytest.raises(ReleaseError, match="size"):
        dr.verify_database(truncated, pointer)
    with pytest.raises(ReleaseError, match="size"):
        dr.verify_database(tmp_path / "gone.db", pointer)


def test_a_file_of_the_right_size_but_other_bytes_fails_the_checksum(db, log, tmp_path):
    pointer = good(db, log)
    tampered = bytearray(db.read_bytes())
    tampered[-1] ^= 0xFF  # one changed byte, same size
    path = tmp_path / "tampered.db"
    path.write_bytes(bytes(tampered))
    with pytest.raises(ReleaseError, match="checksum"):
        dr.verify_database(path, pointer)


def test_a_file_with_the_pointers_checksum_but_another_version_or_month_fails(tmp_path, log):
    base = make_db(tmp_path / "a.db")
    pointer = dr.build_pointer(base, log, now=NOW)
    # a pointer that claims a different version or month than the (genuine) file holds
    lying_version = {**pointer, "data_version": "aaaaaaaaaaaa"}
    lying_version["key"] = f"published/aaaaaaaaaaaa-{pointer['sha256'][:12]}/warehouse.db"
    with pytest.raises(ReleaseError, match="different data version"):
        dr.verify_database(base, dr.validate_pointer(lying_version))
    with pytest.raises(ReleaseError, match="different month"):
        dr.verify_database(base, {**pointer, "run_month_id": 202508})


# ------------------------------------------------------------------ the command


def test_prepare_writes_a_valid_pointer_file_and_prints_what_the_workflow_needs(
    db, log, tmp_path, capsys
):
    out = tmp_path / "release"
    assert dr.main(["prepare", str(db), str(log), str(out)]) == 0
    written = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    dr.validate_pointer(written)
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert lines["key"] == written["key"] and lines["size"] == str(written["size"])
    assert lines["data_version"] == VERSION


def test_prepare_refuses_a_bad_warehouse_with_a_message_and_a_failing_status(tmp_path, log, capsys):
    bad = make_db(tmp_path / "bad.db", tables=("pred_batch",))
    assert dr.main(["prepare", str(bad), str(log), str(tmp_path / "o")]) == 1
    assert "release refused" in capsys.readouterr().err
    assert not (tmp_path / "o" / "latest.json").exists()


# ------------------------------------------------------------------ the real published warehouse


@pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")
def test_the_published_warehouse_makes_a_valid_pointer_that_verifies_against_itself():
    pointer = dr.build_pointer(data.DB_PATH, data.RUN_LOG, now=NOW)
    dr.verify_database(data.DB_PATH, pointer)
    assert pointer["data_version"] == "28b9a7bd3234" and pointer["run_month_id"] == 202507
    assert pointer["run"]["status"] == "ok" and len(json.dumps(pointer)) < dr.MAX_POINTER_BYTES
