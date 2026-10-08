"""Tests for the hand-built reference tables (DL-59, step 5): events and company revenue."""

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.external.common import REFERENCE_DIR
from oa_market_intelligence.external.loaders.events import events_frame, load_events
from oa_market_intelligence.external.loaders.revenue import (
    load_revenue,
    revenue_frame,
    revenue_problems,
)
from oa_market_intelligence.external.schema import create_external_schema


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    create_external_schema(engine)
    return engine


def _write(tmp_path, rows):
    path = tmp_path / "events.csv"
    pd.DataFrame(
        rows,
        columns=["event_date", "event_end_date", "event_type", "description", "source", "verified"],
    ).to_csv(path, index=False)
    return tmp_path


GOOD = ["2021-11-19", "", "corporate", "Acquisition", "10-K accession 1", "1"]


def test_the_committed_events_table_is_valid_and_has_the_verified_anchors():
    frame = events_frame(REFERENCE_DIR)
    assert len(frame) >= 12
    by_date = frame.set_index("event_date")
    assert by_date.loc["2017-10-06", "verified"] == 1  # FDA approval, from the signed letter
    assert by_date.loc["2021-11-19", "event_type"] == "corporate"  # the acquisition closes
    assert by_date.loc["2024-02-21", "event_type"] == "data"  # the claims outage
    passthrough = frame[
        (frame["event_date"] == "2018-04-01") & (frame["event_end_date"] == "2021-03-31")
    ]
    assert len(passthrough) == 2  # Zilretta and Durolane, the same window
    assert (
        frame["source"].str.strip().ne("").all() and frame["description"].str.strip().ne("").all()
    )


def test_an_unverified_event_is_marked_as_such():
    frame = events_frame(REFERENCE_DIR)
    unverified = frame[frame["verified"] == 0]
    assert {"2021-08-31", "2024-03-01"} <= set(
        unverified["event_date"]
    )  # AAOS guideline, IQVIA dip


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (["2021-13-40", "", "x", "d", "s", "1"], "date"),
        (["2021-01-01", "2020-01-01", "x", "d", "s", "1"], "end"),
        (["2021-01-01", "", "x", "d", "s", "2"], "verified"),
        (["2021-01-01", "", "x", "", "s", "1"], "description"),
        (["2021-01-01", "", "x", "d", "", "1"], "source"),
    ],
)
def test_a_malformed_event_is_refused(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        events_frame(_write(tmp_path, [row]))


def test_events_load_idempotently_into_the_event_dimension(tmp_path, engine):
    directory = _write(tmp_path, [GOOD, ["2020-03-11", "", "public_health", "WHO", "10-K", "1"]])
    load_events(engine, directory)
    load_events(engine, directory)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT event_date, event_end_date, verified FROM dim_event ORDER BY event_date")
        ).all()
    assert [tuple(r) for r in rows] == [("2020-03-11", None, 1), ("2021-11-19", None, 1)]


# ---------- company revenue ----------

REVENUE_COLUMNS = [
    "period_end",
    "company",
    "product",
    "period_type",
    "fiscal_label",
    "net_sales_usd",
    "source_accession",
    "source_form",
    "source_page",
    "derived",
    "note",
]


def _revenue_rows(**override):
    base = [
        [
            "2020-03-31",
            "Co",
            "Zilretta",
            "quarter",
            "2020Q1",
            "20",
            "0001564590-20-022827",
            "10-Q",
            "p",
            "0",
            "",
        ],
        [
            "2020-06-30",
            "Co",
            "Zilretta",
            "quarter",
            "2020Q2",
            "15",
            "0001564590-20-036837",
            "10-Q",
            "p",
            "0",
            "",
        ],
        [
            "2020-09-30",
            "Co",
            "Zilretta",
            "quarter",
            "2020Q3",
            "25",
            "0001564590-20-050458",
            "10-Q",
            "p",
            "0",
            "",
        ],
        [
            "2020-09-30",
            "Co",
            "Zilretta",
            "nine_months",
            "20209M",
            "60",
            "0001564590-20-050458",
            "10-Q",
            "p",
            "0",
            "",
        ],
        [
            "2020-12-31",
            "Co",
            "Zilretta",
            "quarter",
            "2020Q4",
            "30",
            "0001564590-21-012050",
            "10-K",
            "p",
            "1",
            "year minus nine months",
        ],
        [
            "2020-12-31",
            "Co",
            "Zilretta",
            "year",
            "FY2020",
            "90",
            "0001564590-21-012050",
            "10-K",
            "p",
            "0",
            "",
        ],
    ]
    return base


