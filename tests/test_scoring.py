"""Tests for the monthly scoring job (src/.../modeling/scoring.py): the adoption map is validated
against the registered results, the features at the origin equal the stored feature rows, the
forecasts cannot see data after the origin, and the job reproduces the stored demonstration."""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from modelling_fixtures import make_seg
from oa_market_intelligence.features.build import build_feature_tables
from oa_market_intelligence.features.segment import group_rare_specialties
from oa_market_intelligence.modeling import alerts, segment_models
from oa_market_intelligence.modeling.horizons import month_id_from_index, month_index, outcome_table
from oa_market_intelligence.modeling.run_modelling import DEFAULT_DB, RESULTS_JSON
from oa_market_intelligence.modeling.scoring import (
    PRED_COLUMNS,
    ServingMapError,
    load_serving_map,
    origin_features,
    score_batch,
    validate_serving_map,
    write_predictions,
)
from oa_market_intelligence.modeling.store import load_results, model_scores_frame

ORIGIN = 202112  # a December: the targets 1, 3 and 6 months on cross into 2022
SERVING = {
    "national": {"1": "last_month", "3": "last_month", "6": "last_month"},
    "segment": {"1": "logistic", "3": "logistic", "6": "last_month"},
    "alert": {"1": "logistic"},
}


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])


def make_monthly(seg):
    by_month = seg.groupby("month_id")[["branded_injectable_visits", "total_category_visits"]].sum()
    z, t = by_month["branded_injectable_visits"], by_month["total_category_visits"]
    return pd.DataFrame({
        "month_id": by_month.index,
        "branded_injectable_visits": z.to_numpy(),
        "generic_corticosteroid_visits": ((t - z) * 0.7).round().to_numpy(),
        "nsaid_otc_visits": ((t - z) * 0.3).round().to_numpy(),
        "visit_share": (z / t).to_numpy(),
    }).reset_index(drop=True)


@pytest.fixture(scope="module")
def data():
    seg = make_seg(n_months=44)
    return seg, make_monthly(seg)


def _scores_frame(serving_rows):
    return pd.DataFrame(serving_rows, columns=["task", "horizon", "scenario", "method", "serving",
                                               "bias_vs_actual"])


# ------------------------------------------------------------------ the adoption map


def test_the_committed_map_is_allowed_and_agrees_with_the_registered_results():
    serving = load_serving_map()
    scores = model_scores_frame(load_results(RESULTS_JSON))
    validate_serving_map(serving)  # allowed names and horizons
    validate_serving_map(serving, scores)  # every adopted model is what the rule chose
    assert serving["decision"] == "DL-79"
    assert serving["alert"] == {"1": "logistic"}  # narrower than the registered rule: 1 month only
    assert serving["segment"] == {"1": "logistic", "3": "logistic", "6": "last_month"}


def test_a_map_naming_something_unsupported_is_refused():
    for bad in (
        {"national": {"1": "gbm"}},  # not a national model
        {"segment": {"2": "logistic"}},  # a horizon that was not tested
        {"alert": {"1": "recent_drop"}},  # a baseline cannot be an alert model
        {"oracle": {"1": "x"}},  # not a task
    ):
        with pytest.raises(ServingMapError):
            validate_serving_map(bad)


def test_a_model_the_registered_rule_did_not_choose_is_refused():
    scores = _scores_frame([
        ("T2", 1, "main", "logistic", 1, 0.05),
        ("T2", 3, "main", "logistic", 0, 0.1),  # promoted but not serving at 3 months
        ("T2", 3, "main", "last_month", 1, None),
    ])
    validate_serving_map({"segment": {"1": "logistic"}}, scores)
    with pytest.raises(ServingMapError, match="not the model the registered serving rule chose"):
        validate_serving_map({"segment": {"3": "logistic"}}, scores)
    with pytest.raises(ServingMapError):  # no such row at all
        validate_serving_map({"national": {"1": "last_month"}}, scores)


