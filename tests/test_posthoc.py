"""Tests for the post-hoc flipped-baseline check (src/.../modeling/posthoc.py)."""

import numpy as np
import pytest

from modelling_fixtures import make_inputs
from oa_market_intelligence.modeling import alerts, segment_models
from oa_market_intelligence.modeling.alerts import (
    add_alert_label,
    average_precision,
    recent_drop_score,
)
from oa_market_intelligence.modeling.horizons import horizon_splits, pair_rows
from oa_market_intelligence.modeling.posthoc import flipped_baseline_check


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])
    monkeypatch.setitem(segment_models.RF_BASE, "n_estimators", 10)


def test_flipping_the_sign_turns_the_registered_baseline_around():
    rows, outcomes, _ = make_inputs(n_months=34)
    out = flipped_baseline_check(rows, outcomes, 3, n_boot=40)
    # recomputed independently from the test rows
    paired = add_alert_label(pair_rows(rows, outcomes, 3))
    test = paired.iloc[np.concatenate([te for _, _, te in horizon_splits(3)(paired)])]
    y = test["y_alert"].to_numpy()
    assert out["pr_auc_registered_recent_drop"] == pytest.approx(
        average_precision(y, recent_drop_score(test))
    )
    assert out["pr_auc_flipped_recent_drop"] == pytest.approx(
        average_precision(y, -recent_drop_score(test))
    )
    assert out["decline_share"] == pytest.approx(y.mean())
    assert out["gain_over_flipped"] == pytest.approx(
        out["pr_auc_logistic"] - out["pr_auc_flipped_recent_drop"]
    )
    assert out["registered_percentile"] == pytest.approx(100 * 0.05 / 6)
    assert out["lower_at_2_5"] >= out["lower_at_registered_percentile"] - 1e-12


def test_the_check_is_reproducible():
    rows, outcomes, _ = make_inputs(n_months=34)
    a = flipped_baseline_check(rows, outcomes, 1, n_boot=40)
    b = flipped_baseline_check(rows, outcomes, 1, n_boot=40)
    assert a == b
