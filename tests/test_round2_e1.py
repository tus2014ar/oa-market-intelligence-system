"""Tests for round 2, experiment E1 (src/.../modeling/round2/e1_bias.py, plan sections 4 and 10):
the correction factor by hand and its time guard, the clipping and the cap, the rolling window, the
pass rule and a small end-to-end run that must reproduce itself. Nothing here uses real results."""

import json

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import make_inputs, month_ids
from oa_market_intelligence.modeling import segment_models
from oa_market_intelligence.modeling.horizons import horizon_splits, month_index, pair_rows
from oa_market_intelligence.modeling.round2 import e1_bias as e1


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    """One setting for the logistic regression, so the end-to-end tests stay quick."""
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])


def _preds(n_months=30, over=1.2, seed=0):
    """Forecasts for two segments over `n_months` consecutive months, `over` times too high."""
    rng = np.random.default_rng(seed)
    months = month_ids(n_months)
    rows = []
    for month in months:
        for segment in ("a", "b"):
            t = int(rng.integers(300, 600))
            share = rng.uniform(0.03, 0.08)
            rows.append(
                {
                    "month_id": month,
                    "segment": segment,
                    "t": float(t),
                    "z": float(round(t * share)),
                    "p": min(0.99, over * round(t * share) / t),
                }
            )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ the correction factor


@pytest.mark.parametrize("horizon", [1, 3])
def test_the_factor_is_one_until_twelve_target_months_have_resolved(horizon):
    factors = e1.correction_factors(_preds(), horizon)
    # a month at position i has i - horizon + 1 resolved months behind it: 12 are needed
    first_active = 11 + horizon
    assert (factors.iloc[:first_active] == 1.0).all()
    assert (factors.iloc[first_active:] != 1.0).all() and len(factors) == 30


def test_a_constant_over_forecast_is_undone_exactly_once_the_factor_is_active():
    preds = _preds(over=1.2)
    factors = e1.correction_factors(preds, 1)
    assert factors.iloc[13:].round(6).eq(round(1 / 1.2, 6)).all()
    corrected = e1.apply_factors(preds, factors)
    active = corrected["month_id"].isin(factors.index[13:])
    before, after = (
        e1.bias(corrected.assign(p=corrected["p_raw"])[active]),
        e1.bias(corrected[active]),
    )
    assert before == pytest.approx(0.2, abs=1e-3) and abs(after) < 1e-3


def test_the_factor_is_clipped_to_the_registered_range_and_a_share_never_exceeds_one():
    too_high = e1.correction_factors(_preds(over=2.5), 1)
    too_low = e1.correction_factors(_preds(over=0.4), 1)
    assert too_high.iloc[13:].eq(0.7).all() and too_low.iloc[13:].eq(1.3).all()
    preds = _preds(over=0.4).assign(p=0.9)
    capped = e1.apply_factors(preds, pd.Series(1.3, index=preds["month_id"].unique()))
    assert (capped["p"] == 1.0).all() and (capped["p_raw"] == 0.9).all()


@pytest.mark.parametrize("horizon", [1, 3])
def test_the_factor_for_a_month_uses_only_forecasts_resolved_by_its_origin(horizon):
    preds = _preds(n_months=30, over=1.1)
    factors = e1.correction_factors(preds, horizon)
    month = factors.index[22]
    origin = int(month_index(month)) - horizon
    resolved = [m for m in preds["month_id"].unique() if int(month_index(m)) <= origin][-12:]
    part = preds[preds["month_id"].isin(resolved)]
    expected = np.clip(part["z"].sum() / (part["t"] * part["p"]).sum(), 0.7, 1.3)
    assert factors[month] == pytest.approx(expected)
    assert len(resolved) == 12 and month not in resolved


@pytest.mark.parametrize("horizon", [1, 3])
def test_rewriting_everything_after_the_origin_does_not_move_the_factor(horizon):
    preds = _preds(n_months=30)
    factors = e1.correction_factors(preds, horizon)
    month = factors.index[24]
    origin = int(month_index(month)) - horizon
    rng = np.random.default_rng(7)
    later = month_index(preds["month_id"].to_numpy()) > origin
    tampered = preds.copy()
    tampered.loc[later, "z"] = rng.integers(0, 50, later.sum()).astype(float)
    tampered.loc[later, "t"] = rng.integers(1, 900, later.sum()).astype(float)
    tampered.loc[later, "p"] = rng.uniform(0, 1, later.sum())
    assert e1.correction_factors(tampered, horizon)[month] == factors[month]


# ------------------------------------------------------------------ the rolling window


@pytest.mark.parametrize("horizon", [1, 3])
def test_the_rolling_window_keeps_only_the_last_36_months_of_outcomes(horizon):
    rows, outcomes, _ = make_inputs(n_months=44)
    paired = pair_rows(rows, outcomes, horizon)
    plain = list(horizon_splits(horizon)(paired))
    rolling = list(e1.rolling_splits(horizon, months=12)(paired))  # a short window, to bind early
    assert [t[0] for t in rolling] == [t[0] for t in plain]  # the same test months
    narrowed = False
    for (month, train, test), (_, wide, same_test) in zip(rolling, plain, strict=True):
        origin = int(month_index(month)) - horizon
        outcome = month_index(paired["month_id"].to_numpy())
        assert np.array_equal(test, same_test) and set(train) <= set(wide)
        assert outcome[train].max() <= origin and outcome[train].min() >= origin - 11
        narrowed |= len(train) < len(wide)
    assert narrowed  # the window really did cut something


