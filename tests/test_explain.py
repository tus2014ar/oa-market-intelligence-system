"""Tests for the model explanation (src/.../modeling/explain.py, DL-91).

The arithmetic is checked by hand; then the committed summary is checked against a fresh run on the
committed warehouse: it is up to date, the refit reproduces the stored forecasts exactly, the
contributions add up to each forecast, and the closed form equals the `shap` package where that is
installed.
"""

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.modeling import explain

REAL = explain.DEFAULT_DB.exists() and explain.SUMMARY_JSON.exists()
real_only = pytest.mark.skipif(not REAL, reason="no published warehouse or committed summary")


# ------------------------------------------------------------------ by hand


def test_linear_shap_is_coefficient_times_distance_from_the_average():
    coefficients = np.array([2.0, -1.0])
    mean = np.array([1.0, 2.0])
    values = explain.linear_shap(np.array([[3.0, 1.0], [1.0, 2.0]]), coefficients, mean)
    assert values.tolist() == [[4.0, 1.0], [0.0, 0.0]]  # a row at the average has no push at all


def test_contributions_add_up_to_the_models_own_output_minus_the_base():
    coefficients, intercept = np.array([0.5, -2.0, 1.5]), 0.3
    matrix = np.array([[1.0, 0.0, 2.0], [0.0, 1.0, -1.0], [2.0, 2.0, 2.0]])
    mean = matrix.mean(axis=0)
    values = explain.linear_shap(matrix, coefficients, mean)
    base = intercept + mean @ coefficients
    assert np.allclose(base + values.sum(axis=1), intercept + matrix @ coefficients)


def test_every_kind_of_input_has_a_plain_language_group_and_unknown_ones_are_refused():
    context = ("g4_logit_spec_share_lag1", "g4_missing")
    assert explain.group_of("logit_roll3", context) == explain.OWN_RECENT
    assert explain.group_of("specialty_grouped_FAMILY PRACTICE", context) == explain.WHICH_SPECIALTY
    assert explain.group_of("g4_missing", context) == explain.CONTEXT
    assert {explain.group_of(k, context) for k in explain.GROUP_OF} == {
        explain.OWN_RECENT,
        explain.LONG_RUN,
        explain.SPECIALTY_LONG_RUN,
        explain.MARKET,
        explain.SIZE,
        explain.AGE_GENDER,
        explain.SEASON,
    }
    with pytest.raises(ValueError, match="no plain-language group"):
        explain.group_of("something_new", context)


def test_grouping_sums_within_a_group_and_shares_add_to_one_hundred():
    values = np.array([[1.0, -2.0, 3.0], [1.0, 2.0, 1.0]])
    table = explain.by_group(values, ["a", "b", "a"])
    assert table["a"].tolist() == [4.0, 2.0] and table["b"].tolist() == [-2.0, 2.0]
    share = explain.shares(table)  # a moves 3 on average, b moves 2
    assert list(share.index) == ["a", "b"]
    assert share["a"] == pytest.approx(60.0) and share["b"] == pytest.approx(40.0)


def test_only_a_logistic_segment_model_can_be_explained():
    seg = pd.DataFrame({"month_id": [202507], "specialty_name": ["X"]})
    serving = {"segment": {"1": "gbm", "3": "logistic"}}
    with pytest.raises(ValueError, match="only the logistic regression"):
        explain.explain(seg, pd.DataFrame(), serving)


# ------------------------------------------------------------------ the committed summary


@pytest.fixture(scope="module")
def fresh():
    return explain.explain_from_warehouse()


@real_only
def test_the_committed_summary_is_the_one_a_fresh_run_gives(fresh):
    committed = explain.SUMMARY_JSON.read_text("utf-8").replace("\r\n", "\n")
    assert committed == explain.to_json(fresh), (
        "run: python -m oa_market_intelligence.modeling.explain"
    )


@real_only
def test_the_refit_reproduces_the_stored_forecasts_and_the_pieces_add_up(fresh):
    for horizon in ("1", "3"):
        h = fresh["horizons"][horizon]
        assert h["max_gap_to_stored_forecast"] <= explain.TOLERANCE
        assert h["additivity_max_error"] <= explain.TOLERANCE
        assert h["segments"] == 113 and h["C"] > 0
        assert sum(h["group_share_of_movement_pct"].values()) == pytest.approx(100.0, abs=0.5)


@real_only
def test_what_the_model_relies_on_matches_what_the_deck_says(fresh):
    one, three = fresh["horizons"]["1"], fresh["horizons"]["3"]
    ranked = list(one["group_share_of_movement_pct"])
    assert ranked[0] == explain.OWN_RECENT and ranked[1] == explain.CONTEXT
    assert 39 <= one["group_share_of_movement_pct"][explain.OWN_RECENT] <= 42
    assert one["group_share_of_movement_pct"][explain.SEASON] < 1.0  # time of year barely matters
    assert (
        three["group_share_of_movement_pct"][explain.OWN_RECENT]
        > one["group_share_of_movement_pct"][explain.OWN_RECENT]
    )
    example = one["example"]
    assert example["segment"] == {
        "specialty_name": "PHYSICIAN ASSISTANT",
        "age_band": "65 TO 74",
        "gender": "FEMALE",
    }
    assert sum(example["pushes"].values()) == pytest.approx(
        np.log(example["forecast_pct"] / (100 - example["forecast_pct"]))
        - np.log(one["typical_segment_pct"] / (100 - one["typical_segment_pct"])),
        abs=0.01,
    )


@real_only
def test_the_closed_form_equals_the_shap_package(fresh):
    shap = pytest.importorskip("shap")
    from sqlalchemy import create_engine

    from oa_market_intelligence.features.groups import columns_for
    from oa_market_intelligence.features.segment import group_rare_specialties, load_segment_gold
    from oa_market_intelligence.modeling.horizons import (
        month_id_from_index,
        month_index,
        outcome_table,
        pair_rows,
    )
    from oa_market_intelligence.modeling.scoring import _calendar, origin_features
    from oa_market_intelligence.modeling.segment_models import (
        design_frame,
        fitter_for,
    )

    engine = create_engine(f"sqlite:///file:{explain.DEFAULT_DB.as_posix()}?mode=ro&uri=true")
    seg = load_segment_gold(engine)
    with engine.connect() as conn:
        monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", conn)
    origin = int(seg["month_id"].max())
    groups = dict(
        zip(seg["specialty_name"], group_rare_specialties(seg["specialty_name"]), strict=True)
    )
    train, now = origin_features(seg, monthly, origin, specialty_groups=groups)
    extra = columns_for(("G4",), target="T2")
    paired = pair_rows(train, outcome_table(seg), 1)
    test = _calendar(now, int(month_id_from_index(month_index(origin) + 1)))
    fitter = fitter_for("logistic", {"C": fresh["horizons"]["1"]["C"]}, extra_columns=extra)
    fitter(paired)
    prep, clf = fitter.model.named_steps["prep"], fitter.model.named_steps["clf"]

    def dense(frame):
        matrix = prep.transform(design_frame(frame, extra))
        return matrix.toarray() if hasattr(matrix, "toarray") else np.asarray(matrix)

    background, x = dense(paired), dense(test)
    expected = shap.LinearExplainer(
        clf, shap.maskers.Independent(background, max_samples=len(background))
    ).shap_values(x)
    ours = explain.linear_shap(x, clf.coef_[0], background.mean(axis=0))
    assert np.abs(ours - expected).max() < 1e-9
