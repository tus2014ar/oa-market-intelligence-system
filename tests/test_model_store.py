"""Tests for the model-result tables (src/.../modeling/store.py): every row of the stored results
reaches `model_scores` with the right direction, gain and serving flag, and the demonstration
reaches `backtest_batch` with shares as fractions."""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from oa_market_intelligence.modeling.run_modelling import RESULTS_JSON
from oa_market_intelligence.modeling.store import (
    BACKTEST_COLUMNS,
    SCORE_COLUMNS,
    backtest_batch_frame,
    load_results,
    model_scores_frame,
    write_model_tables,
)


def _tiny_results():
    gain = {"estimate": 0.01, "low": 0.002, "high": 0.02, "promoted": True}
    return {
        "T1": {"1": {"scenarios": {"main": {
            "best_baseline": "last_month", "serving": "last_month", "n_test_months": 48,
            "scores": {"last_month": {"mae": 0.12, "cover90": 0.9},
                       "same_month_last_year": {"mae": 0.2, "cover90": 0.8},
                       "ridge": {"mae": 0.15, "cover90": 0.95}},
            "models": {"ridge": {**gain, "estimate": -0.03, "low": -0.05, "promoted": False,
                                 "mae": 0.15}},
        }}}},
        "T2": {"1": {"calibration": {"logistic": {"ratio_minus_one": 0.05}}, "scenarios": {"main": {
            "best_baseline": "last_month", "serving": "logistic", "n_test_months": 46,
            "baseline_log_loss": {"last_month": 0.113, "market": 0.116},
            "models": {"logistic": {**gain, "log_loss": 0.1125, "months_better": 46,
                                    "relative_gain": 0.004}},
        }}}},
        "T3": {"1": {"scenarios": {"main": {
            "best_baseline": "small_segment", "serving": "logistic", "n_test_months": 46,
            "methods": {
                "small_segment": {"pr_auc": 0.38, "precision": 0.36, "recall": 0.7, "f1": 0.48,
                                  "n_alerts": 1400},
                "logistic": {"pr_auc": 0.55, "precision": 0.49, "recall": 0.64, "f1": 0.56,
                             "n_alerts": 950},
            },
            "models": {"logistic": gain},
        }}}},
        "demonstration": {
            "national": [{"cutoff": 202212, "step": 1, "month_id": 202301, "model": "last_month",
                          "actual": 2.4, "point": 2.9, "lo90": 2.6, "hi90": 3.2}],
            "segment": [{"cutoff": 202212, "step": 1, "month_id": 202301, "model": "logistic",
                         "specialty_name": "RHEUMATOLOGY", "visits": 1000.0,
                         "predicted_share": 0.03, "actual_share": 0.025}],
        },
    }


def test_scores_by_hand():
    frame = model_scores_frame(_tiny_results())
    assert list(frame.columns) == SCORE_COLUMNS
    assert len(frame) == 3 + 3 + 2  # T1: 3 methods, T2: 2 baselines + 1 model, T3: 2 methods
    t1 = frame[frame["task"] == "T1"].set_index("method")
    assert t1.loc["last_month", "serving"] == 1 and t1.loc["last_month", "is_best_baseline"] == 1
    assert t1.loc["ridge", "kind"] == "model" and t1.loc["ridge", "promoted"] == 0
    assert t1.loc["ridge", "gain_over_best_baseline"] == pytest.approx(-0.03)
    assert pd.isna(t1.loc["last_month", "gain_over_best_baseline"])
    assert t1.loc["ridge", "coverage_90"] == pytest.approx(0.95)
    assert (t1["metric"] == "mae_pp").all() and (t1["higher_is_better"] == 0).all()
    t2 = frame[frame["task"] == "T2"].set_index("method")
    assert t2.loc["logistic", "months_better"] == 46 and t2.loc["logistic", "serving"] == 1
    assert t2.loc["logistic", "bias_vs_actual"] == pytest.approx(0.05)
    assert t2.loc["market", "kind"] == "baseline" and t2.loc["market", "serving"] == 0
    t3 = frame[frame["task"] == "T3"].set_index("method")
    assert (t3["metric"] == "pr_auc").all() and (t3["higher_is_better"] == 1).all()
    assert t3.loc["logistic", "precision"] == pytest.approx(0.49)
    assert t3.loc["small_segment", "is_best_baseline"] == 1


def test_backtest_shares_are_fractions_and_levels_are_labelled():
    frame = backtest_batch_frame(_tiny_results())
    assert list(frame.columns) == BACKTEST_COLUMNS
    national = frame[frame["level"] == "national"].iloc[0]
    assert national["predicted_share"] == pytest.approx(0.029)  # percentage points / 100
    assert national["lo90_share"] == pytest.approx(0.026)
    assert pd.isna(national["specialty_name"]) and pd.isna(national["visits"])
    segment = frame[frame["level"] == "specialty"].iloc[0]
    assert segment["specialty_name"] == "RHEUMATOLOGY" and segment["visits"] == 1000.0
    assert pd.isna(segment["lo90_share"])


def test_the_real_results_reach_the_tables_with_one_serving_method_per_scenario():
    results = load_results(RESULTS_JSON)
    scores = model_scores_frame(results)
    expected = (
        sum(len(d["scores"]) for t in results["T1"].values() for d in t["scenarios"].values())
        + sum(len(d["baseline_log_loss"]) + len(d["models"])
              for t in results["T2"].values() for d in t["scenarios"].values())
        + sum(len(d["methods"]) for t in results["T3"].values() for d in t["scenarios"].values())
    )
    assert len(scores) == expected
    serving = scores.groupby(["task", "horizon", "scenario"])["serving"].sum()
    assert (serving == 1).all()
    best = scores.groupby(["task", "horizon", "scenario"])["is_best_baseline"].sum()
    assert (best == 1).all()
    # the headline numbers of the results document
    main = scores[(scores["scenario"] == "main")].set_index(["task", "horizon", "method"])
    assert main.loc[("T1", 1, "last_month"), "score"] == pytest.approx(0.1246, abs=5e-4)
    assert main.loc[("T2", 1, "logistic"), "promoted"] == 1
    assert main.loc[("T2", 6, "logistic"), "promoted"] == 0
    assert main.loc[("T2", 1, "logistic"), "serving"] == 1
    assert main.loc[("T2", 6, "last_month"), "serving"] == 1
    assert np.isfinite(main.loc[("T3", 1, "logistic"), "score"])
    backtest = backtest_batch_frame(results)
    assert len(backtest) == len(results["demonstration"]["national"]) + len(
        results["demonstration"]["segment"]
    )
    assert (backtest["predicted_share"].between(0, 1)).all()


def test_write_replaces_the_tables_and_leaves_the_rest_alone(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'w.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE other (x INTEGER)"))
        conn.execute(text("INSERT INTO other VALUES (1)"))
    first = write_model_tables(engine)
    second = write_model_tables(engine)  # idempotent: replaced, not appended
    assert first == second and first["model_scores"] > 0 and first["backtest_batch"] > 0
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM model_scores")).scalar()
        assert count == first["model_scores"]
        assert conn.execute(text("SELECT COUNT(*) FROM other")).scalar() == 1


def test_an_empty_results_file_is_an_error(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("{}")
    engine = create_engine(f"sqlite:///{(tmp_path / 'w.db').as_posix()}")
    with pytest.raises(ValueError):
        write_model_tables(engine, path)
