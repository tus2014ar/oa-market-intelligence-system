"""Tests for the live predictor (src/.../serving/predictor.py), on small hand-made models whose
answers can be worked out by hand. It uses only pandas and NumPy, as the API does: no
scikit-learn is needed to run these.

How it reproduces the fitted scikit-learn models and the stored forecasts on the real data is in
tests/test_model_artifacts.py; the endpoints are in tests/test_api.py."""

import json
import math

import numpy as np
import pandas as pd
import pytest

from oa_market_intelligence.modeling.design import _NUMERIC, calendar_columns
from oa_market_intelligence.modeling.segment_baselines import smoothed
from oa_market_intelligence.serving.predictor import (
    Predictor,
    _month_after,
    _sigmoid,
    logistic_predict,
)

N = len(_NUMERIC)  # the numeric columns every logistic model uses


def params(**changes) -> dict:
    """A model with no effect from any numeric column: only the specialty group and the intercept
    count. Zero coefficients, a unit scale and zero means make the arithmetic easy to check."""
    base = {
        "kind": "logistic",
        "numeric_columns": list(_NUMERIC),
        "impute_median": [0.0] * N,
        "scale_mean": [0.0] * N,
        "scale_scale": [1.0] * N,
        "categories": ["ORTHO", "OTHER"],
        "coef": [0.0] * N + [1.0, -1.0],  # +1 for ORTHO, -1 for OTHER
        "intercept": 0.0,
    }
    return {**base, **changes}


def month(*rows) -> pd.DataFrame:
    """Input rows with the calendar columns of January 2022, as the predictor adds them."""
    return calendar_columns(pd.DataFrame(list(rows)), 202201)


def inputs_row(specialty="ORTHOPEDIC SURGERY", group="ORTHO", age="65 TO 74", gender="FEMALE", **f):
    row = {
        "run_month_id": 202112,
        "specialty_name": specialty,
        "age_band": age,
        "gender": gender,
        "specialty_grouped": group,
        "seg_log_visits_lag1": math.log1p(400),
        "seg_share_lag1": 0.02,
        "seg_share_roll3": 0.02,
        "seg_prior_z": 40.0,
        "seg_prior_t": 2000.0,
        "spec_prior_z": 400.0,
        "spec_prior_t": 20000.0,
        "market_share_lag1": 0.02,
        "market_change_lag1": 0.0,
        "age_ordinal": 2.0,
        "gender_FEMALE": 1.0,
        "gender_MALE": 0.0,
    }
    return {**row, **f}


def artifact(task, horizon, model, parameters=None, **extra) -> dict:
    return {
        "model_id": f"{task}:{horizon}",
        "task": task,
        "horizon": horizon,
        "model": model,
        "model_version": f"v{task[0]}{horizon}",
        "run_month_id": 202112,
        "trained_on_rows": 1000,
        "data_version": "d1",
        "library": "scikit-learn test" if parameters else "none (a rule)",
        "known_bias": extra.get("known_bias"),
        "artifact_sha256": "0" * 64,
        "parameters": json.dumps(parameters) if parameters else "{}",
    }


def predictor(rows=None, **alert) -> Predictor:
    rows = rows or [
        inputs_row(),
        inputs_row(specialty="RHEUMATOLOGY", group="OTHER", gender="MALE"),
        inputs_row(specialty="TINY", group="OTHER", seg_log_visits_lag1=math.log1p(40)),
        inputs_row(specialty="NOSHARE", group="OTHER", seg_share_lag1=0.0),
    ]
    arts = [
        artifact("segment", 1, "logistic", params(intercept=0.5), known_bias=0.055),
        artifact("segment", 3, "logistic", params(intercept=-0.5), known_bias=0.109),
        artifact("segment", 6, "last_month"),
        artifact(
            "alert",
            1,
            "logistic",
            {**params(), "threshold": alert.get("threshold", 0.6), "min_origin_visits": 100},
        ),
    ]
    return Predictor(pd.DataFrame(arts), pd.DataFrame(rows))