# ------------------------------------------------------------------ features at the origin


def test_the_features_at_the_origin_equal_the_stored_rows_built_from_all_the_data(data):
    seg, monthly = data
    groups = dict(zip(seg["specialty_name"], group_rare_specialties(seg["specialty_name"]),
                      strict=True))
    full, _ = build_feature_tables(seg, monthly, groups=("G4",))
    full["specialty_grouped"] = full["specialty_name"].map(groups)
    train, now = origin_features(seg, monthly, ORIGIN, specialty_groups=groups)
    next_month = int(month_id_from_index(month_index(ORIGIN) + 1))
    stored_now = full[full["month_id"] == next_month].reset_index(drop=True)
    assert len(now) == len(stored_now) > 0
    columns = [c for c in stored_now.columns if not c.startswith("y_")]
    a = now.sort_values("segment")[columns].reset_index(drop=True)
    b = stored_now.sort_values("segment")[columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)
    # the training rows are the stored rows of the months up to the origin, outcomes included
    stored_train = full[full["month_id"] <= ORIGIN].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        train.sort_values(["month_id", "segment"]).reset_index(drop=True),
        stored_train.sort_values(["month_id", "segment"]).reset_index(drop=True),
        check_exact=False, rtol=1e-12, atol=1e-12,
    )
    assert not any(c.startswith("y_") for c in now.columns)


# ------------------------------------------------------------------ the forecasts


def test_the_national_forecast_is_the_last_value_with_its_range_and_the_right_months(data):
    seg, monthly = data
    out = score_batch(seg, monthly, SERVING, origin=ORIGIN, include_alert=False)
    national = out[out["level"] == "national"].set_index("horizon")
    last = monthly.set_index("month_id").loc[ORIGIN, "visit_share"]
    assert list(national.index) == [1, 3, 6]
    assert list(national["target_month_id"]) == [202201, 202203, 202206]
    assert np.allclose(national["point_share"], last)  # "same as last month", whatever the horizon
    assert (national["lo90_share"] <= national["point_share"]).all()
    assert (national["point_share"] <= national["hi90_share"]).all()
    assert (national["lo80_share"] >= national["lo90_share"]).all()
    assert (national["model"] == "last_month").all()


def test_the_six_month_segment_baseline_is_last_months_smoothed_share_by_hand(data):
    seg, monthly = data
    out = score_batch(seg, monthly, SERVING, origin=ORIGIN, include_alert=False)
    row = out[(out["level"] == "segment") & (out["horizon"] == 6)].iloc[0]
    last = seg[(seg["month_id"] == ORIGIN) & (seg["specialty_name"] == row["specialty_name"])
               & (seg["age_band"] == row["age_band"]) & (seg["gender"] == row["gender"])].iloc[0]
    expected = (last["branded_injectable_visits"] + 0.5) / (last["total_category_visits"] + 1.0)
    assert row["point_share"] == pytest.approx(expected)
    assert row["origin_visits"] == last["total_category_visits"]
    assert row["target_month_id"] == 202206 and row["model"] == "last_month"


def test_predictions_are_shares_for_the_segments_with_enough_visits_at_the_origin(data):
    seg, monthly = data
    out = score_batch(seg, monthly, SERVING, origin=ORIGIN, include_alert=False)
    segment = out[out["level"] == "segment"]
    assert set(segment["horizon"]) == {1, 3, 6}
    assert (segment["origin_visits"] >= 20).all()  # the scored-row rule of the feature table
    assert segment["point_share"].between(0, 1).all()
    n_at_origin = int((seg[seg["month_id"] == ORIGIN]["total_category_visits"] >= 20).sum())
    assert (segment["horizon"] == 1).sum() == n_at_origin
    key = ["level", "horizon", "specialty_name", "age_band", "gender"]
    assert not out.duplicated(key).any()
    assert list(out.columns) == PRED_COLUMNS


