"""Tests for the ML-ready layer (R2): every usable series with the month it became known, and what
was known as of each month.

Synthetic external tables with planted values check each signal's availability and that nothing
IQVIA-derived leaks in; planted violations check that the leakage test would catch a future value;
one test builds the real committed subset and requires zero violations over every IQVIA month.
"""

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.availability import load_availability
from oa_market_intelligence.external.export import DEFAULT_SUBSET
from oa_market_intelligence.external.schema import create_external_schema
from oa_market_intelligence.mart import (
    AS_OF_LAG_MONTHS,
    build_as_of_table,
    build_mart,
    build_signals,
    filing_dates,
    leakage_violations,
    signals_as_of,
    wide,
)
from oa_market_intelligence.warehouse.schema import create_schema

AVAILABILITY = load_availability()
MANIFEST_ROWS = [
    {
        "dataset": "SEC Pacira BioSciences",
        "file": "SEC Filings\\Pacira BioSciences\\2022-05-10_10-Q_0001234567-22-000010_pcrx.htm",
    },
    {
        "dataset": "SEC Pacira BioSciences",
        "file": "SEC Filings/Pacira BioSciences/2023-02-28_10-K_0001234567-23-000003_pcrx.htm",
    },
    {"dataset": "NPPES", "file": "NPPES/NPPES_Data_Dissemination_September_2026_V2.zip"},
]


def _insert(conn, table, **values):
    columns = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({marks})"), values)


@pytest.fixture
def subset():
    engine = create_engine("sqlite://")
    create_external_schema(engine)
    with engine.begin() as conn:
        _insert(
            conn,
            "gold_ext_price_quarterly",
            quarter_id="2020Q2",
            j3304_limit_per_mg=18.6,
            j3301_limit_per_mg=0.15,
            price_ratio=124.0,
        )
        _insert(
            conn,
            "gold_ext_price_quarterly",
            quarter_id="2020Q3",
            j3304_limit_per_mg=18.3,
            j3301_limit_per_mg=0.148,
            price_ratio=123.0,
        )
        _insert(
            conn,
            "gold_ext_promotion_monthly",
            month_id=202112,
            n_physicians_paid=30,
            n_practitioners_paid=9,
            total_amount_usd=1000.0,
            n_records=50,
            n_flagged_records=0,
            iqvia_share_pct=2.5,
        )
        _insert(
            conn,
            "gold_ext_promotion_monthly",
            month_id=202001,
            n_physicians_paid=900,
            n_practitioners_paid=None,
            total_amount_usd=5000.0,
            n_records=200,
            n_flagged_records=0,
            iqvia_share_pct=2.0,
        )
        _insert(
            conn,
            "fact_ext_company_revenue",
            period_end="2022-03-31",
            company="Pacira",
            product="Zilretta",
            period_type="quarter",
            net_sales_usd=23.6e6,
            source_accession="0001234567-22-000010",
            derived=0,
        )
        _insert(
            conn,
            "fact_ext_company_revenue",
            period_end="2022-12-31",
            company="Pacira",
            product="Zilretta",
            period_type="quarter",
            net_sales_usd=28.0e6,
            source_accession="0001234567-23-000003",
            derived=1,
        )
        _insert(
            conn,
            "fact_ext_company_revenue",
            period_end="2022-12-31",
            company="Pacira",
            product="Zilretta",
            period_type="year",
            net_sales_usd=105.5e6,
            source_accession="0001234567-23-000003",
            derived=0,
        )
        _insert(
            conn,
            "dim_event",
            event_id=1,
            event_date="2021-04-01",
            event_type="payment",
            description="a",
            source="s",
            verified=1,
        )
        _insert(
            conn,
            "dim_event",
            event_id=2,
            event_date="2021-04-20",
            event_type="payment",
            description="b",
            source="s",
            verified=1,
        )
        _insert(
            conn,
            "gold_ext_specialty_triangulation",
            period="2021",
            specialty_group="ORTHOPEDIC SURGERY",
            n_visible_providers=1000,
            n_zilretta_providers=40,
            medicare_adoption_rate=0.04,
            iqvia_adjusted_share=0.02,
        )
        _insert(
            conn,
            "gold_ext_specialty_triangulation",
            period="pooled_2020_2024",
            specialty_group="ORTHOPEDIC SURGERY",
            n_visible_providers=5000,
            n_zilretta_providers=200,
            medicare_adoption_rate=0.04,
            iqvia_adjusted_share=0.02,
        )
    return engine


@pytest.fixture
def signals(subset):
    return build_signals(subset, AVAILABILITY, MANIFEST_ROWS)