# ------------------------------------------------------------------ the arithmetic


def test_the_logistic_function_is_stable_at_the_extremes_and_equals_the_formula():
    z = np.array([-800.0, -30.0, -1.0, 0.0, 1.0, 30.0, 800.0])
    p = _sigmoid(z)
    assert np.all((p >= 0) & (p <= 1)) and not np.any(np.isnan(p))
    assert p[3] == 0.5 and p[0] == 0.0 and p[-1] == 1.0
    assert p[4] == pytest.approx(1 / (1 + math.exp(-1))) and p[2] == pytest.approx(1 - p[4])


def test_saved_numbers_give_the_probability_worked_out_by_hand():
    frame = month(inputs_row(group="ORTHO"), inputs_row(group="OTHER"))
    p = logistic_predict(params(intercept=0.25), frame)
    assert p[0] == pytest.approx(1 / (1 + math.exp(-(0.25 + 1.0))))  # the ORTHO group: +1
    assert p[1] == pytest.approx(1 / (1 + math.exp(-(0.25 - 1.0))))  # the OTHER group: -1


def test_a_specialty_group_the_model_never_saw_adds_nothing():
    frame = month(inputs_row(group="A NEW GROUP"))
    assert logistic_predict(params(intercept=0.0), frame)[0] == pytest.approx(0.5)


def test_a_numeric_input_is_filled_scaled_and_weighted_as_saved():
    column = _NUMERIC.index("market_change_lag1")
    coef = [0.0] * N + [0.0, 0.0]
    coef[column] = 2.0
    model = params(
        coef=coef,
        impute_median=[0.0] * column + [0.5] + [0.0] * (N - column - 1),
        scale_mean=[0.0] * column + [0.1] + [0.0] * (N - column - 1),
        scale_scale=[1.0] * column + [0.4] + [1.0] * (N - column - 1),
    )
    given = logistic_predict(model, month(inputs_row(market_change_lag1=0.5)))[0]
    assert given == pytest.approx(1 / (1 + math.exp(-2.0 * (0.5 - 0.1) / 0.4)))
    missing = logistic_predict(model, month(inputs_row(market_change_lag1=np.nan)))[0]
    assert missing == pytest.approx(given)  # a missing value takes the saved median, 0.5


def test_the_months_after_the_last_month_cross_a_year_boundary():
    assert _month_after(202112, 1) == 202201
    assert _month_after(202112, 6) == 202206
    assert _month_after(202507, 6) == 202601
    assert _month_after(202301, 12) == 202401


# ------------------------------------------------------------------ one segment


def test_a_forecast_runs_the_model_with_the_months_calendar_and_compares_with_last_month():
    p = predictor()
    one = p.segment("ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE", 1)
    # the ORTHO group (+1) with intercept 0.5, on the month after December 2021
    assert one["forecast_pct"] == pytest.approx(100 / (1 + math.exp(-1.5)), abs=1e-4)
    assert (one["target_month_id"], one["target_month"], one["horizon"]) == (202201, "Jan 2022", 1)
    assert one["model"] == "logistic" and one["model_version"] == "vs1"
    last = float(smoothed(0.02 * 400, 400)) * 100
    assert one["last_month_pct"] == pytest.approx(last, abs=1e-4)
    assert one["change_pp"] == pytest.approx(one["forecast_pct"] - one["last_month_pct"], abs=2e-4)
    assert one["origin_visits"] == 400 and one["small"] is False
    assert one["known_bias_pct"] == pytest.approx(5.5)
    three = p.segment("ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE", 3)
    assert three["target_month_id"] == 202203
    assert three["forecast_pct"] == pytest.approx(100 / (1 + math.exp(-0.5)), abs=1e-4)


