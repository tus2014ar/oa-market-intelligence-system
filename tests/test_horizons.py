"""Tests for the horizon harness (src/.../modeling/horizons.py): pairing, splits, the leakage
guards and the promotion helpers. Each has a checkable answer by hand."""

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import make_inputs
from oa_market_intelligence.modeling.horizons import (
    apply_scenario,
    assert_pairing,
    horizon_splits,
    month_id_from_index,
    month_index,
    outcome_table,
    pair_rows,
    promotion_percentile,
    serve_simplest,
)
from oa_market_intelligence.modeling.segment_eval import FutureLeakError


def _tiny_rows():
    months = [202311, 202312, 202401, 202402, 202403]
    rows = pd.DataFrame(
        {
            "month_id": months * 2,
            "segment": ["a"] * 5 + ["b"] * 5,
            "specialty_name": ["X"] * 5 + ["PEDIATRICS"] * 5,
            "month_sin": 0.0,
            "month_cos": 0.0,
            "feature": np.arange(10.0),
            "y_share": 0.5,
            "y_visits": 10.0,
            "y_market": 0.1,
        }
    )
    outcomes = pd.DataFrame(
        {
            "segment": ["a"] * 6 + ["b"] * 6,
            "month_id": [202311, 202312, 202401, 202402, 202403, 202404] * 2,
            "y_share": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06] + [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            "y_visits": 100.0,
            "y_market": 0.5,
        }
    )
    return rows, outcomes


def test_month_arithmetic_crosses_the_year():
    assert month_index(202401) - month_index(202312) == 1
    assert month_id_from_index(month_index(202312) + 2) == 202402
    assert month_id_from_index(month_index(202311) - 12) == 202211


def test_promotion_percentiles_are_the_plans():
    assert promotion_percentile(1) == pytest.approx(100 * 0.05 / 3)  # the 1.67th
    assert promotion_percentile(3) == pytest.approx(100 * 0.05 / 6)  # the 0.83th
    assert promotion_percentile(6) == pytest.approx(100 * 0.05 / 6)


def test_pairing_by_hand_for_h3_across_a_year_boundary():
    rows, outcomes = _tiny_rows()
    paired = pair_rows(rows, outcomes, 3)
    a = paired[paired["segment"] == "a"].set_index("row_month")
    # the row of 202311 has features through 202310 (its origin) and the outcome of 202401
    assert a.loc[202311, "origin_id"] == 202310
    assert a.loc[202311, "month_id"] == 202401
    assert a.loc[202311, "y_share"] == pytest.approx(0.03)
    # the row of 202401: outcome 202403
    assert a.loc[202401, "y_share"] == pytest.approx(0.05)
    assert a.loc[202402, "y_share"] == pytest.approx(0.06)  # outcome 202404
    # the row of 202403 would need the outcome of 202405: no such outcome, row dropped
    assert 202403 not in a.index
    assert (paired["feature"] >= 0).all()
    assert_pairing(paired, 3)


def test_calendar_columns_follow_the_outcome_month():
    rows, outcomes = _tiny_rows()
    paired = pair_rows(rows, outcomes, 3)
    row = paired[(paired["segment"] == "a") & (paired["row_month"] == 202311)].iloc[0]
    assert row["month_sin"] == pytest.approx(np.sin(2 * np.pi * 1 / 12))  # January, not November
    assert row["month_cos"] == pytest.approx(np.cos(2 * np.pi * 1 / 12))


def test_pairing_at_h1_reproduces_the_feature_table():
    rows, outcomes, _ = make_inputs()
    paired = pair_rows(rows, outcomes, 1)
    keys = ["segment", "month_id"]
    merged = paired.merge(rows[[*keys, "y_share", "y_visits"]], on=keys, suffixes=("", "_t"))
    assert len(merged) == len(rows) == len(paired)
    assert np.allclose(merged["y_share"], merged["y_share_t"])
    assert np.allclose(merged["y_visits"], merged["y_visits_t"])


def test_a_leaky_pairing_is_caught():
    rows, outcomes = _tiny_rows()
    paired = pair_rows(rows, outcomes, 3)
    leaky = paired.copy()
    leaky["month_id"] = month_id_from_index(month_index(leaky["origin_id"]) - 1)  # before origin
    with pytest.raises(FutureLeakError):
        assert_pairing(leaky, 3)
    wrong = paired.copy()
    wrong["month_id"] = month_id_from_index(month_index(wrong["origin_id"]) + 1)  # h = 1, not 3
    with pytest.raises(FutureLeakError):
        assert_pairing(wrong, 3)


