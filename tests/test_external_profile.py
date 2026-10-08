"""Tests for the raw-data profiler (src/.../external/profile.py).

The profiler reads a file in chunks and reports data-quality facts only: row counts, empty values,
distinct values of chosen columns, numeric ranges, duplicate keys. It never computes outcomes.
"""

import json
import zipfile

import pandas as pd
import pytest

from oa_market_intelligence.external.profile import (
    iter_csv,
    iter_zip_csv,
    profile_chunks,
    sha256_of,
)


def _chunks(frame: pd.DataFrame, size: int):
    for start in range(0, len(frame), size):
        yield frame.iloc[start : start + size]


FRAME = pd.DataFrame(
    {
        "npi": ["1", "2", "3", "3", "4", "5"],
        "state": ["TX", "TX", "CA", "CA", "", None],
        "benes": ["11", "25", "-3", "0", "40", "x"],
        "date": ["2021-01-05", "2021-03-01", "2020-12-31", "2021-02-02", "", "2021-02-10"],
    }
)


def test_row_count_and_empty_values_do_not_depend_on_chunk_size():
    whole = profile_chunks(_chunks(FRAME, 6))
    split = profile_chunks(_chunks(FRAME, 2))
    assert whole["n_rows"] == split["n_rows"] == 6
    assert whole["columns"]["state"]["n_empty"] == split["columns"]["state"]["n_empty"] == 2
    assert whole["columns"]["npi"]["n_empty"] == 0


def test_tracked_columns_report_distinct_values_and_counts_across_chunks():
    out = profile_chunks(_chunks(FRAME, 2), track=("state",))
    state = out["columns"]["state"]
    assert state["n_distinct"] == 2
    assert state["top_values"] == {"TX": 2, "CA": 2}


def test_a_tracked_column_over_the_cap_reports_that_it_was_capped():
    wide = pd.DataFrame({"id": [str(i) for i in range(50)]})
    out = profile_chunks(_chunks(wide, 10), track=("id",), max_distinct=20)
    assert out["columns"]["id"]["capped"] is True
    assert out["columns"]["id"]["n_distinct"] >= 20


def test_numeric_columns_report_range_negatives_zeros_and_unparseable_values():
    out = profile_chunks(_chunks(FRAME, 3), numeric=("benes",))
    benes = out["columns"]["benes"]
    assert benes["min"] == -3 and benes["max"] == 40
    assert benes["n_negative"] == 1 and benes["n_zero"] == 1
    assert benes["n_unparseable"] == 1  # the value "x"


def test_date_columns_report_first_and_last_and_unparseable():
    out = profile_chunks(_chunks(FRAME, 2), dates=("date",))
    date = out["columns"]["date"]
    assert date["min"] == "2020-12-31" and date["max"] == "2021-03-01"
    assert date["n_unparseable"] == 0 and date["n_empty"] == 1


def test_duplicate_keys_are_counted_exactly():
    out = profile_chunks(_chunks(FRAME, 2), key=("npi", "state"))
    assert out["duplicate_keys"] == 1  # (3, CA) appears twice
    assert profile_chunks(_chunks(FRAME, 2), key=("npi",))["duplicate_keys"] == 1


def test_the_result_is_strictly_json_safe():
    out = profile_chunks(
        _chunks(FRAME, 2), track=("state",), numeric=("benes",), dates=("date",), key=("npi",)
    )
    json.dumps(out, allow_nan=False)


def test_a_missing_tracked_column_is_an_error_not_a_silent_skip():
    with pytest.raises(KeyError):
        profile_chunks(_chunks(FRAME, 3), track=("nope",))


def test_iter_csv_reads_everything_as_text_so_leading_zeros_survive(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("code,n\n00123,1\n20610,2\n", encoding="utf-8")
    rows = pd.concat(list(iter_csv(path, chunksize=1)))
    assert list(rows["code"]) == ["00123", "20610"]


def test_iter_csv_can_select_columns_and_filter_rows(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("code,n,other\nJ3304,1,a\nJ3301,2,b\nJ3304,3,c\n", encoding="utf-8")
    only_zilretta = lambda frame: frame["code"] == "J3304"  # noqa: E731
    rows = pd.concat(list(iter_csv(path, usecols=["code", "n"], chunksize=2, keep=only_zilretta)))
    assert list(rows.columns) == ["code", "n"] and list(rows["n"]) == ["1", "3"]


def test_iter_zip_csv_reads_one_member_without_extracting(tmp_path):
    zpath = tmp_path / "x.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("a_GNRL.csv", "name,amount\nZILRETTA,5\nOTHER,7\n")
        z.writestr("b_RSRCH.csv", "name,amount\nNO,1\n")
    rows = pd.concat(list(iter_zip_csv(zpath, member_contains="GNRL", chunksize=1)))
    assert list(rows["name"]) == ["ZILRETTA", "OTHER"]
    with pytest.raises(FileNotFoundError):
        list(iter_zip_csv(zpath, member_contains="MISSING"))


def test_sha256_matches_a_known_digest(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
