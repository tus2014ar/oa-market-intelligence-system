"""The end-to-end gate (DL-83): the real raw extracts through every stage of the publish run to the
published database, twice.

One `publish` run on the real data (the session's parsed extracts, no network) must pass all seven
stages, leave a database whose Gold tables equal the committed golden snapshot, contain every table
the downstream layers read (feature tables, model results, predictions), and make predictions that
agree with the data. A second run into another folder must give exactly the same content in every
table (the repeatability check). Marked `e2e`: CI runs it in its own job so the wall-clock time of
the pipeline does not add to the fast tests.
"""

import json
from unittest import mock

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.golden import GOLDEN_JSON, TABLES, compare, fake_fda, snapshot
from oa_market_intelligence.pipeline import run_pipeline
from oa_market_intelligence.publish import publish

pytestmark = pytest.mark.e2e

STAGES = [
    "public_data", "warehouse", "coverage_check", "feature_tables", "model_tables", "scoring",
    "swap",
]
DOWNSTREAM = ("feat_segment_month", "feat_monthly", "model_scores", "backtest_batch", "pred_batch")


@pytest.fixture(scope="module")
def runs(tmp_path_factory, real_ingest):
    """Two complete publish runs on the real extracts, into separate folders."""

    def pipeline(**kwargs):
        with mock.patch("oa_market_intelligence.pipeline.ingest", lambda raw_dir: real_ingest()):
            return run_pipeline(fetch_approval_date=fake_fda, **kwargs)

    out = []
    for name in ("a", "b"):
        folder = tmp_path_factory.mktemp(f"publish_{name}")
        record = publish(published_dir=folder, run_pipeline_fn=pipeline)
        out.append((folder, record))
    return out


def _engine(folder):
    return create_engine(f"sqlite:///{(folder / 'warehouse.db').as_posix()}")


def test_every_stage_passes_in_order_and_the_run_is_logged(runs):
    for folder, record in runs:
        assert record["status"] == "ok", record.get("error")
        assert [s["stage"] for s in record["stages"]] == STAGES
        assert all(s["status"] == "ok" for s in record["stages"])
        assert record["n_months"] == 72 and record["last_month_id"] == 202507
        logged = json.loads((folder / "run_log.jsonl").read_text().strip().splitlines()[-1])
        assert logged["stages"] == record["stages"]
        assert not list(folder.glob(".staging-*"))  # the staging area is gone
    first = runs[0][1]
    assert first["public_data"]["tables"] == 23 and first["public_data"]["manifest_files"] == 171
    assert first["feature_tables"]["feat_segment_month"] > 8000
    scoring = first["scoring"]
    assert scoring["pred_batch"] == scoring["national"] + scoring["segment"]


def test_the_published_gold_tables_equal_the_committed_golden_snapshot(runs):
    golden = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    differences = compare(snapshot(_engine(runs[0][0])), golden)
    assert differences == [], "\n".join(differences)


def test_every_downstream_table_is_there_and_not_empty(runs):
    engine = _engine(runs[0][0])
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        names = {r[0] for r in rows}
        assert set(TABLES) | set(DOWNSTREAM) <= names
        for table in DOWNSTREAM:
            assert conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() > 0, table


def test_the_predictions_agree_with_the_data(runs):
    engine = _engine(runs[0][0])
    with engine.connect() as conn:
        pred = pd.read_sql("SELECT * FROM pred_batch", conn)
        monthly = pd.read_sql("SELECT month_id, visit_share FROM gold_visit_share_monthly", conn)
        scores = pd.read_sql("SELECT * FROM model_scores", conn)
    last = int(monthly["month_id"].max())
    assert set(pred["run_month_id"]) == {last}
    national = pred[pred["level"] == "national"].set_index("horizon")
    assert list(national["target_month_id"]) == [202508, 202510, 202601]
    value_now = monthly.set_index("month_id").loc[last, "visit_share"]
    assert (national["point_share"] - value_now).abs().max() < 1e-12  # "same as last month"
    segment = pred[pred["level"] == "segment"]
    assert set(segment["horizon"]) == {1, 3, 6} and segment["point_share"].between(0, 1).all()
    assert set(segment[segment["alert_flag"].notna()]["horizon"]) == {1}  # the alert: 1 month only
    bias = scores[(scores["task"] == "T2") & (scores["method"] == "logistic")
                  & (scores["scenario"] == "main")].set_index("horizon")["bias_vs_actual"]
    shown = segment[segment["horizon"] == 1]["known_bias"].dropna().unique()
    assert len(shown) == 1 and shown[0] == pytest.approx(bias[1])


def test_two_runs_give_exactly_the_same_content_in_every_table(runs):
    tables = tuple(TABLES) + DOWNSTREAM
    first = snapshot(_engine(runs[0][0]), tables)
    second = snapshot(_engine(runs[1][0]), tables)
    assert first == second  # row counts, column sums and content hashes, table by table
    assert compare(first, second) == []
