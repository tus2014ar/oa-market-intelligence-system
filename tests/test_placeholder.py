"""Placeholder test so the CI pipeline has something real to run and pass.

Replace/remove once real modules land under src/oa_market_intelligence/
(ingestion, features, models, etc. — see docs/PROPOSAL.md §11).
"""

from oa_market_intelligence import __version__


def test_package_importable():
    assert __version__ == "0.0.0"
