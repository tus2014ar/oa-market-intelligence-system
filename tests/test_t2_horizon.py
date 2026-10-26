"""Tests for T2 at horizons 1, 3 and 6 (src/.../modeling/t2_horizon.py): the model-level
rewrite-the-future test, the judgement on planted models, calibration by hand, and a small
end-to-end run that must reproduce itself."""

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import make_inputs
from oa_market_intelligence.modeling import segment_models
from oa_market_intelligence.modeling.horizons import (
    horizon_splits,
    month_index,
    pair_rows,
)
from oa_market_intelligence.modeling.segment_baselines import BASELINES
from oa_market_intelligence.modeling.segment_eval import share_month_stats
from oa_market_intelligence.modeling.segment_models import TunedPredictor
from oa_market_intelligence.modeling.t2_horizon import (
    calibration_summary,
    judge,
    predictor_for,
    run_t2_horizon,
)


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    """One setting per model and a small forest, so the tests stay quick."""
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
    gbm, rf = [{"max_depth": 3, "learning_rate": 0.1}], [{"max_depth": 4, "min_samples_leaf": 20}]
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "gbm", gbm)
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "rf", rf)
    monkeypatch.setitem(segment_models.RF_BASE, "n_estimators", 15)


def _rewrite_after(paired: pd.DataFrame, origin: int, horizon: int) -> pd.DataFrame:
    """Every value after the origin changed: the outcomes of rows whose outcome month is later
    than the origin, and every feature of rows whose own origin is later than this one."""
    rng = np.random.default_rng(99)
    out = paired.copy()
    later_outcome = month_index(out["month_id"].to_numpy()) > origin
    out.loc[later_outcome, "y_share"] = rng.uniform(0, 1, later_outcome.sum())
    out.loc[later_outcome, "y_visits"] = rng.integers(1, 1000, later_outcome.sum())
    later_origin = month_index(out["origin_id"].to_numpy()) > origin
    for column in ("seg_share_lag1", "seg_share_roll3", "market_share_lag1", "seg_prior_z",
                   "seg_prior_t", "spec_prior_z", "spec_prior_t", "market_change_lag1"):
        out.loc[later_origin, column] = out.loc[later_origin, column] * 7 + 3
    return out


@pytest.mark.parametrize("horizon", [1, 3, 6])
@pytest.mark.parametrize("name", ["last_month", "logistic"])
def test_prediction_at_an_origin_does_not_move_when_the_future_is_rewritten(horizon, name):
    rows, outcomes, _ = make_inputs()
    paired = pair_rows(rows, outcomes, horizon)
    split = list(horizon_splits(horizon)(paired))[4]
    test_month, train_idx, test_idx = split
    origin = int(month_index(test_month)) - horizon
    tampered = _rewrite_after(paired, origin, horizon)

    def predict(frame):
        train, test = frame.iloc[train_idx], frame.iloc[test_idx]
        visible = test.drop(columns=[c for c in test.columns if c.startswith("y_")])
        return np.asarray(predictor_for(name, ())(train, visible), float)

    assert np.allclose(predict(paired), predict(tampered), atol=1e-12)


def _preds(months, p, z, t):
    return pd.DataFrame({"month_id": months, "p": p, "z": z, "t": t})


def test_a_planted_better_model_is_promoted_and_a_noise_model_is_not():
    rng = np.random.default_rng(3)
    months = np.repeat(np.arange(202001, 202041), 30)
    n = len(months)
    true_p = rng.uniform(0.02, 0.2, n)
    t = rng.integers(100, 400, n).astype(float)
    z = rng.binomial(t.astype(int), true_p).astype(float)
    flat = np.full(n, true_p.mean())
    stats = {
        "market": share_month_stats(_preds(months, flat, z, t)),
        "last_month": share_month_stats(_preds(months, flat * 1.05, z, t)),
        "segment_history": share_month_stats(_preds(months, flat * 0.95, z, t)),
        "specialty_history": share_month_stats(_preds(months, flat * 1.1, z, t)),
        "logistic": share_month_stats(_preds(months, true_p, z, t)),  # planted better
        "gbm": share_month_stats(_preds(months, flat * rng.uniform(0.9, 1.1, n), z, t)),  # noise
        "rf": share_month_stats(_preds(months, flat * rng.uniform(0.9, 1.1, n), z, t)),  # noise
    }
    out = judge(stats, 1, n_boot=400)
    assert out["best_baseline"] == "market"
    assert out["models"]["logistic"]["promoted"] and out["models"]["logistic"]["estimate"] > 0
    assert not out["models"]["gbm"]["promoted"] and not out["models"]["rf"]["promoted"]
    assert out["serving"] == "logistic" and not out["serving_is_baseline"]
    assert out["lower_percentile"] == pytest.approx(100 * 0.05 / 3)
    assert judge(stats, 6, n_boot=100)["lower_percentile"] == pytest.approx(100 * 0.05 / 6)


