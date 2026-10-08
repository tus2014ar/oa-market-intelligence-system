"""Shared test fixtures."""

import hashlib
import os
import pickle
import sys
from pathlib import Path

import pytest
from filelock import FileLock
from sqlalchemy import create_engine

from tiny_gold import build_tiny_gold


@pytest.fixture
def tiny_gold_engine():
    """A tiny hand-built Gold database (see tests/test_serving_queries.py for the arithmetic)."""
    return build_tiny_gold(create_engine("sqlite:///:memory:"))


# ---------------------------------------------------------------- the real extracts, parsed once
# Parsing the real OA pivot workbook takes about a minute and a dozen tests need its output. It is
# parsed once per test session and shared (through a file, so every parallel worker uses the same
# result) instead of once per test file. The cache key covers the raw files and the parser source,
# so it can never serve output from older data or code.

_ROOT = Path(__file__).resolve().parent.parent
_PARSER_SOURCES = (
    "ingestion/nmta_loader.py",
    "ingestion/place_of_service_loader.py",
    "ingestion/reference_loader.py",
    "pipeline.py",
)


def _real_extracts_key() -> str:
    """Content-based, so it is the same on every checkout (CI can keep the cache between runs)."""
    import pandas

    digest = hashlib.sha1()
    digest.update(f"{sys.version_info[:2]}:{pandas.__version__}".encode())
    for path in sorted((_ROOT / "data" / "raw").glob("*.xlsx")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    for name in _PARSER_SOURCES:
        digest.update((_ROOT / "src" / "oa_market_intelligence" / name).read_bytes())
    return digest.hexdigest()[:16]


@pytest.fixture(scope="session")
def _real_ingest_cache(tmp_path_factory):
    from oa_market_intelligence.pipeline import DEFAULT_RAW_DIR, ingest

    # OA_TEST_CACHE_DIR lets CI keep the file between runs; otherwise it lives next to pytest's
    # temporary folders (shared by the parallel workers).
    configured = os.environ.get("OA_TEST_CACHE_DIR")
    folder = Path(configured) if configured else tmp_path_factory.getbasetemp().parent
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"real_ingest_{_real_extracts_key()}.pkl"
    with FileLock(str(path) + ".lock"):
        if path.exists():
            with open(path, "rb") as handle:
                return pickle.load(handle)
        frames = ingest(DEFAULT_RAW_DIR)  # the real function: (visits, place_of_service, reference)
        partial = path.with_suffix(".tmp")
        with open(partial, "wb") as handle:
            pickle.dump(frames, handle)
        partial.replace(path)
    return frames


@pytest.fixture(scope="session")
def real_ingest(_real_ingest_cache):
    """A function returning fresh copies of what `pipeline.ingest` returns for the real raw files:
    (visits, place_of_service, reference). Copies, so no test can change another's data."""

    def get():
        return tuple(frame.copy() for frame in _real_ingest_cache)

    return get


@pytest.fixture(scope="session")
def real_oa_visits(real_ingest):
    """The OA pivot as `parse_pivot_sheet` returns it (the OA rows of `ingest`)."""

    def get():
        visits = real_ingest()[0]
        return visits[visits["disease_area"] == "OA"].reset_index(drop=True)

    return get


@pytest.fixture(scope="session")
def real_ra_visits(real_ingest):
    def get():
        visits = real_ingest()[0]
        return visits[visits["disease_area"] == "RA"].reset_index(drop=True)

    return get
