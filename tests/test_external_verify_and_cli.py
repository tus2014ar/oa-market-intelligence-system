"""Tests for the reconciliation checks and the command line of the external loaders."""

import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.__main__ import main, parse_args
from oa_market_intelligence.external.common import DEFAULT_RAW_ROOT
from oa_market_intelligence.external.loaders.reference import load_reference
from oa_market_intelligence.external.profile import sha256_of
from oa_market_intelligence.external.schema import create_external_schema
from oa_market_intelligence.external.verify import verify_reference


def _raw_root(tmp_path):
    data = tmp_path / "a.csv"
    data.write_text("abc", encoding="utf-8")
    entry = {
        "dataset": "D",
        "file": "a.csv",
        "url": "u",
        "bytes": 3,
        "sha256": sha256_of(data),
        "status": "downloaded",
        "at": "t",
    }
    (tmp_path / "_download_manifest.jsonl").write_text(json.dumps(entry), encoding="utf-8")
    descriptions = tmp_path / "d.csv"
    pd.DataFrame({"year": [2024], "code": ["J3304"], "description": ["x"]}).to_csv(
        descriptions, index=False
    )
    return tmp_path, descriptions


@pytest.fixture
def loaded(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    create_external_schema(engine)
    raw, descriptions = _raw_root(tmp_path)
    load_reference(engine, raw_root=raw, descriptions_csv=descriptions, product_names=["KENALOG"])
    return engine, raw


def test_a_correct_load_passes_every_reference_check(loaded):
    engine, raw = loaded
    results = verify_reference(engine, raw)
    assert results and all(ok for _, ok, _ in results), [r for r in results if not r[1]]


def test_a_changed_raw_file_fails_the_catalogue_check(loaded):
    engine, raw = loaded
    (raw / "a.csv").write_text("changed", encoding="utf-8")
    failing = [name for name, ok, _ in verify_reference(engine, raw) if not ok]
    assert failing == ["every catalogued file matches its manifest entry on disk"]


def test_a_missing_row_fails_its_check(loaded):
    engine, raw = loaded
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dim_hcpcs_code WHERE hcpcs_code = 'J3304'"))
    failing = [name for name, ok, _ in verify_reference(engine, raw) if not ok]
    assert "dim_hcpcs_code rows" in failing


def test_the_command_line_defaults_to_the_reference_source_and_the_local_paths():
    args = parse_args([])
    assert args.only == ["reference"] and args.verify is False
    assert args.raw == DEFAULT_RAW_ROOT and args.external_db.name == "external.db"
    assert parse_args(["--verify"]).verify is True


def test_the_command_line_loads_and_verifies_against_the_real_downloads_when_present(tmp_path):
    if not (DEFAULT_RAW_ROOT / "_download_manifest.jsonl").exists():
        pytest.skip("the raw downloads are not on this machine")
    code = main(["--external-db", str(tmp_path / "external.db"), "--verify"])
    assert code == 0
