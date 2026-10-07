"""Tests for the Q1 trend and change-point analysis (src/.../analysis/trend.py).

Synthetic series with a known break let us check the method finds it, finds nothing when
nothing is there, respects the minimum segment length, and gives reproducible intervals.
"""

import numpy as np
import pytest

from oa_market_intelligence.analysis.trend import (
    EVENTS,
    bootstrap_break_locations,
    break_tests,
    category_shares,
    events_overlapping,
    fit_breaks,
    select_breaks,
)

N = 72


def _noise(seed, sd=0.1, n=N):
    return np.random.default_rng(seed).normal(0, sd, n)


def _step(at=36, low=1.0, high=2.0, seed=1):
    y = np.where(np.arange(N) < at, low, high).astype(float)
    return y + _noise(seed)


FAST = dict(n_null=99, seed=0)


def _ar1(seed, phi, sd=0.1, n=N):
    rng = np.random.default_rng(seed)
    e = rng.normal(0, sd, n)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


def test_a_clear_level_shift_is_found_where_it_was_put_in_nearly_every_series():
    """Rates over 20 simulated series, not single seeds: a lone seed can add a spurious second
    break about 3 to 5% of the time, which is what a 5% test is supposed to do."""
    fits = [select_breaks(_step(at=36, seed=1000 + i), n_null=99, seed=i) for i in range(20)]
    exactly_one = [fit for fit in fits if fit.n_breaks == 1]
    assert len(exactly_one) >= 18
    assert all(abs(fit.breaks[0] - 36) <= 2 for fit in fits if fit.n_breaks >= 1)


def test_a_plain_trend_with_noise_has_no_break():
    y = 1.0 + 0.01 * np.arange(N) + _noise(2)
    assert select_breaks(y, **FAST).n_breaks == 0


def test_two_shifts_are_found():
    y = np.where(np.arange(N) < 24, 1.0, np.where(np.arange(N) < 48, 2.0, 1.0)) + _noise(3)
    fit = select_breaks(y, **FAST)
    assert fit.n_breaks == 2
    assert abs(fit.breaks[0] - 24) <= 2 and abs(fit.breaks[1] - 48) <= 2


def test_the_false_positive_rate_is_near_the_nominal_five_percent():
    """The reason BIC was dropped: with no break in the data, how often is one reported?
    Checked for independent noise and for month-to-month dependent noise (as real series
    have). Nominal is 5%; with 30 simulated series per case, 20% is a generous ceiling."""
    for phi in (0.0, 0.5):
        hits = sum(
            select_breaks(
                1.0 + 0.01 * np.arange(N) + _ar1(100 + i, phi), n_null=99, seed=i
            ).n_breaks
            > 0
            for i in range(30)
        )
        assert hits / 30 <= 0.20, f"phi={phi}: {hits}/30 false positives"


def test_a_real_shift_is_detected_in_nearly_every_simulated_series():
    found = sum(
        select_breaks(
            _step(at=36, low=1.0, high=1.5, seed=200 + i), n_null=99, seed=i
        ).n_breaks
        >= 1
        for i in range(10)
    )
    assert found >= 9


def test_tests_run_in_order_and_stop_at_the_first_non_significant_one():
    tests = break_tests(_step(at=36, seed=1000), max_breaks=2, **FAST)
    assert [t["breaks"] for t in tests] == list(range(1, len(tests) + 1))
    assert tests[0]["p_value"] <= 0.05
    assert all(0 < t["p_value"] <= 1 for t in tests)
    assert all(t["p_value"] < 0.05 for t in tests[:-1])  # only the last may be non-significant


def test_no_segment_is_shorter_than_the_minimum():
    # a jump at month 6 is too early to be a segment of its own: it must not be reported
    y = np.where(np.arange(N) < 6, 5.0, 1.0) + _noise(4)
    fit = select_breaks(y, min_segment=12, **FAST)
    edges = [0, *fit.breaks, N]
    assert all(b - a >= 12 for a, b in zip(edges, edges[1:]))


def test_a_noiseless_step_is_exact_and_does_not_crash():
    y = np.array([0.0] * 4 + [1.0] * 4)
    fit = fit_breaks(y, n_breaks=1, min_segment=4)
    assert fit.breaks == (4,)
    assert fit.sse == pytest.approx(0.0, abs=1e-12)
    assert np.isfinite(select_breaks(np.tile(y, 9), min_segment=4, **FAST).bic)


def test_the_fitted_line_matches_a_hand_computed_segment_fit():
    y = np.array([1.0, 2.0, 3.0, 4.0, 10.0, 10.0, 10.0, 10.0])
    fit = fit_breaks(y, n_breaks=1, min_segment=4)
    assert fit.breaks == (4,)
    assert fit.fitted[:4] == pytest.approx([1.0, 2.0, 3.0, 4.0])
    assert fit.fitted[4:] == pytest.approx([10.0] * 4)


def test_location_intervals_cover_the_true_break_and_are_reproducible():
    y = _step(at=36)
    first = bootstrap_break_locations(y, 1, n_boot=200, seed=7)
    again = bootstrap_break_locations(y, 1, n_boot=200, seed=7)
    assert first == again
    low, high = first[0]
    assert low <= 36 <= high
    assert high - low <= 6  # a shift this clear is located tightly


def test_no_breaks_means_no_intervals():
    assert bootstrap_break_locations(_noise(5), 0, n_boot=10) == []


def test_category_shares_are_the_counts_over_the_three_category_total(tiny_gold_engine):
    shares = category_shares(tiny_gold_engine)
    first = shares.iloc[0]
    assert first["month_id"] == 201908
    assert first["branded_injectable"] == pytest.approx(0.10)
    assert first["generic_corticosteroid"] == pytest.approx(0.80)
    assert first["nsaid_otc"] == pytest.approx(0.10)
    totals = shares[["branded_injectable", "generic_corticosteroid", "nsaid_otc"]].sum(axis=1)
    assert totals.tolist() == pytest.approx([1.0, 1.0])


def test_events_are_compared_by_overlap_of_months():
    assert set(EVENTS) == {"COVID shock", "Mar to Jul 2024 volume dip"}
    assert events_overlapping((202002, 202004)) == ["COVID shock"]
    assert events_overlapping((202101, 202103)) == []
    assert events_overlapping((202402, 202503)) == ["Mar to Jul 2024 volume dip"]
