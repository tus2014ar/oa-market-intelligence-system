"""Tests for round 2, experiment E2 (src/.../modeling/round2/e2_alert.py, plan sections 5 and 10):
the lasting-decline label by hand at each threshold, its pairing and time guard (with a planted leak
that must be caught), precision at 10 by hand with ties, the pass rule, and a small end-to-end run
that must reproduce itself. Nothing here uses real results."""

import json

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import make_inputs
from oa_market_intelligence.modeling import alerts, segment_models
from oa_market_intelligence.modeling.horizons import horizon_splits, month_index, pair_rows
from oa_market_intelligence.modeling.round2 import e2_alert as e2


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    """One setting for the logistic regression, so the model tests stay quick."""
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])


# ------------------------------------------------------------------ the label, by hand


def _case(origin, first, second, visits=100):
    return {
        "seg_share_lag1": origin,
        "seg_log_visits_lag1": np.log1p(visits),
        "y_share": first,
        "y_share_next": second,
    }


def test_the_lasting_decline_label_at_each_threshold():
    cases = {
        "both exactly at the thresholds": (_case(0.10, 0.07, 0.085), 1),
        "just inside both": (_case(0.10, 0.06, 0.08), 1),
        "first fall a hair short": (_case(0.10, 0.0701, 0.05), 0),
        "second fall a hair short": (_case(0.10, 0.05, 0.0851), 0),
        "a one-month dip that recovers": (_case(0.10, 0.05, 0.09), 0),
        "a fall that has not happened yet": (_case(0.10, 0.09, 0.05), 0),
    }
    frame = pd.DataFrame([c for c, _ in cases.values()])
    labelled = e2.add_lasting_label(frame)
    assert labelled["y_alert"].tolist() == [want for _, want in cases.values()]
    assert labelled["y_alert_round1"].tolist() == [1, 1, 0, 1, 1, 0]  # round 1's label alone


def test_eligibility_is_a_positive_origin_share_and_at_least_100_origin_visits():
    frame = pd.DataFrame(
        [
            _case(0.10, 0.05, 0.05, visits=100),  # eligible, exactly 100 visits
            _case(0.10, 0.05, 0.05, visits=99),  # one visit short
            _case(0.0, 0.0, 0.0, visits=500),  # a zero share cannot fall
        ]
    )
    labelled = e2.add_lasting_label(frame)
    assert len(labelled) == 1 and labelled["y_alert"].iloc[0] == 1


# ------------------------------------------------------------------ the pairing and the time guard


@pytest.mark.parametrize("horizon", [1, 3])
def test_the_label_resolves_one_month_after_the_first_outcome(horizon):
    rows, outcomes, _ = make_inputs(n_months=44)
    lasting = e2.lasting_decline_rows(rows, outcomes, horizon)
    gap = month_index(lasting["month_id"].to_numpy()) - month_index(lasting["origin_id"].to_numpy())
    assert (gap == horizon + 1).all()
    first = pair_rows(rows, outcomes, horizon)
    both = lasting.merge(first, on=["segment", "origin_id"], suffixes=("", "_first"))
    assert np.allclose(both["y_share"], both["y_share_first"])  # the first outcome is round 1's
    lookup = outcomes.set_index(["segment", "month_id"])["y_share"]
    sample = lasting.head(50)
    assert np.allclose(
        sample["y_share_next"],
        [lookup[(s, m)] for s, m in zip(sample["segment"], sample["month_id"])],
    )


@pytest.mark.parametrize("horizon", [1, 3])
def test_there_is_one_fewer_test_month_than_round_1(horizon):
    rows, outcomes, _ = make_inputs(n_months=44)
    lasting = e2.lasting_decline_rows(rows, outcomes, horizon)
    round1 = len(list(horizon_splits(horizon)(pair_rows(rows, outcomes, horizon))))
    assert len(list(horizon_splits(horizon + 1)(lasting))) == round1 - 1


@pytest.mark.parametrize("horizon", [1, 3])
def test_a_training_row_has_both_outcomes_at_or_before_the_origin(horizon):
    rows, outcomes, _ = make_inputs(n_months=44)
    lasting = e2.lasting_decline_rows(rows, outcomes, horizon)
    for test_month, train, _ in horizon_splits(horizon + 1)(lasting):
        origin = int(month_index(test_month)) - (horizon + 1)
        assert month_index(lasting["month_id"].to_numpy()[train]).max() <= origin
        first_outcome = month_index(lasting["month_id"].to_numpy()[train]) - 1
        assert first_outcome.max() <= origin - 1


