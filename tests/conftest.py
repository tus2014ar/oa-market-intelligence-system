"""Shared test fixtures."""

import pytest
from sqlalchemy import create_engine

from tiny_gold import build_tiny_gold


@pytest.fixture
def tiny_gold_engine():
    """A tiny hand-built Gold database (see tests/test_serving_queries.py for the arithmetic)."""
    return build_tiny_gold(create_engine("sqlite:///:memory:"))
