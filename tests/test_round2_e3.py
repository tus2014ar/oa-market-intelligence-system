"""Tests for round 2, experiment E3 (src/.../modeling/round2/e3_ranking.py, plan sections 6 and 10):
Spearman and the top-fifth hit rate by hand, the history the comparators may see, eligibility, the
pass rule, planted good and random rankings, and a small run that must reproduce itself. Nothing
here uses real results."""

import json
import math

import numpy as np
import pandas as pd
import pytest

from modelling_fixtures import make_inputs, month_ids
from oa_market_intelligence.modeling import segment_models
from oa_market_intelligence.modeling.horizons import month_index
from oa_market_intelligence.modeling.round2 import e3_ranking as e3

# ------------------------------------------------------------------ the measures by hand


def test_spearman_of_a_six_segment_example_by_hand():
    a = [1, 2, 3, 4, 5, 6]
    b = [2, 1, 4, 3, 6, 5]  # every rank differs by 1: sum of squared gaps = 6
    assert e3.spearman(a, b) == pytest.approx(1 - 6 * 6 / (6 * 35))
    assert e3.spearman(a, a) == pytest.approx(1.0)
    assert e3.spearman(a, a[::-1]) == pytest.approx(-1.0)


def test_spearman_gives_tied_values_their_average_rank():
    # ranks of the first list: 1.5, 1.5, 3, 4; of the second: 1, 2, 3, 4
    # covariance 4.5, variances 4.5 and 5, so rho = 4.5 / sqrt(22.5)
    assert e3.spearman([1, 1, 2, 3], [1, 2, 3, 4]) == pytest.approx(4.5 / math.sqrt(22.5))


def test_spearman_of_a_constant_is_undefined():
    assert math.isnan(e3.spearman([2, 2, 2, 2], [1, 2, 3, 4]))


def _ten(model, actual=None):
    """Ten segments in one month; segment sN has the (9 - N)th-largest actual change by default."""
    return pd.DataFrame(
        {
            "month_id": 1,
            "segment": [f"s{i}" for i in range(10)],
            "actual": list(range(9, -1, -1)) if actual is None else actual,
            "model": model,
            "specialty_trend": 0.0,
            "segment_trend": 0.0,
            "random": 0.0,
        }
    )


def test_top_fifth_hit_rate_by_hand():
    # ten segments: the top fifth is 2 of them and the top two fifths of actual change is 4 (s0-s3)
    scores = [10, 1, 2, 3, 4, 9, 5, 6, 7, 8]  # picks s0 and s5
    frame = _ten(scores)
    assert e3.top_fifth_hit(frame, "model").iloc[0] == pytest.approx(0.5)  # s0 hit, s5 missed
    perfect = _ten(list(range(9, -1, -1)))
    assert e3.top_fifth_hit(perfect, "model").iloc[0] == pytest.approx(1.0)  # s0, s1


def test_top_fifth_ties_are_broken_by_segment_name():
    frame = _ten([0.0] * 10)  # all tied: the picks are s0 and s1, both truly in the top two fifths
    assert e3.top_fifth_hit(frame, "model").iloc[0] == 1.0
    reversed_actual = _ten([0.0] * 10, actual=list(range(10)))  # now s0, s1 are the worst
    assert e3.top_fifth_hit(reversed_actual, "model").iloc[0] == 0.0


def test_the_chance_hit_rate_is_the_top_two_fifths_share():
    assert e3.chance_hit(_ten([0.0] * 10)) == pytest.approx(0.4)
    seven = _ten([0.0] * 10).head(7)  # ceil(0.4 x 7) = 3 of 7
    assert e3.chance_hit(seven) == pytest.approx(3 / 7)


def test_a_month_with_too_few_segments_is_left_out_for_every_method():
    few = _ten([0.0] * 10).head(e3.MIN_ROWS - 1)
    assert e3.months_used(few) == [] and e3.months_used(_ten([0.0] * 10)) == [1]


def test_a_method_that_gives_everyone_the_same_score_counts_zero_that_month():
    assert e3.monthly_spearman(_ten([0.0] * 10), "model").iloc[0] == 0.0


# ------------------------------------------------------------------ the history known at the origin

M = month_ids(6, start=(2020, 1))  # 202001 .. 202006; the origin is M[4], three months back is M[1]


