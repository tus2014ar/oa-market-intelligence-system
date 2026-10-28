"""Tests for the site's data layer (src/.../serving/data.py) on the committed warehouse: read-only,
aggregates only, the shapes the page expects, and the findings the site shows equal the README."""

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from oa_market_intelligence.serving import data

README = Path(__file__).resolve().parent.parent / "README.md"
pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")


@pytest.fixture(scope="module")
def engine():
    eng = data.open_engine()
    yield eng
    eng.dispose()


def test_the_engine_is_read_only(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM gold_visit_share_monthly")).scalar() == 72
        with pytest.raises(Exception, match="readonly|read-only|attempt to write"):
            conn.execute(text("CREATE TABLE should_not_exist (x INTEGER)"))


def test_the_market_series_and_the_segments_have_the_expected_shape(engine):
    monthly = data.market_monthly(engine)
    assert len(monthly) == 72 and monthly["month_id"].iloc[-1] == 202507
    assert monthly["visit_share"].between(0.01, 0.05).all()
    seg = data.segments(engine, "specialty")
    assert {"group", "zilretta_visits", "category_visits", "share"} <= set(seg.columns)
    assert (seg["category_visits"] >= 1000).all() and seg["share"].between(0, 1).all()
    assert set(data.segments(engine, "gender")["group"]) >= {"FEMALE", "MALE"}
    with pytest.raises(ValueError):
        data.segments(engine, "patient_id")
    by_year = data.specialty_by_year(engine)
    assert {"specialty", "year", "share"} <= set(by_year.columns)


def test_the_site_reads_aggregates_only(engine):
    forbidden = {"patient_id", "npi", "provider_id", "person_id", "claim_id"}
    frames = [data.market_monthly(engine), data.segments(engine), data.specialty_by_year(engine),
              data.predictions(engine), data.model_scores(engine), data.backtest(engine)]
    for frame in frames:
        assert not forbidden & {c.lower() for c in frame.columns}
    pred = data.predictions(engine)
    assert len(pred) == 342 and set(pred["level"]) == {"national", "segment"}
    # segment rows are specialty x age band x gender groups with their visit counts, nothing finer
    seg = pred[pred["level"] == "segment"]
    assert not seg.duplicated(["horizon", "specialty_name", "age_band", "gender"]).any()


def test_the_reference_files_and_the_last_run_load():
    assert data.serving_map()["decision"] == "DL-79"
    assert "horizons" in data.posthoc()
    run = data.last_run()
    assert run["status"] == "ok" and [s["stage"] for s in run["stages"]][-1] == "swap"
    assert data.last_run("no/such/log.jsonl") == {}


def test_the_findings_the_site_shows_are_the_ones_in_the_readme():
    shown = data.findings()
    table = {}
    for line in README.read_text("utf-8").splitlines():
        m = re.match(r"^\| (\d+) \| (.*?) \| (.*?) \| (.*) \|$", line)
        if m:
            table[int(m.group(1))] = tuple(x.replace("**", "").strip() for x in m.groups()[1:])
    assert [f["id"] for f in shown] == sorted(table) == list(range(1, 18))
    for f in shown:
        assert (f["finding"], f["evidence"], f["impact"]) == table[f["id"]]