def test_at_six_months_the_rule_gives_last_months_smoothed_share():
    out = predictor().segment("ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE", 6)
    assert out["model"] == "last_month" and out["target_month_id"] == 202206
    assert out["forecast_pct"] == pytest.approx(out["last_month_pct"], abs=1e-4)
    assert out["change_pp"] == pytest.approx(0.0, abs=2e-4) and out["known_bias_pct"] is None


def test_names_are_matched_without_regard_to_case_or_spaces_around_them():
    p = predictor()
    out = p.segment("  rheumatology ", "65 to 74", "male", 1)
    assert (out["specialty"], out["age_band"], out["gender"]) == (
        "RHEUMATOLOGY",
        "65 TO 74",
        "MALE",
    )


def test_a_small_segment_is_marked_and_still_forecast():
    out = predictor().segment("TINY", "65 TO 74", "FEMALE", 1)
    assert out["origin_visits"] == 40 and out["small"] is True


def test_an_unknown_segment_or_horizon_is_an_error_that_says_what_is_available():
    p = predictor()
    with pytest.raises(LookupError, match="No segment 'NOPE'"):
        p.segment("NOPE", "65 TO 74", "FEMALE", 1)
    with pytest.raises(
        LookupError, match=r"No saved segment model for horizon 2; served: \[1, 3, 6\]"
    ):
        p.segment("ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE", 2)
    with pytest.raises(LookupError, match="No segment"):
        p.alert("NOPE", "65 TO 74", "FEMALE")


# ------------------------------------------------------------------ the alert


def test_the_alert_scores_an_eligible_segment_and_compares_it_with_the_cut_off():
    flagged = predictor(threshold=0.6).alert("ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE")
    # the ORTHO group (+1), intercept 0: 0.7311 is over a 0.6 cut-off
    assert flagged["eligible"] is True and flagged["reason"] is None
    assert flagged["alert_score"] == pytest.approx(1 / (1 + math.exp(-1.0)), abs=1e-6)
    assert flagged["threshold"] == 0.6 and flagged["flagged"] is True
    quiet = predictor(threshold=0.9).alert("ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE")
    assert quiet["flagged"] is False
    exactly = predictor(threshold=1 / (1 + math.exp(-1.0))).alert(
        "ORTHOPEDIC SURGERY", "65 TO 74", "FEMALE"
    )
    assert exactly["flagged"] is True  # a score equal to the cut-off is flagged, as in the job


def test_the_alert_says_why_a_segment_is_not_scored():
    p = predictor()
    small = p.alert("TINY", "65 TO 74", "FEMALE")
    assert small["eligible"] is False and "Fewer than 100 visits" in small["reason"]
    assert small["alert_score"] is None and small["flagged"] is None
    none = p.alert("NOSHARE", "65 TO 74", "FEMALE")
    assert none["eligible"] is False and "no share" in none["reason"]


# ------------------------------------------------------------------ the listing


def test_the_listing_names_every_model_and_every_segment_in_a_fixed_order():
    p = predictor()
    models = p.models()
    assert [m["model_id"] for m in models] == ["alert:1", "segment:1", "segment:3", "segment:6"]
    by_id = {m["model_id"]: m for m in models}
    assert by_id["segment:1"]["n_parameters"] == N + 2 + 1  # coefficients and the intercept
    assert (
        by_id["segment:6"]["n_parameters"] == 0 and by_id["segment:6"]["library"] == "none (a rule)"
    )
    assert by_id["segment:1"]["known_bias_pct"] == pytest.approx(5.5)
    assert by_id["segment:6"]["known_bias_pct"] is None
    assert p.horizons() == [1, 3, 6] and p.origin == 202112
    segments = p.segments()
    assert len(segments) == 4 and segments == sorted(
        segments, key=lambda s: (s["specialty"], s["age_band"], s["gender"])
    )


def test_a_predictor_with_nothing_saved_cannot_be_made():
    with pytest.raises(ValueError, match="no saved models"):
        Predictor(pd.DataFrame(), pd.DataFrame())