def _revenue_dir(tmp_path, rows):
    path = tmp_path / "company_revenue.csv"
    pd.DataFrame(rows, columns=REVENUE_COLUMNS).to_csv(path, index=False)
    return tmp_path


def test_the_committed_revenue_table_is_valid_and_its_arithmetic_holds():
    frame = revenue_frame(REFERENCE_DIR)
    assert revenue_problems(frame) == []
    assert frame["source_accession"].str.match(r"^\d{10}-\d{2}-\d{6}$").all()


def test_the_committed_quarterly_series_is_complete_and_flags_every_derived_quarter():
    frame = revenue_frame(REFERENCE_DIR)
    quarters = frame[frame["period_type"] == "quarter"].set_index("fiscal_label")
    expected = [f"{y}Q{q}" for y in range(2019, 2027) for q in range(1, 5)][:-2]  # to 2026Q2
    assert list(quarters.index) == expected
    assert all(quarters.loc[f"{y}Q4", "derived"] == 1 for y in range(2019, 2026))
    assert (quarters.drop([f"{y}Q4" for y in range(2019, 2026)])["derived"] == 0).all()


def test_values_match_the_figures_read_from_the_filings():
    frame = revenue_frame(REFERENCE_DIR).set_index(["fiscal_label", "period_type"])
    assert frame.loc[("2020Q2", "quarter"), "net_sales_usd"] == 15_451_000  # the COVID quarter
    assert frame.loc[("FY2020", "year"), "net_sales_usd"] == 85_552_000
    assert frame.loc[("2022Q1", "quarter"), "net_sales_usd"] == 23_635_000  # first Pacira quarter
    assert frame.loc[("2022Q1", "quarter"), "company"] == "Pacira BioSciences"
    assert frame.loc[("2019Q1", "quarter"), "company"] == "Flexion Therapeutics"


def test_the_fourth_quarter_of_2021_spans_the_acquisition_and_says_so():
    frame = revenue_frame(REFERENCE_DIR).set_index(["fiscal_label", "period_type"])
    row = frame.loc[("2021Q4", "quarter")]
    assert row["company"] == "Flexion Therapeutics and Pacira BioSciences" and row["derived"] == 1
    assert row["net_sales_usd"] == pytest.approx(28_610_000)
    assert "19 November 2021" in row["note"] and "0.05" in row["note"]


def test_a_broken_identity_is_reported(tmp_path):
    rows = _revenue_rows()
    assert revenue_problems(revenue_frame(_revenue_dir(tmp_path, rows))) == []
    rows[1][5] = "16"  # Q2 no longer adds up to the nine months
    problems = revenue_problems(revenue_frame(_revenue_dir(tmp_path, rows)))
    assert any("first three quarters" in p for p in problems)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (9, "2", "derived"),
        (6, "not-an-accession", "accession"),
        (5, "-3", "positive"),
        (3, "month", "period_type"),
        (0, "31/12/2020", "ISO"),
    ],
)
def test_a_malformed_revenue_row_is_refused(tmp_path, column, value, message):
    rows = _revenue_rows()
    rows[0][column] = value
    with pytest.raises(ValueError, match=message):
        revenue_frame(_revenue_dir(tmp_path, rows))


def test_a_derived_row_must_explain_itself(tmp_path):
    rows = _revenue_rows()
    rows[4][10] = ""
    with pytest.raises(ValueError, match="derived"):
        revenue_frame(_revenue_dir(tmp_path, rows))


def test_revenue_loads_idempotently_and_the_year_and_fourth_quarter_share_a_period_end(
    tmp_path, engine
):
    directory = _revenue_dir(tmp_path, _revenue_rows())
    load_revenue(engine, directory)
    load_revenue(engine, directory)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM fact_ext_company_revenue")).scalar()
        both = conn.execute(
            text("SELECT count(*) FROM fact_ext_company_revenue WHERE period_end = '2020-12-31'")
        ).scalar()
    assert n == 6 and both == 2


def test_a_table_that_fails_its_arithmetic_is_not_loaded(tmp_path, engine):
    rows = _revenue_rows()
    rows[0][5] = "99"
    with pytest.raises(ValueError, match="first three quarters"):
        load_revenue(engine, _revenue_dir(tmp_path, rows))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM fact_ext_company_revenue")).scalar() == 0