def one(frame, signal_id, **where):
    rows = frame[frame["signal_id"] == signal_id]
    for column, value in where.items():
        rows = rows[rows[column] == value]
    assert len(rows) == 1, (signal_id, where, len(rows))
    return rows.iloc[0]


# ---------- each signal's availability ----------


def test_price_signals_are_known_when_the_quarter_starts(signals):
    row = one(signals, "asp.price_ratio", period_end_month=202006)
    assert row["available_from_month"] == 202004 and row["period_start_month"] == 202004
    assert row["value"] == 124.0 and row["source_id"] == "asp_price"


def test_promotion_signals_are_known_in_june_of_the_next_year(signals):
    row = one(signals, "openpay.physicians_paid", period_end_month=202112)
    assert row["available_from_month"] == 202206 and row["value"] == 30
    assert one(signals, "openpay.physicians_paid", period_end_month=202001)["value"] == 900


def test_a_missing_value_is_not_a_signal(signals):
    practitioners = signals[signals["signal_id"] == "openpay.practitioners_paid"]
    assert list(practitioners["period_end_month"]) == [202112]  # January 2020 was empty


def test_company_sales_are_known_on_the_filing_date_of_the_cited_document(signals):
    q1 = one(signals, "company.net_sales_usd", period_end_month=202203)
    assert q1["available_from_month"] == 202205  # filed 10 May 2022
    q4 = one(signals, "company.net_sales_usd", period_end_month=202212)
    assert q4["available_from_month"] == 202302  # the 10-K, filed 28 February 2023
    assert len(signals[signals["signal_id"] == "company.net_sales_usd"]) == 2  # year row excluded


def test_events_are_counted_per_month_and_known_in_that_month(signals):
    row = one(signals, "event.count_in_month", period_end_month=202104)
    assert row["value"] == 2 and row["available_from_month"] == 202104


def test_medicare_adoption_is_by_specialty_two_years_late_and_excludes_the_pooled_row(signals):
    row = one(signals, "medicare.adoption_rate", period_end_month=202112)
    assert row["specialty_group"] == "ORTHOPEDIC SURGERY" and row["value"] == 0.04
    assert row["available_from_month"] == 202312  # data year + 2, December
    assert signals[signals["signal_id"] == "medicare.adoption_rate"][
        "period_end_month"
    ].tolist() == [202112]


def test_nothing_iqvia_derived_enters_the_layer(signals):
    ids = set(signals["signal_id"])
    assert not any("iqvia" in name for name in ids)
    assert not {"openpay.iqvia_share_pct", "medicare.iqvia_adjusted_share"} & ids


def test_filing_dates_are_read_from_the_sec_file_names_with_either_slash_style():
    mapping = filing_dates(MANIFEST_ROWS)
    assert mapping == {"0001234567-22-000010": "2022-05-10", "0001234567-23-000003": "2023-02-28"}


def test_a_revenue_row_whose_filing_is_unknown_is_an_error_not_a_guess(subset):
    with pytest.raises(ValueError, match="0001234567-23-000003"):
        build_signals(subset, AVAILABILITY, MANIFEST_ROWS[:1])


# ---------- as of a month ----------


def _plain(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "signal_id",
            "specialty_group",
            "period_end_month",
            "value",
            "available_from_month",
        ],
    )


def test_as_of_picks_the_latest_value_already_known_and_ignores_the_future():
    data = _plain(
        [
            ("s", "", 202003, 1.0, 202004),
            ("s", "", 202006, 2.0, 202007),
            ("s", "", 202009, 3.0, 202010),  # not known yet in August
        ]
    )
    row = signals_as_of(data, 202008).iloc[0]
    assert row["value"] == 2.0 and row["period_end_month"] == 202006
    assert row["age_months"] == 2 and row["available_from_month"] == 202007
    assert signals_as_of(data, 202003).empty  # nothing known yet


def test_a_value_known_exactly_in_the_as_of_month_counts():
    data = _plain([("s", "", 202003, 1.0, 202004)])
    assert len(signals_as_of(data, 202004)) == 1 and signals_as_of(data, 202003).empty


def test_each_signal_and_specialty_group_gets_its_own_latest_value():
    data = _plain(
        [
            ("a", "X", 202001, 1.0, 202002),
            ("a", "Y", 202001, 5.0, 202002),
            ("b", "", 202001, 9.0, 202002),
        ]
    )
    out = signals_as_of(data, 202012)
    assert len(out) == 3 and set(out["specialty_group"]) == {"X", "Y", ""}


