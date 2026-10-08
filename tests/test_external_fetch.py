"""Tests for the raw-data restore tool (`external.fetch`) and the committed manifest.

Downloads are faked with an in-memory opener, so nothing touches the network.
"""

import hashlib
import io
import json
import re

import pytest

from oa_market_intelligence.external.common import REFERENCE_DIR
from oa_market_intelligence.external.fetch import (
    MANIFEST_NAME,
    classify,
    latest_entries,
    restore,
)

COMMITTED = REFERENCE_DIR / "external_manifest.jsonl"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entry(file, data=b"hello", url="https://www.cms.gov/files/zip/a.zip", **extra):
    base = {
        "dataset": "ASP Pricing Files",
        "year": 2021,
        "url": url,
        "file": file,
        "bytes": len(data),
        "sha256": sha(data),
        "status": "downloaded",
    }
    return {**base, **extra}


class FakeResponse(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeOpener:
    """Serves bytes by URL and records the requests made."""

    def __init__(self, served=None, fail=()):
        self.served = served or {}
        self.fail = set(fail)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url
        if url in self.fail or url not in self.served:
            raise OSError(f"cannot fetch {url}")
        return FakeResponse(self.served[url])


def write_manifest(root, entries):
    root.mkdir(parents=True, exist_ok=True)
    path = root.parent / "committed.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def run(tmp_path, entries, opener=None, **kwargs):
    root = tmp_path / "raw"
    manifest = write_manifest(root, entries)
    return root, restore(
        root, manifest, opener=opener or FakeOpener(), sleep=lambda s: None, **kwargs
    )


# ---------- reading the manifest ----------


def test_the_last_entry_for_a_file_wins_and_slashes_are_normalised():
    first = entry("A\\x.zip", b"old")
    second = entry("A/x.zip", b"new")
    latest = latest_entries([first, second, entry("B/y.zip", b"y")])
    assert [e["file"] for e in latest] == ["A/x.zip", "B/y.zip"]
    assert latest[0]["sha256"] == sha(b"new")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "http"),
        ({"url": "https://www.sec.gov/Archives/edgar/x.htm"}, "sec"),
        ({"status": "downloaded manually by owner"}, "manual"),
        ({"url": "manual download via AMA licence page"}, "manual"),
        (
            {
                "dataset": "Medicare Physician & Other Practitioners - by Provider and Service "
                "(filtered API pull)",
                "status": "downloaded (filtered)",
                "url": "https://data.cms.gov/data-api/v1/dataset/x/data?filter[HCPCS_Cd]=<code>",
            },
            "api_pull",
        ),
        ({"status": "saved from fetch", "url": "https://www.cms.gov/files/document/a.pdf"}, "http"),
    ],
)
def test_entries_are_classified_by_how_they_can_be_restored(overrides, expected):
    assert classify(entry("F/x", **overrides)) == expected


# ---------- restoring ----------


def test_a_file_that_is_already_there_and_matches_is_not_downloaded(tmp_path):
    root = tmp_path / "raw"
    (root / "A").mkdir(parents=True)
    (root / "A" / "x.zip").write_bytes(b"hello")
    opener = FakeOpener()
    _, report = run(tmp_path, [entry("A/x.zip")], opener)
    assert report["ok"] == ["A/x.zip"] and opener.requests == []


def test_a_missing_file_is_downloaded_and_checked_against_the_manifest(tmp_path):
    opener = FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"hello"})
    root, report = run(tmp_path, [entry("A/x.zip")], opener)
    assert report["downloaded"] == ["A/x.zip"]
    assert (root / "A" / "x.zip").read_bytes() == b"hello"
    assert not list(root.rglob("*.part"))


def test_a_download_that_differs_from_the_manifest_is_kept_and_reported(tmp_path):
    opener = FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"revised!"})
    root, report = run(tmp_path, [entry("A/x.zip")], opener)
    assert report["differs"] == ["A/x.zip"] and report["downloaded"] == []
    assert (root / "A" / "x.zip").read_bytes() == b"revised!"  # kept, not deleted


def test_an_existing_file_that_differs_is_reported_and_not_overwritten(tmp_path):
    root = tmp_path / "raw"
    (root / "A").mkdir(parents=True)
    (root / "A" / "x.zip").write_bytes(b"other")
    opener = FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"hello"})
    _, report = run(tmp_path, [entry("A/x.zip")], opener)
    assert report["differs"] == ["A/x.zip"] and opener.requests == []
    assert (root / "A" / "x.zip").read_bytes() == b"other"