def test_the_alert_exists_only_at_one_month_and_only_for_eligible_segments(data):
    seg, monthly = data
    out = score_batch(seg, monthly, SERVING, origin=ORIGIN)
    segment = out[out["level"] == "segment"]
    flagged = segment[segment["alert_flag"].notna()]
    assert not flagged.empty and set(flagged["horizon"]) == {1}
    assert (flagged["origin_visits"] >= alerts.MIN_ORIGIN_VISITS).all()
    assert set(flagged["alert_flag"]) <= {0, 1}
    assert flagged["alert_score"].between(0, 1).all()
    eligible = segment[(segment["horizon"] == 1) & (segment["origin_visits"] >= 100)]
    assert len(eligible) >= len(flagged)  # a zero origin share is not scored either
    assert segment[segment["horizon"] != 1]["alert_flag"].isna().all()
    assert out[out["level"] == "national"]["alert_flag"].isna().all()


def test_the_known_bias_comes_from_the_stored_scores(data):
    seg, monthly = data
    scores = _scores_frame([
        ("T1", 1, "main", "last_month", 1, None), ("T1", 3, "main", "last_month", 1, None),
        ("T1", 6, "main", "last_month", 1, None),
        ("T2", 1, "main", "logistic", 1, 0.055), ("T2", 3, "main", "logistic", 1, 0.109),
        ("T2", 6, "main", "last_month", 1, None),
        ("T3", 1, "main", "logistic", 1, None),
    ])
    out = score_batch(seg, monthly, SERVING, origin=ORIGIN, scores=scores, include_alert=False)
    segment = out[out["level"] == "segment"].groupby("horizon")["known_bias"].first()
    assert segment[1] == pytest.approx(0.055) and segment[3] == pytest.approx(0.109)
    assert pd.isna(segment[6])  # a baseline has no recorded bias


# ------------------------------------------------------------------ no future, reproducible


def test_the_forecasts_do_not_move_when_the_data_after_the_origin_is_rewritten(data):
    seg, monthly = data
    base = score_batch(seg, monthly, SERVING, origin=ORIGIN)
    seg_t, monthly_t = seg.copy(), monthly.copy()
    later = seg_t["month_id"] > ORIGIN
    rng = np.random.default_rng(5)
    seg_t.loc[later, "branded_injectable_visits"] = rng.integers(0, 400, later.sum())
    seg_t.loc[later, "total_category_visits"] = rng.integers(400, 900, later.sum())
    monthly_t.loc[monthly_t["month_id"] > ORIGIN, "visit_share"] = 0.5
    changed = score_batch(seg_t, monthly_t, SERVING, origin=ORIGIN)
    pd.testing.assert_frame_equal(base, changed)


def test_the_job_is_reproducible(data):
    seg, monthly = data
    a = score_batch(seg, monthly, SERVING, origin=ORIGIN)
    b = score_batch(seg, monthly, SERVING, origin=ORIGIN)
    pd.testing.assert_frame_equal(a, b)
    assert a["model_version"].nunique() >= 3  # one per model and horizon, stable across runs


def test_an_origin_that_is_not_a_month_of_the_data_is_an_error(data):
    seg, monthly = data
    with pytest.raises(ValueError, match="not a month of the data"):
        score_batch(seg, monthly, SERVING, origin=209912)


# ------------------------------------------------------------------ the pipeline stage