def test_when_nothing_is_promoted_the_best_baseline_serves():
    rng = np.random.default_rng(4)
    months = np.repeat(np.arange(202001, 202041), 30)
    n = len(months)
    t = rng.integers(100, 400, n).astype(float)
    z = rng.binomial(t.astype(int), 0.08).astype(float)
    flat = np.full(n, 0.08)
    stats = {name: share_month_stats(_preds(months, flat * f, z, t)) for name, f in
             [("market", 1.0), ("last_month", 1.2), ("segment_history", 0.8),
              ("specialty_history", 1.3), ("logistic", 1.1), ("gbm", 0.9), ("rf", 1.05)]}
    out = judge(stats, 3, n_boot=300)
    assert out["promoted"] == [] and out["serving_is_baseline"]
    assert out["serving"] == "market"


def test_calibration_ratio_by_hand():
    preds = pd.DataFrame(
        {
            "month_id": [1, 1, 2, 2],
            "p": [0.10, 0.20, 0.10, 0.20],
            "z": [10.0, 30.0, 10.0, 30.0],
            "t": [100.0, 100.0, 100.0, 100.0],
            "seg_log_visits_lag1": np.log1p([40.0, 40.0, 300.0, 300.0]),
            "specialty_name": ["A", "B", "A", "B"],
        }
    )
    out = calibration_summary(preds)
    # predicted visits = 10 + 20 + 10 + 20 = 60; observed = 80; 60 / 80 - 1 = -0.25
    assert out["ratio_minus_one"] == pytest.approx(-0.25)
    assert out["by_size"]["under 50"]["n_rows"] == 2
    assert out["by_size"]["200 to 999"]["n_rows"] == 2
    assert out["by_specialty"]["A"]["ratio_minus_one"] == pytest.approx(20 / 20 - 1)
    assert out["by_specialty"]["B"]["ratio_minus_one"] == pytest.approx(40 / 60 - 1)
    assert np.isfinite(out["slope"])


def test_run_t2_horizon_end_to_end_is_reproducible():
    rows, outcomes, _ = make_inputs(n_months=34)
    kwargs = dict(extra_columns=(), scenarios=("main",), n_boot=60, seed=0)
    a = run_t2_horizon(rows, outcomes, 3, **kwargs)
    b = run_t2_horizon(rows, outcomes, 3, **kwargs)
    main = a["scenarios"]["main"]
    # 34 months, rows from the third, first origin at the 24th scored month, h = 3
    assert main["n_test_months"] == 34 - 2 - 24 - 2
    assert main["best_baseline"] in BASELINES
    assert set(main["models"]) == {"logistic", "gbm", "rf"}
    assert set(a["calibration"]) == {"logistic", "gbm", "rf"}
    for key in ("scenarios", "calibration"):
        assert str(a[key]) == str(b[key])  # identical, NaNs and all
    preds = a["_preds"]["logistic"]
    assert preds["month_id"].nunique() == main["n_test_months"]
    assert np.isfinite(preds["p"]).all() and ((preds["p"] >= 0) & (preds["p"] <= 1)).all()


def test_tuned_predictor_is_used_for_trained_models_and_baselines_need_no_fit():
    assert isinstance(predictor_for("gbm", ()), TunedPredictor)
    assert predictor_for("market", ()) is BASELINES["market"]
