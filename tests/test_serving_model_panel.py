"""Tests for the serving rule and model panel (src/.../serving/model_panel.py).

The serving rule is pre-specified (decision log DL-32) so a model is never promoted by
default: a trained model serves only if its balanced accuracy is strictly above BOTH the
95th percentile of what random guessing produces AND every simple baseline's. Otherwise the
best baseline serves, and the site says so.
"""

import pandas as pd
import pytest

from oa_market_intelligence.serving.model_panel import serving_decision


def _scores(**balanced_accuracy):
    return pd.DataFrame({"balanced_accuracy": balanced_accuracy})


BASELINES = {"always-majority": 0.333, "persistence": 0.406, "seasonal": 0.419}


def test_a_model_inside_the_chance_band_is_not_promoted():
    decision = serving_decision(_scores(**BASELINES, logistic=0.369), chance_p95=0.461)
    assert decision["promoted"] is False
    assert decision["serving"] == "seasonal"  # the best baseline
    assert "chance" in decision["reason"].lower()


def test_a_model_above_chance_but_below_a_baseline_is_not_promoted():
    decision = serving_decision(_scores(**BASELINES, logistic=0.415), chance_p95=0.40)
    assert decision["promoted"] is False
    assert decision["serving"] == "seasonal"
    assert "baseline" in decision["reason"].lower()


def test_a_model_above_chance_and_every_baseline_is_promoted():
    decision = serving_decision(_scores(**BASELINES, logistic=0.55), chance_p95=0.461)
    assert decision["promoted"] is True
    assert decision["serving"] == "logistic"


def test_the_bar_is_strict_a_tie_does_not_promote():
    decision = serving_decision(_scores(**BASELINES, logistic=0.461), chance_p95=0.461)
    assert decision["promoted"] is False
    decision = serving_decision(_scores(**BASELINES, logistic=0.419), chance_p95=0.30)
    assert decision["promoted"] is False


def test_the_best_trained_model_is_the_one_considered():
    decision = serving_decision(
        _scores(**BASELINES, logistic=0.30, forest=0.60), chance_p95=0.461
    )
    assert decision["promoted"] is True
    assert decision["serving"] == "forest"


def test_with_no_trained_model_the_best_baseline_serves():
    decision = serving_decision(_scores(**BASELINES), chance_p95=0.461)
    assert decision["promoted"] is False
    assert decision["serving"] == "seasonal"


@pytest.mark.parametrize("missing", ["always-majority", "persistence", "seasonal"])
def test_missing_a_baseline_is_an_error_not_a_silent_promotion(missing):
    baselines = {k: v for k, v in BASELINES.items() if k != missing}
    with pytest.raises(ValueError, match="baseline"):
        serving_decision(_scores(**baselines, logistic=0.9), chance_p95=0.461)
