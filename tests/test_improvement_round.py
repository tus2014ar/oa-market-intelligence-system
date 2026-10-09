"""Tests for the modelling improvement round (DL-73): M1 offset layer, M4 conformal intervals,
M5 CUSUM monitor and decision metric. Hand-computed answers, planted effects, leakage rewrites."""

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit, logit

from oa_market_intelligence.modeling import improvement_round as ir
from oa_market_intelligence.modeling.improvement_round import (
    aci_forecast,
    apply_offset_layer,
    bias,
    choose_threshold,
    conformal_quantile,
    cusum_alarm_positions,
    decision_metric,
    detection_experiment,
    fit_offset,
    judge_m1,
    judge_m4_task_a,
    judge_m4_task_b,
    segment_intervals,
    simulate_arl,
    standardized_changes,
    top_overlap,
)


def month_ids(n, start=(2019, 8)):
    out, (year, month) = [], start
    for _ in range(n):
        out.append(year * 100 + month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


# ---------- the plan's numbers ----------


def test_the_constants_are_the_plans():
    assert (ir.OFFSET_WINDOW, ir.OFFSET_MIN_PRIOR, ir.M1_LOWER_PERCENTILE) == (12, 3, 5.0)
    assert ir.BIAS_TOLERANCE == 0.03
    assert (ir.ACI_GAMMA, ir.ACI_ALPHA, ir.WIDTH_CUT) == (0.02, 0.10, 0.10)
    assert (ir.SEGMENT_WARMUP, ir.SEGMENT_COVERAGE_TOLERANCE) == (3, 0.03)
    assert ir.BUCKET_COVERAGE_TOLERANCE == 0.05
    assert (ir.CUSUM_K, ir.H_GRID, ir.ARL_MIN) == (0.5, (3, 4, 5, 6, 8), 48)
    assert ir.DRIFTS == (0.05, 0.1, 0.2, 0.3) and ir.PASS_DRIFT == 0.2
    assert ir.PASS_DETECTION == 0.80 and ir.DETECTION_HORIZONS == (12, 6)
    assert (ir.TOP_K, ir.MIN_VISITS_RANK) == (10, 100)


def test_the_plan_document_states_the_same_rules():
    from pathlib import Path

    plan = (
        Path(__file__).resolve().parents[1] / "docs" / "modelling_improvement_plan.md"
    ).read_text("utf-8")
    for text in ("5th percentile", "{3, 4, 5, 6, 8}", "gamma 0.02", "0.2 pp a month", "top 10"):
        assert text in plan


# ---------- M1: the offset layer ----------


def test_the_offset_recovers_a_planted_shift():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.01, 0.1, 400)
    t = np.full(400, 200.0)
    z = t * expit(logit(p) + 0.37)  # expected counts under a shift of 0.37 on the logit scale
    assert fit_offset(z, t, logit(p)) == pytest.approx(0.37, abs=1e-6)


def test_the_offset_edge_cases():
    assert fit_offset([], [], []) == 0.0
    assert fit_offset([0.0, 0.0], [50.0, 50.0], logit(np.array([0.02, 0.03]))) == -10.0


