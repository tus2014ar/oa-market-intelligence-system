"""Tests for saving the fitted models as readable numbers (src/.../modeling/artifacts.py, DL-117).

The point of the saved numbers is that applying them with NumPy gives the very forecasts the job
stored, so these tests run the scoring job on synthetic data and then check the saved models
against `pred_batch`: the same probability for every segment and horizon, the same alert flags."""

import copy
import hashlib
import json

import pandas as pd
import pytest
from sqlalchemy import create_engine

from modelling_fixtures import make_seg
from oa_market_intelligence.modeling import alerts, segment_models
from oa_market_intelligence.modeling.artifacts import (
    ARTIFACT_COLUMNS,
    ARTIFACT_TABLE,
    INPUT_FEATURES,
    INPUT_KEYS,
    INPUT_TABLE,
    build_tables,
    verify,
)
from oa_market_intelligence.modeling.scoring import score_batch, write_predictions
from oa_market_intelligence.serving.predictor import Predictor, logistic_predict
from test_scoring import ORIGIN, SERVING, make_monthly


@pytest.fixture(autouse=True)
def small_models(monkeypatch):
    monkeypatch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
    monkeypatch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])


@pytest.fixture(scope="module")
def data():
    seg = make_seg(n_months=44)
    return seg, make_monthly(seg)


@pytest.fixture(scope="module")
def scored(data):
    """The batch forecasts, with what the job collected to save the models."""
    seg, monthly = data
    collect: dict = {}
    with pytest.MonkeyPatch.context() as patch:  # small models: the autouse fixture is per test
        patch.setitem(segment_models.MODEL_CONFIGS, "logistic", [{"C": 0.1}])
        patch.setitem(alerts.ALERT_CONFIGS, "logistic", [{"C": 0.1}])
        out = score_batch(seg, monthly, SERVING, origin=ORIGIN, collect=collect)
    artifacts, inputs = build_tables(collect, "dv1")
    return out, collect, artifacts, inputs


def live(artifacts, inputs) -> Predictor:
    return Predictor(artifacts, inputs)


# ------------------------------------------------------------------ what is saved


def test_one_row_is_saved_for_each_served_model_and_nothing_for_the_national_rule(scored):
    _, _, artifacts, _ = scored
    assert list(artifacts.columns) == ARTIFACT_COLUMNS
    assert list(artifacts["model_id"]) == ["segment:1", "segment:3", "segment:6", "alert:1"]
    by_id = artifacts.set_index("model_id")
    assert by_id.loc["segment:6", "model"] == "last_month"
    assert by_id.loc["segment:6", "parameters"] == "{}"
    assert by_id.loc["segment:6", "library"] == "none (a rule)"
    for model_id in ("segment:1", "segment:3", "alert:1"):
        assert by_id.loc[model_id, "model"] == "logistic"
        assert by_id.loc[model_id, "library"].startswith("scikit-learn ")
    assert set(artifacts["run_month_id"]) == {ORIGIN} and set(artifacts["data_version"]) == {"dv1"}


def test_the_saved_numbers_have_the_shape_of_the_fitted_pipeline(scored):
    _, collect, artifacts, _ = scored
    extra = list(collect["extra"])
    for row in artifacts[artifacts["model"] == "logistic"].itertuples():
        p = json.loads(row.parameters)
        columns = p["numeric_columns"]
        assert columns[len(columns) - len(extra) :] == extra
        assert (
            len(p["impute_median"]) == len(p["scale_mean"]) == len(p["scale_scale"]) == len(columns)
        )
        assert len(p["coef"]) == len(columns) + len(p["categories"])
        assert all(s > 0 for s in p["scale_scale"])
        assert p["categories"] == sorted(p["categories"])
        assert isinstance(p["intercept"], float) and p["config"] == {"C": 0.1}
    alert = json.loads(artifacts.set_index("model_id").loc["alert:1", "parameters"])
    assert 0 < alert["threshold"] < 1 and alert["min_origin_visits"] == alerts.MIN_ORIGIN_VISITS


def test_the_fingerprint_is_the_sha256_of_the_canonical_numbers(scored):
    _, _, artifacts, _ = scored
    for row in artifacts.itertuples():
        canonical = json.dumps(json.loads(row.parameters), sort_keys=True, separators=(",", ":"))
        assert row.artifact_sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    assert artifacts["artifact_sha256"].is_unique


def test_the_known_bias_and_training_size_are_recorded_per_model(scored):
    _, collect, artifacts, _ = scored
    by_id = artifacts.set_index("model_id")
    for horizon in (1, 3, 6):
        assert (
            by_id.loc[f"segment:{horizon}", "trained_on_rows"]
            == (collect["segment"][horizon]["trained_on_rows"])
        )
    assert (artifacts["trained_on_rows"] > 0).all()


def test_the_inputs_are_one_row_per_segment_at_the_last_month(scored, data):
    out, _, _, inputs = scored
    seg, _ = data
    assert list(inputs.columns[:1]) == ["run_month_id"] and set(inputs["run_month_id"]) == {ORIGIN}
    assert set(INPUT_KEYS + INPUT_FEATURES) <= set(inputs.columns)
    keys = ["specialty_name", "age_band", "gender"]
    assert not inputs.duplicated(keys).any()
    forecast = out[(out["level"] == "segment") & (out["horizon"] == 1)]
    assert len(inputs) == len(forecast)  # a row for each segment that was forecast
    no_future = seg[seg["month_id"] > ORIGIN]
    assert not no_future.empty and "y_share" not in inputs.columns  # no outcome columns saved


# ------------------------------------------------------------------ equal to the batch forecasts