def _tampered(base: pd.DataFrame, origin: int) -> pd.DataFrame:
    """Everything after the origin changed: both outcomes of rows resolving after it, and every
    feature of rows whose own origin is later."""
    rng = np.random.default_rng(99)
    out = base.copy()
    later = month_index(out["month_id"].to_numpy()) > origin
    out.loc[later, "y_share"] = rng.uniform(0, 1, later.sum())
    out.loc[later, "y_share_next"] = rng.uniform(0, 1, later.sum())
    out.loc[later, "y_visits"] = rng.integers(1, 1000, later.sum())
    later_origin = month_index(out["origin_id"].to_numpy()) > origin
    for column in (
        "seg_share_lag1",
        "seg_share_roll3",
        "market_share_lag1",
        "seg_prior_z",
        "seg_prior_t",
        "spec_prior_z",
        "spec_prior_t",
        "market_change_lag1",
    ):
        out.loc[later_origin, column] = out.loc[later_origin, column] * 7 + 3
    return out


def _scores_at(base, split_horizon, which):
    paired = e2.add_lasting_label(base)
    month, train, test = list(horizon_splits(split_horizon)(paired))[which]
    visible = paired.iloc[test].drop(columns=[c for c in paired.columns if c.startswith("y_")])
    scores, _ = e2.RankingClassifier()(paired.iloc[train], visible)
    return month, scores


@pytest.mark.parametrize("horizon", [1, 3])
def test_scores_at_an_origin_do_not_move_when_the_future_is_rewritten(horizon):
    rows, outcomes, _ = make_inputs(n_months=44)
    base = e2.lasting_decline_rows(rows, outcomes, horizon)
    month, before = _scores_at(base, horizon + 1, 4)
    origin = int(month_index(month)) - (horizon + 1)
    _, after = _scores_at(_tampered(base, origin), horizon + 1, 4)
    assert np.allclose(before, after, atol=1e-12)


def test_the_same_test_catches_a_leak_if_the_label_month_is_ignored():
    # a leaky split (treating the label as resolving at the first outcome) lets in training rows
    # whose second outcome is after the origin, so rewriting the future now moves the scores
    rows, outcomes, _ = make_inputs(n_months=44)
    base = e2.lasting_decline_rows(rows, outcomes, 1)
    month, before = _scores_at(base, 1, 4)  # horizon 1 instead of 2: the leak
    true_origin = int(month_index(month)) - 2  # the label really resolves two months on
    _, after = _scores_at(_tampered(base, true_origin), 1, 4)
    assert not np.allclose(before, after, atol=1e-12)


# ------------------------------------------------------------------ the measure, by hand


def _month(month, scores, labels, names=None):
    names = names or [f"s{i:02d}" for i in range(len(scores))]
    return pd.DataFrame({"month_id": month, "score": scores, "label": labels, "segment": names})


def test_precision_at_10_takes_the_ten_highest_scores_and_breaks_ties_by_name():
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.05, 0.05]
    labels = [1, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0]  # s09 is first of the three tied at 0.05
    names = [f"s{i:02d}" for i in range(12)]
    month_a = _month(202101, scores, labels, names)
    assert e2.precision_at_k(month_a).iloc[0] == pytest.approx(0.4)  # s09 is in, s10 and s11 out
    reversed_names = _month(202101, scores, labels, names[:9] + ["s11", "s10", "s09"])
    assert e2.precision_at_k(reversed_names).iloc[0] == pytest.approx(0.3)  # the tie went by name


def test_a_month_with_fewer_than_ten_rows_uses_all_of_them_and_months_are_averaged_equally():
    month_a = _month(202101, np.linspace(1, 0.1, 12), [1] * 5 + [0] * 7)
    month_b = _month(202102, [0.9, 0.5, 0.1, 0.0], [1, 0, 0, 0])
    both = pd.concat([month_a, month_b], ignore_index=True)
    series = e2.precision_at_k(both)
    assert series.tolist() == [pytest.approx(0.5), pytest.approx(0.25)]
    assert series.mean() == pytest.approx(0.375)  # each month counts once, whatever its size
    assert e2.base_rate(both) == pytest.approx(6 / 16)


def test_the_pass_rule_at_each_threshold():
    assert e2.checks(0.30, 0.15, 0.01)["passes"]  # exactly twice the base rate
    assert not e2.checks(0.299, 0.15, 0.01)["lift_at_least_2"]
    assert not e2.checks(0.30, 0.15, 0.0)["beats_best_comparator"]  # the gain must be above zero
    assert not e2.checks(0.30, 0.15, -0.01)["passes"]
    assert e2.LIFT_MINIMUM == 2.0 and e2.TOP_K == 10 and e2.HORIZONS == (1, 3)