def test_write_predictions_replaces_the_table_and_checks_the_map(tmp_path, data, monkeypatch):
    seg, monthly = data
    engine = create_engine(f"sqlite:///{(tmp_path / 'w.db').as_posix()}")
    scores = _scores_frame([
        ("T1", 1, "main", "last_month", 1, None), ("T1", 3, "main", "last_month", 1, None),
        ("T1", 6, "main", "last_month", 1, None),
        ("T2", 1, "main", "logistic", 1, 0.055), ("T2", 3, "main", "logistic", 1, 0.109),
        ("T2", 6, "main", "last_month", 1, None), ("T3", 1, "main", "logistic", 1, None),
    ])
    with engine.begin() as conn:
        monthly.to_sql("gold_visit_share_monthly", conn, index=False)
        scores.to_sql("model_scores", conn, index=False)
    monkeypatch.setattr("oa_market_intelligence.features.segment.load_segment_gold", lambda e: seg)
    serving_json = tmp_path / "serving.json"
    import json
    serving_json.write_text(json.dumps(SERVING))
    first = write_predictions(engine, serving_json)
    second = write_predictions(engine, serving_json)  # replaced, not appended
    assert first == second and first["national"] == 3 and first["run_month_id"] == 202303
    assert first["pred_batch"] == first["national"] + first["segment"]
    table = pd.read_sql("SELECT * FROM pred_batch", engine)
    assert list(table.columns) == PRED_COLUMNS and len(table) == first["pred_batch"]
    # a map that disagrees with the stored scores stops the stage
    serving_json.write_text(json.dumps({**SERVING, "segment": {"3": "gbm"}}))
    with pytest.raises(ServingMapError):
        write_predictions(engine, serving_json)


# ------------------------------------------------------------ against the stored demonstration


@pytest.mark.skipif(not DEFAULT_DB.exists(), reason="the committed warehouse is not present")
def test_the_job_reproduces_the_stored_batch_demonstration_on_the_real_data(monkeypatch):
    monkeypatch.undo()  # the real model settings (the demonstration tuned over all three C)
    from oa_market_intelligence.features.segment import load_segment_gold

    engine = create_engine(f"sqlite:///{DEFAULT_DB.as_posix()}")
    seg = load_segment_gold(engine)
    monthly = pd.read_sql("SELECT * FROM gold_visit_share_monthly ORDER BY month_id", engine)
    scores = pd.read_sql("SELECT * FROM model_scores", engine)
    backtest = pd.read_sql("SELECT * FROM backtest_batch WHERE cutoff_month_id = 202212", engine)
    out = score_batch(seg, monthly, load_serving_map(), origin=202212, scores=scores,
                      include_alert=False)
    national = out[out["level"] == "national"].set_index("horizon")
    reference = backtest[backtest["level"] == "national"].set_index("step")
    for horizon in (1, 3, 6):
        assert national.loc[horizon, "point_share"] == pytest.approx(
            reference.loc[horizon, "predicted_share"], abs=1e-12)
        assert national.loc[horizon, "lo90_share"] == pytest.approx(
            reference.loc[horizon, "lo90_share"], abs=1e-12)
        assert national.loc[horizon, "hi90_share"] == pytest.approx(
            reference.loc[horizon, "hi90_share"], abs=1e-12)
    outcomes = outcome_table(seg)
    segment = out[out["level"] == "segment"].copy()
    segment["segment"] = (
        segment["specialty_name"] + "|" + segment["age_band"] + "|" + segment["gender"]
    )
    for horizon in (1, 3, 6):  # the demonstration's step k uses the model of horizon k here
        target = int(month_id_from_index(month_index(202212) + horizon))
        part = segment[segment["horizon"] == horizon].merge(
            outcomes[outcomes["month_id"] == target][["segment", "y_visits"]], on="segment")
        part = part.assign(weighted=part["point_share"] * part["y_visits"])
        mine = part.groupby("specialty_name").apply(
            lambda g: g["weighted"].sum() / g["y_visits"].sum(), include_groups=False)
        rows = backtest[(backtest["level"] == "specialty") & (backtest["step"] == horizon)]
        ref = rows.set_index("specialty_name")["predicted_share"]
        both = pd.concat([mine, ref], axis=1, keys=["mine", "ref"]).dropna()
        assert len(both) == len(ref) > 0
        assert np.allclose(both["mine"], both["ref"], atol=1e-10)
