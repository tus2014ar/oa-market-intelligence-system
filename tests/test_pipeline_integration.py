"""End-to-end tests chaining every Phase 2 stage together: raw files -> ingestion
loaders -> the Pandera validation gate -> the Silver builder -> the Gold builder.

This is a real gap the per-step test files don't close: test_validation.py exercises
each schema in isolation, and test_build_silver.py/test_build_gold.py both build
Silver/Gold directly from *unvalidated* loader output, skipping the validation gate
entirely. Until this file, nothing had ever actually run a loader's real output
*through* validate_nmta_visits/validate_place_of_service/validate_reference_table and
then fed the validated frame into build_silver - so nothing had confirmed the gate's
column dtypes and row order survive that handoff. It does: no rows are rejected, and
build_silver/build_gold behave identically on the validated frames as on the raw ones.

Also collects the key real-data figures independently confirmed throughout Steps 2-8
into one canonical regression check, so a future change to any loader or builder that
silently shifts one of these numbers is caught here even if it doesn't touch the
specific step's own test file.
"""

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.ingestion.nmta_loader import parse_pivot_sheet
from oa_market_intelligence.ingestion.place_of_service_loader import parse_place_of_service
from oa_market_intelligence.ingestion.reference_loader import parse_reference_table
from oa_market_intelligence.ingestion.validation import (
    validate_nmta_visits,
    validate_place_of_service,
    validate_reference_table,
)
from oa_market_intelligence.warehouse.build_gold import build_gold
from oa_market_intelligence.warehouse.build_silver import build_silver
from oa_market_intelligence.warehouse.schema import create_schema

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"


def _fake_fda(name: str):
    from datetime import date

    return date(2017, 10, 6) if name == "ZILRETTA" else None


@pytest.fixture(scope="module")
def raw_extracts():
    """Parsed straight from the real files - no validation applied yet."""
    oa_visits = parse_pivot_sheet(RAW / "Team1_M15_19_OA.xlsx")
    ra_visits = parse_pivot_sheet(RAW / "Team1_M04_RA.xlsx")
    visits = pd.concat([oa_visits, ra_visits], ignore_index=True)
    pos = pd.concat(
        [
            parse_place_of_service(RAW / "Team1_M15_19_OA.xlsx"),
            parse_place_of_service(RAW / "Team1_M04_RA.xlsx"),
        ],
        ignore_index=True,
    )
    reference = pd.concat(
        [
            parse_reference_table(RAW / "Branded Generic - OA.xlsx"),
            parse_reference_table(RAW / "Branded Generic - RA.xlsx"),
        ],
        ignore_index=True,
    )
    taxonomy = pd.read_csv(REFERENCE_DIR / "product_taxonomy.csv")
    return visits, reference, taxonomy, pos


def test_validation_gate_accepts_every_real_extract_without_dropping_rows(raw_extracts):
    visits, reference, taxonomy, pos = raw_extracts

    validated_visits = validate_nmta_visits(visits)
    validated_pos = validate_place_of_service(pos)
    validated_reference = validate_reference_table(reference)

    # The gate's job is to reject bad data, not reshape good data - every real row
    # must survive, none silently dropped or duplicated by the per-disease-area
    # split-and-concat in validate_nmta_visits.
    assert len(validated_visits) == len(visits)
    assert len(validated_pos) == len(pos)
    assert len(validated_reference) == len(reference)
    assert set(validated_visits.columns) == set(visits.columns)


@pytest.fixture(scope="module")
def validated_extracts(raw_extracts):
    visits, reference, taxonomy, pos = raw_extracts
    return (
        validate_nmta_visits(visits),
        validate_reference_table(reference),
        taxonomy,
        validate_place_of_service(pos),
    )


@pytest.fixture
def gold_from_validated_pipeline(validated_extracts):
    visits, reference, taxonomy, pos = validated_extracts
    engine = create_engine("sqlite:///:memory:")
    create_schema(engine)
    build_silver(
        engine,
        visits=visits,
        reference_table=reference,
        taxonomy=taxonomy,
        place_of_service=pos,
        fetch_approval_date=_fake_fda,
    )
    build_gold(engine)
    return engine


def test_full_pipeline_end_to_end_through_the_validation_gate(gold_from_validated_pipeline):
    """The same real-data figures test_build_silver.py and test_build_gold.py already
    confirm - reproduced here starting from *validated* data, proving the gate doesn't
    change the pipeline's behavior on real data, only rejects bad data it never sees."""
    engine = gold_from_validated_pipeline

    with engine.connect() as conn:
        counts = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            for table in ["dim_month", "dim_product", "dim_specialty", "dim_demographics"]
        }
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly", conn)
        seg = pd.read_sql("SELECT * FROM gold_segment_adoption", conn)

    assert counts["dim_month"] == 72
    assert counts["dim_product"] == 160
    assert counts["dim_specialty"] == 50

    assert monthly["branded_injectable_visits"].sum() == 135_119
    assert monthly["visit_share"].min() > 0.017
    assert monthly["visit_share"].max() < 0.034

    april_2020 = monthly.set_index("month_id").loc[202004]
    assert april_2020["office_visits"] == 39_487
    assert april_2020["telehealth_visits"] == 1_016

    assert len(seg) > 0
    assert (seg["segment_visit_share"] >= 0).all()
    assert (seg["segment_visit_share"] <= 1).all()


def test_full_pipeline_is_idempotent_end_to_end(validated_extracts):
    """Running the entire validated chain twice against the same database must leave
    Silver's surrogate keys untouched and produce byte-identical Gold tables - the
    combined guarantee build_silver.py and build_gold.py each make separately."""
    visits, reference, taxonomy, pos = validated_extracts
    engine = create_engine("sqlite:///:memory:")
    create_schema(engine)

    def _run():
        build_silver(
            engine,
            visits=visits,
            reference_table=reference,
            taxonomy=taxonomy,
            place_of_service=pos,
            fetch_approval_date=_fake_fda,
        )
        build_gold(engine)

    _run()
    with engine.connect() as conn:
        product_ids_before = dict(
            conn.execute(text("SELECT product_name, product_id FROM dim_product")).all()
        )
        gold_before = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)

    _run()
    with engine.connect() as conn:
        product_ids_after = dict(
            conn.execute(text("SELECT product_name, product_id FROM dim_product")).all()
        )
        gold_after = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)

    assert product_ids_before == product_ids_after
    pd.testing.assert_frame_equal(gold_before, gold_after)