def test_the_table_for_a_month_uses_what_was_known_one_month_earlier(signals):
    assert AS_OF_LAG_MONTHS == 1
    table = build_as_of_table(signals, [202004, 202005])
    april = table[table["month_id"] == 202004]
    assert (april["as_of_month"] == 202003).all()
    assert "asp.price_ratio" not in set(april["signal_id"])  # known from 202004, as of 202003: no
    may = table[table["month_id"] == 202005]
    assert "asp.price_ratio" in set(may["signal_id"])  # known from 202004, as of 202004: yes


# ---------- leakage ----------


def test_the_table_never_contains_a_value_that_was_not_yet_known(signals):
    months = [m for y in (2020, 2021, 2022, 2023) for m in range(y * 100 + 1, y * 100 + 13)]
    table = build_as_of_table(signals, months)
    assert len(table) > 0 and leakage_violations(table).empty


def test_the_leakage_test_catches_a_planted_future_value(signals):
    table = build_as_of_table(signals, [202105, 202106])
    bad = table.iloc[[0]].copy()
    bad["available_from_month"] = bad["as_of_month"] + 1  # known only after the as-of month
    caught = leakage_violations(pd.concat([table, bad], ignore_index=True))
    assert len(caught) == 1


def test_a_price_known_before_its_quarter_ends_is_not_a_violation(signals):
    table = build_as_of_table(signals, [202005])  # as of April: the Q2 schedule is already posted
    row = one(table, "asp.price_ratio")
    assert row["period_end_month"] == 202006 and row["as_of_month"] == 202004
    assert row["age_months"] < 0 and leakage_violations(table).empty


# ---------- the wide table for modelling ----------


def test_the_wide_table_has_one_row_per_month_and_a_column_per_signal_and_group(signals):
    table = build_as_of_table(signals, [202105, 202401])
    frame = wide(table)
    assert list(frame.index) == [202105, 202401]
    assert "asp.price_ratio" in frame.columns
    column = "medicare.adoption_rate|ORTHOPEDIC SURGERY"
    assert column in frame.columns
    assert pd.isna(frame.loc[202105, column])  # 2021 adoption is only known from December 2023
    assert frame.loc[202401, column] == 0.04


# ---------- the tables in the warehouse ----------


def test_the_mart_is_written_replaced_on_rerun_and_skipped_without_a_subset(tmp_path, subset):
    engine = create_engine("sqlite://")
    create_schema(engine)
    with engine.begin() as conn:
        for month in (202004, 202005, 202006):
            conn.execute(
                text(
                    "INSERT INTO dim_month (month_id, calendar_date, year, quarter, month_number, "
                    "month_name) VALUES (:m, 'x', :y, 2, :n, 'x')"
                ),
                {"m": month, "y": 2020, "n": month % 100},
            )
    assert build_mart(engine, tmp_path / "missing.db", MANIFEST_ROWS) is None
    # a real file path is needed; write the synthetic subset to disk
    path = tmp_path / "subset.db"
    on_disk = create_engine(f"sqlite:///{path.as_posix()}")
    create_external_schema(on_disk)
    with subset.connect() as read, on_disk.begin() as write:
        for table in (
            "gold_ext_price_quarterly",
            "gold_ext_promotion_monthly",
            "fact_ext_company_revenue",
            "dim_event",
            "gold_ext_specialty_triangulation",
        ):
            pd.read_sql(text(f"SELECT * FROM {table}"), read).to_sql(
                table, write, if_exists="append", index=False
            )
    first = build_mart(engine, path, MANIFEST_ROWS)
    second = build_mart(engine, path, MANIFEST_ROWS)
    assert first == second and first["signals"] > 0 and first["as_of_rows"] > 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM mart_signal")).scalar() == first["signals"]
        late = conn.execute(
            text("SELECT COUNT(*) FROM mart_signal_asof WHERE available_from_month > as_of_month")
        ).scalar()
    assert late == 0


# ---------- the real committed subset ----------


def test_the_real_subset_builds_with_zero_leakage_over_every_iqvia_month():
    import json

    from oa_market_intelligence.external.common import REFERENCE_DIR

    manifest = [
        json.loads(line)
        for line in (REFERENCE_DIR / "external_manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    engine = create_engine(f"sqlite:///{DEFAULT_SUBSET.as_posix()}")
    real = build_signals(engine, AVAILABILITY, manifest)
    expected = {
        "asp.price_ratio",
        "openpay.physicians_paid",
        "company.net_sales_usd",
        "event.count_in_month",
        "medicare.adoption_rate",
    }
    assert expected <= set(real["signal_id"])
    assert not any("iqvia" in name for name in set(real["signal_id"]))
    months = [m for y in range(2019, 2026) for m in range(y * 100 + 1, y * 100 + 13)]
    table = build_as_of_table(real, [m for m in months if 201908 <= m <= 202507])
    assert len(table) > 1000 and leakage_violations(table).empty