def test_the_new_scores_by_hand():
    frame = pd.DataFrame(
        {
            "seg_share_roll3": [0.05, 0.05],
            "seg_share_lag1": [0.08, 0.04],
            "segment": ["a", "b"],
            "month_id": [202101, 202101],
        }
    )
    spike = e2.recent_spike_score(frame)
    assert spike[0] == pytest.approx(-0.625) and spike[1] == pytest.approx(-1.25)
    assert spike[0] > spike[1]  # the segment above its recent mean ranks riskier
    scores = e2.random_score(frame)
    assert np.array_equal(scores, e2.random_score(frame)) and 0 <= scores.min() <= scores.max() < 1
    assert np.array_equal(e2.random_score(frame.iloc[::-1])[::-1], scores)  # independent of order


# ---------------------------------------------------------- the judgement on planted methods


def _planted(n_months=24, n=30, seed=0):
    rng = np.random.default_rng(seed)
    frames = {
        "model": [],
        "recent_spike": [],
        "small_segment": [],
        "random": [],
        "round1_alert": [],
    }
    for month in range(202101, 202101 + n_months):
        labels = (rng.uniform(size=n) < 0.2).astype(int)
        names = [f"s{i:02d}" for i in range(n)]
        frames["model"].append(_month(month, labels + rng.uniform(0, 0.1, n), labels, names))
        for name in ("recent_spike", "small_segment", "random", "round1_alert"):
            frames[name].append(_month(month, rng.uniform(size=n), labels, names))
    return {name: pd.concat(parts, ignore_index=True) for name, parts in frames.items()}


def test_a_planted_perfect_ranking_passes_and_a_random_one_does_not():
    good = e2.judge(_planted(), 1, n_boot=300, seed=0)
    assert good["passes"] and good["precision_at_10"]["model"] > 0.6
    assert good["versus_best_comparator"]["low"] > 0 and good["lift_over_base_rate"]["model"] >= 2
    preds = _planted()
    preds["model"] = preds["random"].copy()  # a model that ranks no better than chance
    bad = e2.judge(preds, 1, n_boot=300, seed=0)
    assert not bad["passes"] and bad["best_comparator"] in e2.COMPARATORS


def test_methods_must_be_scored_on_the_same_months():
    a = pd.Series([0.1, 0.2], index=[202101, 202102])
    b = pd.Series([0.1, 0.2], index=[202101, 202103])
    with pytest.raises(ValueError, match="same test months"):
        e2.mean_bootstrap({"a": a, "b": b}, n_boot=5)


# ------------------------------------------------------------------ a small end-to-end run


def _run(**kwargs):
    rows, outcomes, _ = make_inputs(n_months=44)
    return e2.run_e2(rows, outcomes, 1, scenarios=("main",), n_boot=60, seed=0, **kwargs)


def _plain(result):
    return json.dumps(
        {k: v for k, v in result.items() if not k.startswith("_")}, sort_keys=True, default=float
    )


@pytest.fixture(scope="module")
def small_run():
    with (
        pytest.MonkeyPatch.context() as patch
    ):  # restored afterwards: other tests use the real grid
        patch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])
        yield _run()


def test_the_run_scores_the_method_and_all_four_comparators_on_the_same_rows(small_run):
    main = small_run["scenarios"]["main"]
    assert set(main["precision_at_10"]) == {"model", *e2.COMPARATORS}
    assert all(0 <= v <= 1 for v in main["precision_at_10"].values())
    assert 0 < main["base_rate"] < 1 and main["n_test_months"] > 5
    preds = small_run["_preds"]
    reference = preds["model"]["month_id"].to_numpy()
    assert all((frame["month_id"].to_numpy() == reference).all() for frame in preds.values())
    assert (preds["round1_alert"]["label"].to_numpy() == preds["model"]["label"].to_numpy()).all()
    assert {"lift_at_least_2", "beats_best_comparator", "passes"} <= set(main)


def test_the_run_reproduces_itself_exactly(small_run):
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])
        assert _plain(_run()) == _plain(small_run)


def test_only_the_registered_horizons_can_be_run():
    rows, outcomes, _ = make_inputs(n_months=44)
    with pytest.raises(ValueError, match="horizons"):
        e2.run_e2(rows, outcomes, 6, scenarios=("main",), n_boot=10)
