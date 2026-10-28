"""The committed published warehouse is complete and consistent (DL-83).

The monthly workflow runs these after `oa-publish` and before it commits, so a published database
that is missing a table, or whose results and predictions do not agree with each other, is never
committed. They also run on every pull request against the database in the repository.
"""

import json
import sqlite3

import pandas as pd
import pytest

from oa_market_intelligence.golden import TABLES
from oa_market_intelligence.modeling.run_modelling import DEFAULT_DB, RESULTS_JSON
from oa_market_intelligence.modeling.store import (
    backtest_batch_frame,
    load_results,
    model_scores_frame,
)

LOG = DEFAULT_DB.parent / "run_log.jsonl"
STAGES = [
    "public_data", "warehouse", "coverage_check", "feature_tables", "model_tables", "scoring",
    "swap",
]
EXPECTED = set(TABLES) | {
    "feat_segment_month", "feat_monthly", "model_scores", "backtest_batch", "pred_batch",
    "bronze_ingest_files", "dq_report",
}

pytestmark = pytest.mark.skipif(not DEFAULT_DB.exists(), reason="no published warehouse")


@pytest.fixture(scope="module")
def con():
    connection = sqlite3.connect(f"file:{DEFAULT_DB.as_posix()}?mode=ro", uri=True)
    yield connection
    connection.close()


def _table(con, name):
    return pd.read_sql(f"SELECT * FROM {name}", con)


def test_every_expected_table_is_present_and_not_empty(con):
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED <= names, sorted(EXPECTED - names)
    for name in sorted(EXPECTED):
        assert con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] > 0, name


def test_the_last_logged_run_passed_every_stage_in_order():
    last = json.loads([line for line in LOG.read_text("utf-8").splitlines() if line.strip()][-1])
    assert last["status"] == "ok" and last["error"] is None
    assert [s["stage"] for s in last["stages"]] == STAGES
    assert all(s["status"] == "ok" for s in last["stages"])


def test_the_result_tables_equal_the_stored_results(con):
    results = load_results(RESULTS_JSON)
    scores = _table(con, "model_scores")
    assert len(scores) == len(model_scores_frame(results))
    assert len(_table(con, "backtest_batch")) == len(backtest_batch_frame(results))
    serving = scores[(scores["scenario"] == "main") & (scores["serving"] == 1)]
    assert len(serving) == 9  # one serving method for each of three tasks at three horizons


def test_the_predictions_are_for_the_month_after_the_last_month_of_data(con):
    last = con.execute("SELECT MAX(month_id) FROM gold_visit_share_monthly").fetchone()[0]
    pred = _table(con, "pred_batch")
    assert set(pred["run_month_id"]) == {last}
    assert set(pred["level"]) == {"national", "segment"} and set(pred["horizon"]) == {1, 3, 6}
    assert (pred["target_month_id"] > last).all()
    run = json.loads([line for line in LOG.read_text("utf-8").splitlines() if line.strip()][-1])
    assert run["scoring"]["pred_batch"] == len(pred)  # the log and the table agree


def test_the_predictions_follow_the_adoption_map(con):
    from oa_market_intelligence.modeling.scoring import load_serving_map

    serving = load_serving_map()
    pred = _table(con, "pred_batch")
    for level, key in (("national", "national"), ("segment", "segment")):
        used = pred[pred["level"] == level].groupby("horizon")["model"].agg(lambda s: set(s))
        for horizon, model in serving[key].items():
            assert used[int(horizon)] == {model}, (level, horizon)
    flagged = pred[pred["alert_flag"].notna()]
    assert set(flagged["horizon"]) == {int(h) for h in serving["alert"]}