def _outcomes(spec):
    """`spec` maps a segment to (shares per month in M, visits per month)."""
    rows = []
    for segment, (shares, visits) in spec.items():
        for month, share, v in zip(M, shares, visits, strict=True):
            if share is not None:
                rows.append(
                    {"segment": segment, "month_id": month, "y_share": share, "y_visits": v}
                )
    return pd.DataFrame(rows)


def _preds(segments, origin=M[4]):
    return pd.DataFrame(
        {
            "month_id": M[4] + 3 if origin == M[4] else 0,
            "segment": segments,
            "origin_id": origin,
        }
    )


def test_the_trend_comparators_by_hand():
    outcomes = _outcomes(
        {
            "A|x|F": ([0.10, 0.12, 0.14, 0.16, 0.18, 0.20], [100] * 6),
            "A|x|M": ([0.02] * 6, [300] * 6),
            "B|x|F": ([0.05, 0.05, 0.05, 0.05, 0.04, 0.03], [200] * 6),
        }
    )
    out = e3.add_history(_preds(["A|x|F", "B|x|F"]), outcomes)
    # segment A|x|F: share at the origin (index 4), 0.18, less the share at index 1, 0.12
    assert out["segment_trend"].iloc[0] == pytest.approx(0.18 - 0.12)
    # specialty A pooled: (18 + 6) / 400 at the origin, (12 + 6) / 400 three months earlier
    assert out["specialty_trend"].iloc[0] == pytest.approx(24 / 400 - 18 / 400)
    assert out["segment_trend"].iloc[1] == pytest.approx(0.04 - 0.05)
    assert out["specialty_trend"].iloc[1] == pytest.approx(0.04 - 0.05)


def test_rewriting_every_month_after_the_origin_does_not_move_the_history():
    shares = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20]
    base = _outcomes({"A|x|F": (shares, [100] * 6), "A|x|M": ([0.02] * 6, [300] * 6)})
    tampered = base.copy()
    later = month_index(tampered["month_id"].to_numpy()) > month_index(M[4])
    tampered.loc[later, "y_share"] = 0.9
    tampered.loc[later, "y_visits"] = 5
    before = e3.add_history(_preds(["A|x|F"]), base)
    after = e3.add_history(_preds(["A|x|F"]), tampered)
    assert before[["segment_trend", "specialty_trend"]].equals(
        after[["segment_trend", "specialty_trend"]]
    )


def test_a_segment_with_no_share_three_months_before_the_origin_has_no_trend():
    outcomes = _outcomes({"A|x|F": ([0.1, None, 0.1, 0.1, 0.1, 0.1], [100] * 6)})
    out = e3.add_history(_preds(["A|x|F"]), outcomes)
    assert math.isnan(out["segment_trend"].iloc[0]) and math.isnan(out["specialty_trend"].iloc[0])


# ------------------------------------------------------------------ eligibility


def _scored(segment, share, visits, p=0.1, outcome_share=0.1, outcome_visits=100):
    return {
        "month_id": M[4] + 3,
        "segment": segment,
        "origin_id": M[4],
        "p": p,
        "z": round(outcome_share * outcome_visits),
        "t": outcome_visits,
        "seg_share_lag1": share,
        "seg_log_visits_lag1": math.log1p(visits),
    }


def test_eligibility_has_round_1s_edges_and_needs_a_share_three_months_before():
    outcomes = _outcomes(
        {
            "A|x|F": ([0.1] * 6, [100] * 6),
            "B|x|F": ([0.1] * 6, [99] * 6),
            "C|x|F": ([0.0] * 6, [100] * 6),
            "D|x|F": ([0.1, None, 0.1, 0.1, 0.1, 0.1], [100] * 6),
        }
    )
    preds = pd.DataFrame(
        [
            _scored("A|x|F", 0.1, 100),  # 100 visits: eligible
            _scored("B|x|F", 0.1, 99),  # 99 visits: out
            _scored("C|x|F", 0.0, 100),  # a zero share at the origin: out
            _scored("D|x|F", 0.1, 100),  # no share three months before the origin: out
        ]
    )
    frame = e3.eligible_frame(preds, outcomes)
    assert list(frame["segment"]) == ["A|x|F"]


def test_the_predicted_and_actual_changes_are_measured_from_the_origin_share():
    outcomes = _outcomes({"A|x|F": ([0.10, 0.10, 0.10, 0.10, 0.10, 0.10], [100] * 6)})
    preds = pd.DataFrame([_scored("A|x|F", 0.10, 100, p=0.13, outcome_share=0.07)])
    frame = e3.eligible_frame(preds, outcomes)
    assert frame["model"].iloc[0] == pytest.approx(0.03)
    assert frame["actual"].iloc[0] == pytest.approx(-0.03)


