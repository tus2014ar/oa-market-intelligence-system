"""The stored modelling results still follow from the committed data and the current code (DL-83).

The two-hour modelling run is not repeated on every change. These tests re-run the cheap, exact
parts on the committed warehouse and compare with `data/reference/modelling_results.json`: the
counts, and every baseline score (pure arithmetic, so equal to the last digit), plus two trained
national models with a loose tolerance (an optimiser may differ in the last digits between
environments). If a change to the harness, the features or the data moved a stored number, a test
here names which.
"""

import pytest
from sqlalchemy import create_engine

from oa_market_intelligence.modeling.alerts import (
    add_alert_label,
    average_precision,
    walk_alert_method,
)
from oa_market_intelligence.modeling.forecast import forecast_scores
from oa_market_intelligence.modeling.horizons import pair_rows
from oa_market_intelligence.modeling.run_modelling import (
    DEFAULT_DB,
    RESULTS_JSON,
    counts,
    load_inputs,
)
from oa_market_intelligence.modeling.segment_eval import pooled, share_month_stats
from oa_market_intelligence.modeling.store import load_results
from oa_market_intelligence.modeling.t1_horizon import make_forecasters, walk_forward_forecast_h
from oa_market_intelligence.modeling.t2_horizon import walk_method

pytestmark = pytest.mark.skipif(not DEFAULT_DB.exists(), reason="the committed warehouse is absent")


@pytest.fixture(scope="module")
def real():
    inputs = load_inputs(create_engine(f"sqlite:///{DEFAULT_DB.as_posix()}"))
    return inputs, load_results(RESULTS_JSON)


def test_the_counts_of_the_stored_run_are_what_the_warehouse_gives(real):
    inputs, results = real
    assert counts(inputs["rows"], inputs["outcomes"]) == results["counts"]


@pytest.mark.parametrize("horizon", [1, 3, 6])
def test_the_national_baseline_error_is_the_stored_one(real, horizon):
    inputs, results = real
    from oa_market_intelligence.modeling.t1_horizon import naive_forecaster

    frame = walk_forward_forecast_h(inputs["series"], naive_forecaster, horizon)
    stored = results["T1"][str(horizon)]["scenarios"]["main"]["scores"]["last_month"]["mae"]
    assert forecast_scores(frame)["mae"] == pytest.approx(stored, abs=1e-12)


@pytest.mark.parametrize("name,tolerance", [("ridge", 1e-6), ("ets_damped", 5e-3)])
def test_two_trained_national_models_stay_close_to_their_stored_error(real, name, tolerance):
    inputs, results = real
    frame = walk_forward_forecast_h(inputs["series"], make_forecasters()[name], 1)
    stored = results["T1"]["1"]["scenarios"]["main"]["scores"][name]["mae"]
    assert forecast_scores(frame)["mae"] == pytest.approx(stored, abs=tolerance)


@pytest.mark.parametrize("horizon", [1, 3, 6])
def test_the_segment_baseline_log_loss_is_the_stored_one(real, horizon):
    inputs, results = real
    paired = pair_rows(inputs["rows"], inputs["outcomes"], horizon)
    stats = share_month_stats(walk_method(paired, horizon, "last_month", ()))
    stored = results["T2"][str(horizon)]["scenarios"]["main"]["baseline_log_loss"]["last_month"]
    assert pooled(stats, "log_loss") == pytest.approx(stored, abs=1e-12)


@pytest.mark.parametrize("horizon", [1, 6])
def test_the_alert_baseline_pr_auc_is_the_stored_one(real, horizon):
    inputs, results = real
    labelled = add_alert_label(pair_rows(inputs["rows"], inputs["outcomes"], horizon))
    preds = walk_alert_method(labelled, horizon, "small_segment", ())
    stored = results["T3"][str(horizon)]["scenarios"]["main"]["methods"]["small_segment"]["pr_auc"]
    assert average_precision(preds["label"], preds["score"]) == pytest.approx(stored, abs=1e-12)