@pytest.mark.parametrize("horizon", [1, 3, 6])
def test_splits_train_only_on_outcomes_at_or_before_the_origin(horizon):
    rows, outcomes, _ = make_inputs()
    paired = pair_rows(rows, outcomes, horizon)
    outcome_idx = month_index(paired["month_id"].to_numpy())
    splits = list(horizon_splits(horizon)(paired))
    assert splits
    first_origin = month_index(paired["row_month"].to_numpy()).min() + 23
    for test_month, train, test in splits:
        origin = month_index(test_month) - horizon
        assert origin >= first_origin
        assert outcome_idx[train].max() <= origin
        assert (outcome_idx[test] == month_index(test_month)).all()
        assert (month_index(paired["origin_id"].to_numpy())[test] == origin).all()


def test_test_month_counts_follow_the_first_origin_rule():
    rows, outcomes, _ = make_inputs(n_months=44)
    # the feature rows start at the third month; the first origin is the 24th scored month, so
    # the first test outcome month is that origin plus h
    counts = {h: len(list(horizon_splits(h)(pair_rows(rows, outcomes, h)))) for h in (1, 3, 6)}
    assert counts[1] == 44 - 2 - 24
    assert counts[3] == counts[1] - 2
    assert counts[6] == counts[1] - 5


def test_scenarios_remove_the_named_rows():
    rows, outcomes, _ = make_inputs()
    paired = pair_rows(rows, outcomes, 1)
    covid = apply_scenario(paired, "covid_removed")
    assert not covid["month_id"].between(202003, 202005).any()
    assert len(covid) < len(paired)
    pediatrics = apply_scenario(paired, "pediatrics_removed")
    assert "PEDIATRICS" not in set(pediatrics["specialty_name"])
    assert apply_scenario(paired, "main") is paired
    with pytest.raises(ValueError):
        apply_scenario(paired, "nope")


def test_outcome_table_comes_from_gold_and_drops_empty_months():
    seg = pd.DataFrame(
        {
            "month_id": [202001, 202001, 202002],
            "specialty_name": ["A", "B", "A"],
            "age_band": "40 TO 59",
            "gender": "MALE",
            "branded_injectable_visits": [5, 1, 0],
            "total_category_visits": [50, 50, 0],
        }
    )
    out = outcome_table(seg).set_index(["segment", "month_id"])
    assert out.loc[("A|40 TO 59|MALE", 202001), "y_share"] == pytest.approx(0.1)
    assert out.loc[("A|40 TO 59|MALE", 202001), "y_market"] == pytest.approx(6 / 100)
    assert ("A|40 TO 59|MALE", 202002) not in out.index  # no category visits, no share


def test_serve_simplest_prefers_the_simpler_unless_clearly_beaten():
    rng = np.random.default_rng(0)
    order = ("a", "b", "c")
    # lower is better; c is clearly best, b only ties a
    draws = {
        "a": 1.0 + rng.normal(0, 0.01, 500),
        "b": 1.0 + rng.normal(0, 0.01, 500),
        "c": 0.5 + rng.normal(0, 0.01, 500),
    }
    pooled = {"a": 1.0, "b": 0.999, "c": 0.5}
    out = serve_simplest(["a", "b", "c"], pooled, draws, order=order, lower_percentile=1.0,
                         higher_is_better=False)
    assert out["serving"] == "c"  # c beats the simpler models clearly
    tied = serve_simplest(["a", "b"], pooled, draws, order=order, lower_percentile=1.0,
                          higher_is_better=False)
    assert tied["serving"] == "a"  # b is not clearly better than a
    none = serve_simplest([], pooled, draws, order=order, lower_percentile=1.0,
                          higher_is_better=False)
    assert none["serving"] is None
    higher = serve_simplest(["a", "c"], {"a": 1.0, "c": 2.0},
                            {"a": 1 + rng.normal(0, 0.01, 500), "c": 2 + rng.normal(0, 0.01, 500)},
                            order=order, lower_percentile=1.0, higher_is_better=True)
    assert higher["serving"] == "c"