# ---------------------------------------------- the pass rule and planted rankings


def test_the_pass_rule_needs_the_lower_end_above_zero():
    assert e3.checks(0.0001)["passes"] and not e3.checks(0.0)["passes"]
    assert not e3.checks(-0.2)["passes"]


def test_the_round_is_still_seven_tests_and_one_strict_percentile():
    assert e3.ROUND_PERCENTILE == pytest.approx(0.05 / 7 * 100)
    assert (e3.HORIZON, e3.LOOKBACK, e3.MIN_ROWS) == (3, 3, 5)
    assert e3.COMPARATORS == ("specialty_trend", "segment_trend", "random")


def _planted(model_signal, n_months=30, n_segments=12, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for month in range(n_months):
        for i in range(n_segments):
            actual = rng.normal(0, 0.01)
            rows.append(
                {
                    "month_id": 202001 + month,
                    "segment": f"seg{i:02d}",
                    "actual": actual,
                    "model": model_signal(actual, rng),
                    "specialty_trend": rng.normal(0, 0.01),
                    "segment_trend": rng.normal(0, 0.01),
                    "random": rng.random(),
                }
            )
    return pd.DataFrame(rows)


def test_a_planted_good_ranking_passes_and_a_planted_random_one_does_not():
    good = e3.judge(_planted(lambda a, rng: a + rng.normal(0, 0.003)), n_boot=300)
    bad = e3.judge(_planted(lambda a, rng: rng.normal(0, 0.01)), n_boot=300)
    assert good["passes"] and good["mean_spearman"]["model"] > 0.9
    assert good["top_fifth_hit_rate"]["model"] > good["chance_hit_rate"]
    assert not bad["passes"] and abs(bad["mean_spearman"]["model"]) < 0.2


def test_the_best_comparator_is_the_one_with_the_highest_mean_correlation():
    frame = _planted(lambda a, rng: a + rng.normal(0, 0.003))
    frame["segment_trend"] = frame["actual"] * 0.5 + np.random.default_rng(3).normal(
        0, 0.01, len(frame)
    )
    result = e3.judge(frame, n_boot=100)
    assert result["best_comparator"] == "segment_trend"
    assert set(result["versus_each_comparator"]) == set(e3.COMPARATORS)


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
    kwargs.setdefault("scenarios", ("main",))
    return e3.run_e3(rows, outcomes, n_boot=60, seed=0, **kwargs)


def _plain(result):
    keep = {k: v for k, v in result.items() if not k.startswith("_")}
    return json.dumps(keep, sort_keys=True, default=float)


def test_the_run_scores_the_model_and_all_three_comparators_on_the_same_rows(small_run):
    main = small_run["scenarios"]["main"]
    assert set(main["mean_spearman"]) == {"model", *e3.COMPARATORS}
    assert main["n_test_months"] > 5 and main["n_rows"] >= e3.MIN_ROWS * main["n_test_months"]
    assert {"passes", "beats_best_comparator", "chance_hit_rate", "log_loss"} <= set(main)
    assert -1 <= main["mean_spearman"]["model"] <= 1


def test_the_run_reproduces_itself_exactly(small_run):
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
        rows, outcomes, _ = make_inputs(n_months=44)
        assert _plain(run(rows, outcomes)) == _plain(small_run)


def test_rewriting_the_last_month_does_not_move_any_earlier_month(small_run):
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
        rows, outcomes, _ = make_inputs(n_months=44)
        last = outcomes["month_id"].max()
        rng = np.random.default_rng(5)
        tampered = outcomes.copy()
        at_last = tampered["month_id"] == last
        tampered.loc[at_last, "y_share"] = rng.uniform(0, 1, at_last.sum())
        tampered.loc[at_last, "y_visits"] = rng.integers(1, 900, at_last.sum()).astype(float)
        frame = run(rows, tampered)["_frame"]
    original = small_run["_frame"]
    earlier = lambda f: f[f["month_id"] < last].reset_index(drop=True)  # noqa: E731
    pd.testing.assert_frame_equal(earlier(frame), earlier(original))


def test_a_wrong_stored_score_stops_the_run():
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
        rows, outcomes, _ = make_inputs(n_months=44)
        with pytest.raises(ValueError, match="differ from round 1"):
            run(rows, outcomes, stored_log_loss=0.5)