def test_a_failing_download_is_retried_then_reported_and_the_rest_continue(tmp_path):
    good = entry("B/y.zip", b"yy", url="https://www.cms.gov/files/zip/b.zip")
    opener = FakeOpener({"https://www.cms.gov/files/zip/b.zip": b"yy"})
    root, report = run(tmp_path, [entry("A/x.zip"), good], opener)
    assert report["failed"] == ["A/x.zip"] and report["downloaded"] == ["B/y.zip"]
    assert len([r for r in opener.requests if r.full_url.endswith("a.zip")]) == 3
    assert not list(root.rglob("*.part"))


def test_files_that_cannot_be_fetched_are_listed_with_how_to_get_them(tmp_path):
    manual = entry(
        "M/m.zip", url="manual download via AMA licence page", status="downloaded manually by owner"
    )
    api = entry(
        "P/p.csv",
        status="downloaded (filtered)",
        url="https://data.cms.gov/data-api/v1/dataset/x/data?filter[HCPCS_Cd]=<code>",
        dataset=(
            "Medicare Physician & Other Practitioners - by Provider and Service (filtered API pull)"
        ),
    )
    _, report = run(tmp_path, [manual, api])
    assert [m["file"] for m in report["manual"]] == ["M/m.zip"]
    assert report["api_pull"] == ["P/p.csv"]
    assert not report["complete"]


def test_sec_files_need_a_contact_and_use_it_in_the_user_agent(tmp_path):
    sec = entry("S/f.htm", url="https://www.sec.gov/Archives/edgar/data/1/f.htm")
    _, report = run(tmp_path, [sec])
    assert report["sec_skipped"] == ["S/f.htm"]
    opener = FakeOpener({sec["url"]: b"hello"})
    _, report = run(tmp_path / "again", [sec], opener, sec_contact="someone@example.org")
    assert report["downloaded"] == ["S/f.htm"]
    assert "someone@example.org" in opener.requests[0].get_header("User-agent")


def test_a_dry_run_changes_nothing(tmp_path):
    opener = FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"hello"})
    root, report = run(tmp_path, [entry("A/x.zip")], opener, dry_run=True)
    assert report["would_download"] == ["A/x.zip"] and opener.requests == []
    assert not (root / "A").exists() and not (root / MANIFEST_NAME).exists()


def test_the_manifest_is_copied_into_the_raw_folder_for_the_loaders(tmp_path):
    opener = FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"hello"})
    root, _ = run(tmp_path, [entry("A/x.zip")], opener)
    copied = (root / MANIFEST_NAME).read_text(encoding="utf-8")
    assert json.loads(copied.splitlines()[0])["file"] == "A/x.zip"


def test_an_existing_raw_manifest_is_not_overwritten(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / MANIFEST_NAME).write_text("keep me\n", encoding="utf-8")
    run(tmp_path, [entry("A/x.zip")], FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"hello"}))
    assert (root / MANIFEST_NAME).read_text(encoding="utf-8") == "keep me\n"


def test_only_restricts_the_datasets_considered(tmp_path):
    other = entry("B/y.zip", dataset="NPPES", url="https://download.cms.gov/nppes/y.zip")
    opener = FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"hello"})
    _, report = run(tmp_path, [entry("A/x.zip"), other], opener, only="ASP")
    assert report["downloaded"] == ["A/x.zip"] and len(opener.requests) == 1


def test_a_complete_restore_reports_complete(tmp_path):
    opener = FakeOpener({"https://www.cms.gov/files/zip/a.zip": b"hello"})
    _, report = run(tmp_path, [entry("A/x.zip")], opener)
    assert report["complete"] is True


# ---------- the committed manifest ----------


def test_the_committed_manifest_is_complete_clean_and_free_of_contact_details():
    lines = [line for line in COMMITTED.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = [json.loads(line) for line in lines]
    assert len(entries) == 171
    files = [e["file"].replace("\\", "/") for e in entries]
    assert len(set(files)) == 171
    for e in entries:
        assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) and e["bytes"] > 0
    assert "@" not in COMMITTED.read_text(encoding="utf-8")  # no e-mail address anywhere
    kinds = {classify(e) for e in entries}
    assert kinds == {"http", "sec", "manual", "api_pull"}