def test_before_the_window_fills_the_rolling_splits_equal_round_1s():
    rows, outcomes, _ = make_inputs(n_months=44)
    paired = pair_rows(rows, outcomes, 1)
    plain = list(horizon_splits(1)(paired))
    rolling = list(
        e1.rolling_splits(1)(paired)
    )  # 36 months: 44 months of data never reach it for long
    for (month_a, train_a, _), (month_b, train_b, _) in zip(plain[:5], rolling[:5], strict=True):
        assert month_a == month_b and np.array_equal(train_a, train_b)


# ------------------------------------------------------------------ the pass rule


def test_the_pass_rule_by_hand_at_each_threshold():
    ok = dict(low_vs_raw=0.0, raw_loss=0.1127, low_vs_baseline=0.0001)
    assert (
        e1.checks(0.025, horizon=1, **ok)["passes"]
        and not e1.checks(0.0251, horizon=1, **ok)["passes"]
    )
    assert (
        e1.checks(-0.05, horizon=3, **ok)["passes"]
        and not e1.checks(0.0501, horizon=3, **ok)["passes"]
    )
    edge = -0.001 * 0.1127  # exactly 0.1% worse is still allowed; a hair more is not

    def run(low_vs_raw=0.0, low_vs_baseline=0.0001):
        return e1.checks(
            0.0, low_vs_raw=low_vs_raw, raw_loss=0.1127, low_vs_baseline=low_vs_baseline, horizon=1
        )

    assert run(edge)["not_less_accurate"] and not run(edge - 1e-9)["not_less_accurate"]
    assert not run(low_vs_baseline=0.0)["still_beats_baseline"]  # the gain must be above zero
    assert not run(low_vs_baseline=-1e-6)["passes"] and run()["passes"]


def test_the_round_has_seven_tests_and_one_strict_percentile():
    assert e1.ROUND_TESTS == 7 and e1.ROUND_PERCENTILE == pytest.approx(0.05 / 7 * 100)
    assert (
        e1.BIAS_LIMIT == {1: 0.025, 3: 0.05} and e1.WINDOW_MONTHS == 12 and e1.ROLLING_MONTHS == 36
    )
    assert (e1.FACTOR_LOW, e1.FACTOR_HIGH) == (0.7, 1.3)


def test_the_uncorrected_walk_must_equal_round_1s_stored_score():
    from oa_market_intelligence.modeling.segment_eval import share_month_stats

    preds = _preds()
    stats = share_month_stats(preds)
    from oa_market_intelligence.modeling.segment_eval import pooled

    e1.assert_matches_round1(stats, pooled(stats, "log_loss"))
    with pytest.raises(ValueError, match="differ from round 1"):
        e1.assert_matches_round1(stats, pooled(stats, "log_loss") + 1e-6)


# ------------------------------------------------------------------ a small end-to-end run


@pytest.fixture(scope="module")
def small_run():
    with (
        pytest.MonkeyPatch.context() as patch
    ):  # restored afterwards: other tests use the real grid
        patch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
        rows, outcomes, _ = make_inputs(n_months=44)
        yield run(rows, outcomes)


def run(rows, outcomes, **kwargs):
    return e1.run_e1(rows, outcomes, 1, scenarios=("main",), n_boot=60, seed=0, **kwargs)


def _plain(result):
    keep = {k: v for k, v in result.items() if not k.startswith("_")}
    return json.dumps(keep, sort_keys=True, default=float)


def test_the_run_has_both_variants_the_three_checks_and_a_baseline(small_run):
    main = small_run["scenarios"]["main"]
    assert set(main["variants"]) == {"e1a", "e1b"} and set(main["log_loss"]) == {
        "base",
        "raw",
        "e1a",
        "e1b",
    }
    for variant in main["variants"].values():
        assert {"bias_within_limit", "not_less_accurate", "still_beats_baseline", "passes"} <= set(
            variant
        )
        assert variant["versus_uncorrected"]["n_valid"] > 0
    assert main["n_test_months"] > e1.WINDOW_MONTHS and main["factor_active_months"] > 0


def test_the_corrected_forecasts_are_valid_shares_and_the_raw_ones_are_kept(small_run):
    preds = small_run["_preds"]
    assert (preds["e1a"]["p"] <= 1.0).all() and (preds["e1a"]["p"] >= 0).all()
    assert np.allclose(preds["e1a"]["p_raw"], preds["raw"]["p"])
    assert len(preds["e1a"]) == len(preds["raw"]) == len(preds["e1b"]) == len(preds["base"])


def test_the_run_reproduces_itself_exactly(small_run):
    rows, outcomes, _ = make_inputs(n_months=44)
    assert _plain(run(rows, outcomes)) == _plain(small_run)


def test_a_wrong_stored_score_stops_the_run():
    rows, outcomes, _ = make_inputs(n_months=44)
    with pytest.raises(ValueError, match="differ from round 1"):
        run(rows, outcomes, stored_log_loss=0.5)


def test_only_the_registered_horizons_can_be_run():
    rows, outcomes, _ = make_inputs(n_months=44)
    with pytest.raises(ValueError, match="horizons"):
        e1.run_e1(rows, outcomes, 6, scenarios=("main",), n_boot=10)