def test_live_forecasts_equal_the_stored_batch_forecasts_for_every_segment_and_horizon(scored):
    out, _, artifacts, inputs = scored
    p = live(artifacts, inputs)
    segment = out[out["level"] == "segment"]
    assert len(segment) > 0
    for r in segment.itertuples():
        got = p.segment(r.specialty_name, r.age_band, r.gender, int(r.horizon))
        assert got["forecast_pct"] / 100 == pytest.approx(r.point_share, abs=1e-6)
        assert got["target_month_id"] == r.target_month_id and got["model"] == r.model
        assert got["origin_visits"] == r.origin_visits


def test_live_alerts_equal_the_stored_scores_and_flags(scored):
    out, _, artifacts, inputs = scored
    p = live(artifacts, inputs)
    one = out[(out["level"] == "segment") & (out["horizon"] == 1)]
    scored_rows = one[one["alert_score"].notna()]
    unscored = one[one["alert_score"].isna()]
    assert len(scored_rows) > 0
    for r in scored_rows.itertuples():
        got = p.alert(r.specialty_name, r.age_band, r.gender)
        assert got["eligible"] is True
        assert got["alert_score"] == pytest.approx(r.alert_score, abs=1e-6)
        assert got["flagged"] == bool(r.alert_flag)
    for r in unscored.itertuples():  # a segment the job did not score is one the API will not score
        assert p.alert(r.specialty_name, r.age_band, r.gender)["eligible"] is False


def _month_after(month_id: int, months: int) -> int:
    index = (month_id // 100) * 12 + (month_id % 100 - 1) + months
    return (index // 12) * 100 + index % 12 + 1


# ------------------------------------------------------------------ nothing saved on trust


def test_numbers_that_do_not_reproduce_the_fitted_model_stop_the_run(scored):
    _, _, artifacts, inputs = scored
    from oa_market_intelligence.modeling.design import calendar_columns

    parameters = json.loads(artifacts.set_index("model_id").loc["segment:1", "parameters"])
    frame = calendar_columns(inputs, _month_after(ORIGIN, 1))
    expected = logistic_predict(parameters, frame)
    verify(parameters, frame, expected, "segment")  # the true numbers pass
    bent = copy.deepcopy(parameters)
    bent["coef"][0] += 1e-6  # a change of one in a million
    with pytest.raises(ValueError, match="the saved segment model differs from the fitted one"):
        verify(bent, frame, expected, "segment")
    shifted = copy.deepcopy(parameters)
    shifted["intercept"] += 0.5
    with pytest.raises(ValueError, match="differs"):
        verify(shifted, frame, expected, "alert")


# ------------------------------------------------------------------ the pipeline stage


def _staged_engine(tmp_path, data, monkeypatch):
    seg, monthly = data
    engine = create_engine(f"sqlite:///{(tmp_path / 'w.db').as_posix()}")
    scores = pd.DataFrame(
        [
            ("T1", 1, "main", "last_month", 1, None),
            ("T1", 3, "main", "last_month", 1, None),
            ("T1", 6, "main", "last_month", 1, None),
            ("T2", 1, "main", "logistic", 1, 0.055),
            ("T2", 3, "main", "logistic", 1, 0.109),
            ("T2", 6, "main", "last_month", 1, None),
            ("T3", 1, "main", "logistic", 1, None),
        ],
        columns=["task", "horizon", "scenario", "method", "serving", "bias_vs_actual"],
    )
    files = pd.DataFrame([{"file_name": "x.xlsx", "sha256": "1" * 64}])
    with engine.begin() as conn:
        monthly.to_sql("gold_visit_share_monthly", conn, index=False)
        scores.to_sql("model_scores", conn, index=False)
        files.to_sql("bronze_ingest_files", conn, index=False)
    monkeypatch.setattr("oa_market_intelligence.features.segment.load_segment_gold", lambda e: seg)
    path = tmp_path / "serving.json"
    path.write_text(json.dumps(SERVING))
    return engine, path


def test_the_stage_writes_the_models_and_inputs_beside_the_forecasts_and_is_repeatable(
    tmp_path, data, monkeypatch
):
    engine, path = _staged_engine(tmp_path, data, monkeypatch)
    first = write_predictions(engine, path)
    one = {
        t: pd.read_sql(f"SELECT * FROM {t}", engine)
        for t in ("pred_batch", ARTIFACT_TABLE, INPUT_TABLE)
    }
    second = write_predictions(engine, path)  # replaced, not appended
    two = {t: pd.read_sql(f"SELECT * FROM {t}", engine) for t in one}
    assert first == second
    assert first[ARTIFACT_TABLE] == 4 and first[INPUT_TABLE] == len(one[INPUT_TABLE]) > 0
    for table in one:
        pd.testing.assert_frame_equal(one[table], two[table])  # same bytes of numbers each run
    assert set(one[ARTIFACT_TABLE]["data_version"]) == {first["data_version"]}
    assert set(one["pred_batch"]["data_version"]) == {first["data_version"]}


def test_a_predictor_made_from_the_written_tables_reproduces_the_written_forecasts(
    tmp_path, data, monkeypatch
):
    engine, path = _staged_engine(tmp_path, data, monkeypatch)
    write_predictions(engine, path)
    batch = pd.read_sql("SELECT * FROM pred_batch WHERE level = 'segment'", engine)
    p = Predictor(
        pd.read_sql(f"SELECT * FROM {ARTIFACT_TABLE}", engine),
        pd.read_sql(f"SELECT * FROM {INPUT_TABLE}", engine),
    )
    worst = 0.0
    for r in batch.itertuples():
        got = p.segment(r.specialty_name, r.age_band, r.gender, int(r.horizon))
        worst = max(worst, abs(got["forecast_pct"] / 100 - r.point_share))
    assert len(batch) > 0 and worst < 1e-6  # the stored shares are rounded; the models are exact
