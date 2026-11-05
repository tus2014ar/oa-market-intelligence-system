"""Tests for the round 2 runner (src/.../modeling/round2/run.py): all seven tests are run and
summarised, the output is plain JSON, and a repeat run is byte-identical. Small synthetic data."""

import json

import pytest

from modelling_fixtures import make_inputs
from oa_market_intelligence.modeling import segment_models
from oa_market_intelligence.modeling.round2 import run as round2


@pytest.fixture(scope="module")
def small_run():
    with (
        pytest.MonkeyPatch.context() as patch
    ):  # restored afterwards: other tests use the real grid
        patch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
        rows, outcomes, _ = make_inputs(n_months=44)
        first = round2.run_round2(rows, outcomes, scenarios=("main",), n_boot=40)
        again = round2.run_round2(rows, outcomes, scenarios=("main",), n_boot=40)
        yield first, again


def _text(result):
    return json.dumps(round2.clean(result), sort_keys=True, allow_nan=False)


def test_the_run_has_the_seven_registered_tests(small_run):
    first, _ = small_run
    assert [(s["test"], s["horizon"]) for s in first["summary"]] == list(round2.TESTS)
    assert len(first["summary"]) == first["meta"]["tests"] == 7
    assert first["tests_passed"] == sum(s["passes"] for s in first["summary"])
    assert first["meta"]["percentile"] == pytest.approx(0.05 / 7 * 100)


def test_the_output_is_plain_json_and_repeats_byte_for_byte(small_run):
    first, again = small_run
    assert _text(first) == _text(again)
    assert "_preds" not in _text(first) and "_frame" not in _text(first)


def test_the_stored_round_1_score_is_read_from_the_results_file():
    stored = {"T2": {"1": {"scenarios": {"main": {"models": {"logistic": {"log_loss": 0.11}}}}}}}
    assert round2.stored_logistic_log_loss(stored, 1) == 0.11


def test_a_wrong_stored_score_stops_the_run():
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
        rows, outcomes, _ = make_inputs(n_months=44)
        wrong = {
            "T2": {
                str(h): {"scenarios": {"main": {"models": {"logistic": {"log_loss": 0.5}}}}}
                for h in (1, 3)
            }
        }
        with pytest.raises(ValueError, match="differ from round 1"):
            round2.run_round2(rows, outcomes, stored=wrong, scenarios=("main",), n_boot=10)


def test_the_post_hoc_check_flips_the_trend_scores_and_leaves_the_registered_rows_alone():
    import numpy as np
    import pandas as pd

    from oa_market_intelligence.modeling.round2 import posthoc

    rng = np.random.default_rng(0)
    rows = []
    for month in range(30):
        for i in range(12):
            actual = rng.normal(0, 0.01)
            rows.append(
                {
                    "month_id": 202001 + month,
                    "segment": f"seg{i:02d}",
                    "actual": actual,
                    "model": actual + rng.normal(0, 0.005),
                    "segment_trend": -actual + rng.normal(0, 0.005),  # trends reverse
                    "specialty_trend": rng.normal(0, 0.01),
                    "random": rng.random(),
                }
            )
    frame = pd.DataFrame(rows)
    before = frame.copy()
    result = posthoc.flipped_check(frame, n_boot=100)
    pd.testing.assert_frame_equal(frame, before)  # the input is not changed
    assert result["mean_spearman"]["segment_reversal"] > 0.5  # a reversal score ranks well here
    assert result["mean_spearman"]["model"] > result["mean_spearman"]["specialty_reversal"]
    assert set(result["model_versus_flipped"]) == {"segment_reversal", "specialty_reversal"}
