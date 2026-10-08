"""Tests for the export of the publishable external tables to a small committed file."""

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

from oa_market_intelligence.external.export import (
    DEFAULT_SUBSET,
    META_TABLE,
    export_subset,
)
from oa_market_intelligence.external.schema import (
    LOCAL_ONLY_TABLES,
    PUBLISHED_TABLES,
    create_external_schema,
)


@pytest.fixture
def source_db(tmp_path):
    path = tmp_path / "external.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    create_external_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO gold_ext_price_quarterly (quarter_id, j3304_limit_per_mg, "
                "j3301_limit_per_mg, price_ratio) VALUES ('2020Q2', 18.6, 0.15, 123.9)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fact_ext_partb_provider (year, npi, hcpcs_code, setting, "
                "specialty_cms, "
                "entity_type, state_code) "
                "VALUES (2021, '1234567890', 'J3304', 'O', 'ORTHOPEDIC SURGERY', 'I', 'TX')"
            )
        )
    engine.dispose()
    return path


def test_only_the_published_tables_are_copied_and_local_ones_never(tmp_path, source_db):
    target = tmp_path / "subset.db"
    counts = export_subset(source_db, target)
    names = set(inspect(create_engine(f"sqlite:///{target.as_posix()}")).get_table_names())
    assert names == set(PUBLISHED_TABLES) | {META_TABLE}
    assert not names & set(LOCAL_ONLY_TABLES)
    assert counts["gold_ext_price_quarterly"] == 1


def test_rows_and_constraints_survive_the_copy(tmp_path, source_db):
    target = tmp_path / "subset.db"
    export_subset(source_db, target)
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT quarter_id, price_ratio FROM gold_ext_price_quarterly")
        ).one()
    assert row == ("2020Q2", pytest.approx(123.9))
    with pytest.raises(Exception, match="UNIQUE|constraint"):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO gold_ext_price_quarterly (quarter_id) VALUES ('2020Q2')")
            )


def test_the_meta_table_lists_every_table_with_its_row_count(tmp_path, source_db):
    target = tmp_path / "subset.db"
    counts = export_subset(source_db, target)
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    with engine.connect() as conn:
        meta = pd.read_sql(text(f"SELECT * FROM {META_TABLE}"), conn).set_index("table_name")
    assert set(meta.index) == set(PUBLISHED_TABLES)
    assert meta.loc["gold_ext_price_quarterly", "n_rows"] == 1
    assert meta["n_rows"].to_dict() == counts
    assert meta["exported_at"].notna().all()


def test_exporting_again_replaces_the_file(tmp_path, source_db):
    target = tmp_path / "subset.db"
    export_subset(source_db, target)
    counts = export_subset(source_db, target)
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM gold_ext_price_quarterly")).scalar() == 1
    assert counts["gold_ext_price_quarterly"] == 1


def test_the_committed_subset_has_every_published_table_and_no_local_one():
    engine = create_engine(f"sqlite:///{DEFAULT_SUBSET.as_posix()}")
    names = set(inspect(engine).get_table_names())
    assert set(PUBLISHED_TABLES) <= names and not names & set(LOCAL_ONLY_TABLES)
    with engine.connect() as conn:
        for table in ("gold_ext_price_quarterly", "gold_ext_promotion_monthly", "dim_event"):
            assert conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() > 0
    assert DEFAULT_SUBSET.stat().st_size < 10_000_000  # small enough to commit