def _preds(months=30, segments=40, factor=1.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    true = rng.uniform(0.01, 0.06, segments)
    for m in month_ids(months):
        t = rng.integers(80, 400, segments).astype(float)
        z = rng.binomial(t.astype(int), true).astype(float)
        rows.append(
            pd.DataFrame(
                {
                    "month_id": m,
                    "segment": [f"s{i}" for i in range(segments)],
                    "p": np.clip(true * factor, 1e-4, 0.9),
                    "z": z,
                    "t": t,
                    "seg_log_visits_lag1": np.log1p(rng.integers(30, 400, segments)),
                    "market_share_lag1": float(true.mean()),
                    "y_market": float(true.mean()),
                    "seg_share_lag1": true,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def test_bias_is_the_visit_weighted_predicted_over_observed_share_minus_one():
    frame = pd.DataFrame({"p": [0.02, 0.04], "z": [2.0, 2.0], "t": [100.0, 100.0]})
    assert bias(frame) == pytest.approx((0.02 * 100 + 0.04 * 100) / 4 - 1)


def test_the_layer_leaves_the_first_three_months_alone_and_uses_a_twelve_month_window():
    preds = _preds(months=20, factor=1.2)
    corrected, deltas = apply_offset_layer(preds)
    months = sorted(preds["month_id"].unique())
    assert (deltas.loc[months[:3]] == 0).all() and (deltas.loc[months[3:]] != 0).all()
    first = preds[preds["month_id"] == months[0]]["p"].to_numpy()
    assert np.allclose(corrected[corrected["month_id"] == months[0]]["p"], first)
    window = preds[preds["month_id"].isin(months[8:20][:12])]  # months 8..19 for the last month
    last = months[19]
    prior = preds[preds["month_id"].isin(months[7:19])]
    expected = fit_offset(prior["z"], prior["t"], logit(prior["p"].clip(1e-6, 1 - 1e-6)))
    assert deltas.loc[last] == pytest.approx(expected)
    assert len(window) > 0


def test_the_layer_uses_nothing_from_the_month_it_corrects_or_later():
    preds = _preds(months=24, factor=1.1)
    base, _ = apply_offset_layer(preds)
    cut = sorted(preds["month_id"].unique())[15]
    changed = preds.copy()
    later = changed["month_id"] >= cut
    changed.loc[later, "z"] = changed.loc[later, "z"] * 3 + 5
    changed.loc[later, "p"] = changed.loc[later, "p"] / 2
    again, _ = apply_offset_layer(changed)
    # the offset of the cut month depends on earlier months only, so its correction of the
    # *unchanged* raw predictions of that month cannot move
    row = lambda frame: frame[frame["month_id"] == cut].reset_index(drop=True)  # noqa: E731
    delta_base = logit(row(base)["p"]) - logit(preds[preds["month_id"] == cut]["p"].to_numpy())
    delta_again = logit(row(again)["p"]) - logit(
        changed[changed["month_id"] == cut]["p"].to_numpy()
    )
    assert np.allclose(delta_base, delta_again)
    before = base[base["month_id"] < cut]
    assert np.allclose(before["p"], again[again["month_id"] < cut]["p"])


def test_a_leaky_layer_would_be_caught_by_the_same_rewrite():
    preds = _preds(months=24, factor=1.1)
    cut = sorted(preds["month_id"].unique())[15]

    def leaky_delta(frame):  # includes the month itself in its own window
        window = frame[frame["month_id"] <= cut].tail(40 * 12)
        return fit_offset(window["z"], window["t"], logit(window["p"].clip(1e-6, 1 - 1e-6)))

    changed = preds.copy()
    changed.loc[changed["month_id"] == cut, "z"] = 0.0
    assert leaky_delta(preds) != pytest.approx(leaky_delta(changed))


def test_a_biased_model_is_corrected_and_an_unbiased_one_is_not_improved():
    biased = judge_m1(_preds(months=36, factor=1.25), n_boot=400)
    assert biased["bias_before"] > 0.15 and abs(biased["bias_after"]) <= 0.03
    assert biased["low"] > 0 and biased["passes"] and biased["months_better"] > 20
    fair = judge_m1(_preds(months=36, factor=1.0), n_boot=400)
    assert fair["low"] <= 0 and not fair["passes"]
    assert set(fair["bias_by_size"]) == {"<50", "50-100", "100-300", ">300"}


# ---------- M4: conformal quantile and the national interval ----------


def test_the_conformal_quantile_is_the_rank_the_theory_gives():
    scores = np.arange(1.0, 10.0)
    assert conformal_quantile(scores, 0.10) == 9.0  # ceil(10 x 0.9) = 9
    assert conformal_quantile(scores, 0.50) == 5.0
    assert conformal_quantile(scores, 0.05) == 9.0  # rank 10 exceeds n: the largest
    assert conformal_quantile(scores, 1.2) == 0.0  # rank below one: no width


def test_the_aci_interval_and_its_update_match_a_hand_computation():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 4.0], index=month_ids(6))
    out = aci_forecast(series, min_train=3)
    first = out.iloc[0]  # forecasts month 4 (value 4.0) from [1, 2, 3]: scores [1, 1]
    assert first["point"] == 3.0 and first["alpha"] == 0.10
    assert (first["lo90"], first["hi90"]) == (2.0, 4.0)  # half-width 1, and 4.0 is inside
    second = out.iloc[1]
    assert second["alpha"] == pytest.approx(0.10 + 0.02 * 0.10)  # no miss: alpha rises
    last = out.iloc[2]  # month 6 (value 4.0): history [1,2,3,4,5], scores all 1
    assert last["point"] == 5.0 and (last["lo90"], last["hi90"]) == (4.0, 6.0)
    jump = pd.Series([1.0, 2.0, 3.0, 4.0, 9.0, 10.0], index=month_ids(6))
    missed = aci_forecast(jump, min_train=3)
    assert missed.iloc[1]["alpha"] == pytest.approx(0.102)  # month 5 (9.0) is a miss...
    assert missed.iloc[2]["alpha"] == pytest.approx(0.102 + 0.02 * (0.10 - 1))  # ...and lowers it


def test_the_aci_interval_for_a_month_never_depends_on_that_month_or_later():
    rng = np.random.default_rng(1)
    values = 2.5 + np.cumsum(rng.normal(scale=0.1, size=60))
    series = pd.Series(values, index=month_ids(60))
    base = aci_forecast(series)
    altered = series.copy()
    altered.iloc[45:] = altered.iloc[45:] + 5.0
    again = aci_forecast(altered)
    keep = base["month_id"] < series.index[45]
    cols = ["point", "lo90", "hi90", "alpha"]
    assert np.allclose(base.loc[keep, cols], again.loc[keep, cols])


def test_judge_m4_task_b_reports_the_gate_and_the_width_rule():
    rng = np.random.default_rng(3)
    series = pd.Series(2.5 + np.cumsum(rng.normal(scale=0.15, size=72)), index=month_ids(72))
    out = judge_m4_task_b(series, n_boot=300)
    assert out["n_test_months"] == 48
    assert {"ok", "low", "high"} <= set(out["coverage_gate"])
    assert out["passes"] == (out["coverage_gate"]["ok"] and out["width_cut"] >= 0.10)
    assert out["aci"]["width90"] > 0 and len(out["aci"]["coverage_interval"]) == 2


# ---------- M4: segment intervals ----------


def test_segment_intervals_are_calibrated_on_binomial_data_and_skip_the_warmup():
    preds = _preds(months=40, segments=60, factor=1.0, seed=7)
    scored = segment_intervals(preds)
    months = sorted(preds["month_id"].unique())
    assert scored["month_id"].min() == months[3]  # the first three months only fill the pool
    assert abs(scored["covered"].mean() - 0.90) < 0.05
    result = judge_m4_task_a(preds, n_boot=300)
    assert result["n_scored_months"] == len(months) - 3
    assert set(result["buckets"]) == {"<50", "50-100", "100-300", ">300"}
    assert result["passes"] == (result["overall_ok"] and result["buckets_ok"])


def test_segment_intervals_of_a_month_do_not_move_when_later_months_change():
    preds = _preds(months=30, segments=40, seed=9)
    base = segment_intervals(preds)
    cut = sorted(preds["month_id"].unique())[20]
    changed = preds.copy()
    later = changed["month_id"] >= cut
    changed.loc[later, "z"] = 0.0
    again = segment_intervals(changed)
    early = base["month_id"] < cut
    assert np.allclose(base.loc[early, "width"], again.loc[again["month_id"] < cut, "width"])
    assert np.allclose(base.loc[early, "covered"], again.loc[again["month_id"] < cut, "covered"])


# ---------- M5: CUSUM ----------


def test_standardised_changes_match_a_hand_computation_and_use_only_the_past():
    values = np.array([0.0, 1.0, 3.0, 6.0, 10.0])
    z = standardized_changes(values, min_train=3)
    assert np.isnan(z[:3]).all()
    sd = np.std([1.0, 2.0], ddof=1)  # the changes before position 3
    assert z[3] == pytest.approx(3.0 / sd)
    changed = values.copy()
    changed[4] = 100.0
    assert standardized_changes(changed, min_train=3)[3] == pytest.approx(z[3])


def test_the_cusum_recursion_alarms_and_restarts():
    assert cusum_alarm_positions([1, 1, 1, 1], h=1.2) == [2]  # 0.5, 1.0, 1.5 > 1.2, reset, 0.5
    assert cusum_alarm_positions([-1, -1, -1, -1], h=1.2) == [2]  # symmetric
    assert cusum_alarm_positions([0, 0, 0, 0], h=1.2) == []
    assert cusum_alarm_positions([np.nan, 1, 1, 1], h=1.2) == [3]


def test_the_no_drift_run_length_grows_with_the_threshold_and_respects_the_cap():
    changes = np.random.default_rng(0).normal(size=71)
    arl = simulate_arl(changes, n_runs=300, block_length=1, seed=0)
    lengths = [arl[h]["arl"] for h in ir.H_GRID]
    assert lengths == sorted(lengths) and lengths[-1] > lengths[0]
    assert max(lengths) <= ir.ARL_CAP
    assert simulate_arl(changes, n_runs=300, block_length=1, seed=0) == arl


def test_the_threshold_is_the_smallest_on_the_grid_that_reaches_the_minimum_run_length():
    arl = {3: {"arl": 30.0}, 4: {"arl": 50.0}, 5: {"arl": 100.0}}
    assert choose_threshold(arl) == 4
    assert choose_threshold({3: {"arl": 10.0}}) is None


def test_planted_drift_is_detected_and_a_flat_series_is_not():
    rng = np.random.default_rng(5)
    series = pd.Series(2.5 + rng.normal(scale=0.05, size=72), index=month_ids(72))
    rows = detection_experiment(series, h=5, drifts=(0.0, 0.3))
    rate = {(r["drift_pp_per_month"], r["horizon_months"]): r["detection_rate"] for r in rows}
    assert rate[(0.3, 12)] >= 0.8
    assert rate[(0.0, 12)] <= 0.2
    assert rate[(0.3, 6)] <= rate[(0.3, 12)]


# ---------- M5: the decision metric ----------


def test_the_top_overlap_matches_a_hand_computation():
    n = 14
    visits = np.full(n, 200.0)
    realised = np.linspace(0.0, 0.026, n)  # lower share = more headroom
    market = 0.03
    perfect = top_overlap(realised, realised, visits, visits, market, market)
    assert perfect == 1.0
    reversed_ = top_overlap(realised[::-1], realised, visits, visits, market, market)
    assert reversed_ == pytest.approx(6 / 10)  # 14 segments: the two ends share six of ten
    assert top_overlap(realised[:5], realised[:5], visits[:5], visits[:5], market, market) is None
    small = np.full(n, 50.0)  # nothing eligible
    assert top_overlap(realised, realised, small, visits, market, market) is None


def test_the_decision_metric_prefers_a_better_ranking_and_reports_an_interval():
    preds = _preds(months=30, segments=40, seed=11)
    result = decision_metric(preds, n_boot=300)
    assert result["n_months"] > 0 and result["k"] == 10
    assert 0.0 <= result["model_overlap"] <= 1.0 and 0.0 <= result["baseline_overlap"] <= 1.0
    assert len(result["difference_interval"]) == 2
    assert result["difference"] == pytest.approx(
        result["model_overlap"] - result["baseline_overlap"]
    )


def test_the_command_line_prints_help_without_running():
    assert ir.main([]) == 0


def test_months_helper_is_consecutive():
    ids = month_ids(14)
    assert ids[0] == 201908 and ids[5] == 202001 and ids[-1] == 202009
    assert len(set(ids)) == 14
    assert isinstance(ids, list) and ids == sorted(ids)
