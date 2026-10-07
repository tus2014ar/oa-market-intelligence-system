"""Tests for the mix-vs-adoption decomposition (src/.../analysis/decomposition.py).

Zilretta's share of the category total is, in any window, the sum over groups (specialties)
of (the group's share of category visits) x (Zilretta's share within the group). A change in
that total splits exactly into a MIX effect (the weights moved) and a RATE effect (the
within-group shares moved). The hand-built examples below have known answers.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.analysis.decomposition import (
    bootstrap_decomposition,
    decompose,
    decompose_chain,
    group_counts,
    year_windows,
)


def _counts(rows):
    """rows: (month_id, group, zilretta, category)."""
    return pd.DataFrame(rows, columns=["month_id", "group", "zilretta", "category"])


# Window A: X has 100 visits at 20% and Y 100 at 10%  -> share 30/200 = 15%
A = _counts([(1, "X", 20, 100), (1, "Y", 10, 100)])


def test_a_pure_mix_change_is_all_mix():
    # same rates (X 20%, Y 10%), but visits move from X to Y: X 60, Y 140 -> 12 + 14 = 26/200
    b = _counts([(2, "X", 12, 60), (2, "Y", 14, 140)])
    result = decompose(A, b)
    assert result["total"] == pytest.approx(0.13 - 0.15)
    assert result["mix"] == pytest.approx(-0.02)  # X: (0.3-0.5)*0.2 + Y: (0.7-0.5)*0.1
    assert result["rate"] == pytest.approx(0.0)
    table = result["by_group"].set_index("group")
    assert table.loc["X", "mix"] == pytest.approx(-0.04)
    assert table.loc["Y", "mix"] == pytest.approx(0.02)


def test_a_pure_rate_change_is_all_rate():
    # same visits, X's rate rises from 20% to 30%
    b = _counts([(2, "X", 30, 100), (2, "Y", 10, 100)])
    result = decompose(A, b)
    assert result["total"] == pytest.approx(0.20 - 0.15)
    assert result["rate"] == pytest.approx(0.05)
    assert result["mix"] == pytest.approx(0.0)


def test_mix_plus_rate_equals_the_total_change_exactly_when_both_move():
    b = _counts([(2, "X", 9, 50), (2, "Y", 30, 250)])
    result = decompose(A, b)
    assert result["mix"] + result["rate"] == pytest.approx(result["total"], abs=1e-12)
    assert result["total"] == pytest.approx(39 / 300 - 30 / 200)


def test_the_identity_holds_for_random_data_with_many_groups():
    rng = np.random.default_rng(0)
    for _ in range(20):
        groups = [f"g{i}" for i in range(15)]
        a = _counts([(1, g, int(rng.integers(0, 50)), int(rng.integers(50, 400))) for g in groups])
        b = _counts([(2, g, int(rng.integers(0, 50)), int(rng.integers(50, 400))) for g in groups])
        result = decompose(a, b)
        assert result["mix"] + result["rate"] == pytest.approx(result["total"], abs=1e-12)
        assert result["by_group"]["total"].sum() == pytest.approx(result["total"], abs=1e-12)


def test_a_group_that_appears_only_in_the_later_window_counts_as_mix():
    # Z enters in B with 100 visits at 40%: all of its contribution is mix, none is rate
    b = _counts([(2, "X", 20, 100), (2, "Y", 10, 100), (2, "Z", 40, 100)])
    result = decompose(A, b)
    assert result["mix"] + result["rate"] == pytest.approx(result["total"], abs=1e-12)
    z = result["by_group"].set_index("group").loc["Z"]
    assert z["rate"] == pytest.approx(0.0)
    assert z["mix"] > 0


def test_a_group_that_disappears_is_handled_the_same_way():
    b = _counts([(2, "X", 20, 100)])
    result = decompose(A, b)
    assert result["mix"] + result["rate"] == pytest.approx(result["total"], abs=1e-12)
    assert result["by_group"].set_index("group").loc["Y", "rate"] == pytest.approx(0.0)


def test_windows_are_consecutive_twelve_month_blocks():
    months = [m for y in range(2019, 2026) for m in range(y * 100 + 1, y * 100 + 13)]
    months = [m for m in months if 201908 <= m <= 202507]
    windows = year_windows(months)
    assert len(windows) == 6
    assert windows[0][0] == 201908 and windows[0][-1] == 202007
    assert windows[-1][0] == 202408 and windows[-1][-1] == 202507
    assert all(len(w) == 12 for w in windows)
    with pytest.raises(ValueError, match="multiple of 12"):
        year_windows(months[:-1])


def test_the_chain_covers_each_consecutive_pair_and_first_against_last():
    rows = []
    for window in range(3):
        for month in range(2):
            month_id = window * 100 + month
            rows += [
                (month_id, "X", 10 + 5 * window, 100),
                (month_id, "Y", 10, 100 + 40 * window),
            ]
    counts = _counts(rows)
    windows = [[0, 1], [100, 101], [200, 201]]
    chain = decompose_chain(counts, windows)
    assert list(chain["comparison"]) == ["1 to 2", "2 to 3", "1 to 3"]
    first_to_last = chain.set_index("comparison").loc["1 to 3"]
    assert first_to_last["mix"] + first_to_last["rate"] == pytest.approx(
        first_to_last["total"], abs=1e-12
    )


def test_bootstrap_is_reproducible_and_collapses_when_every_month_is_identical():
    rows_a = [(m, "X", 20, 100) for m in (1, 2, 3)] + [(m, "Y", 10, 100) for m in (1, 2, 3)]
    rows_b = [(m, "X", 12, 60) for m in (4, 5, 6)] + [(m, "Y", 14, 140) for m in (4, 5, 6)]
    counts = _counts(rows_a + rows_b)
    first = bootstrap_decomposition(counts, [1, 2, 3], [4, 5, 6], n_boot=100, seed=1)
    again = bootstrap_decomposition(counts, [1, 2, 3], [4, 5, 6], n_boot=100, seed=1)
    assert first == again
    # identical months in a window: resampling changes nothing, so the interval is the point
    assert first["mix"]["low"] == pytest.approx(-0.02) == pytest.approx(first["mix"]["high"])
    assert first["rate"]["low"] == pytest.approx(0.0, abs=1e-12)


def test_bootstrap_interval_brackets_the_estimate_when_months_differ():
    rng = np.random.default_rng(3)
    rows = []
    for m in range(1, 25):
        low, high = (15, 25) if m <= 12 else (5, 15)  # X's visit counts differ by window
        rows += [
            (m, "X", int(rng.integers(low, high)), int(rng.integers(80, 121))),
            (m, "Y", int(rng.integers(5, 15)), int(rng.integers(80, 121))),
        ]
    counts = _counts(rows)
    estimate = decompose(counts[counts.month_id <= 12], counts[counts.month_id > 12])
    result = bootstrap_decomposition(counts, list(range(1, 13)), list(range(13, 25)), n_boot=300)
    for key in ("total", "mix", "rate"):
        assert result[key]["low"] <= estimate[key] <= result[key]["high"]
        assert result[key]["high"] > result[key]["low"]


def test_group_counts_reconcile_to_the_gold_totals(tiny_gold_engine):
    for by in ("specialty", "age_gender", "segment"):
        counts = group_counts(tiny_gold_engine, by=by)
        assert counts["zilretta"].sum() == 30
        assert counts["category"].sum() == 300
    assert set(group_counts(tiny_gold_engine, by="specialty")["group"]) == {"A", "B"}
    assert "65 TO 74 | FEMALE" in set(group_counts(tiny_gold_engine, by="age_gender")["group"])
    with pytest.raises(ValueError, match="by must be one of"):
        group_counts(tiny_gold_engine, by="payer")
