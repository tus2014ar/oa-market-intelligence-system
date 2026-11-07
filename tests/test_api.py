"""Tests for the read-only API (src/.../api): every endpoint returns what the lookups (and so the
website's pages) show, the key and rate limits work, bad input is a clean error, the interactive
documentation describes every endpoint, and the logs never hold a key.

Needs the API's packages (api/requirements.txt); the CI job `api` installs them and these tests are
skipped where FastAPI is not installed. They run on the committed warehouse.
"""

import json
import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from oa_market_intelligence.api.main import VERSION, create_app  # noqa: E402
from oa_market_intelligence.serving import data, qa_tools, views  # noqa: E402

pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")

V1 = [
    "/v1/status",
    "/v1/share/monthly",
    "/v1/share/yearly",
    "/v1/segments",
    "/v1/forecasts/national",
    "/v1/forecasts/specialty",
    "/v1/forecasts/alerts",
    "/v1/model-results",
    "/v1/findings",
]
LIVE = ["/v1/models", "/v1/predict/segments", "/v1/predict/segment", "/v1/predict/alert"]
FORBIDDEN = ("patient", "npi", "provider_id", "person_id", "claim")


@pytest.fixture(scope="module")
def snap():
    engine = data.open_engine()
    try:
        return qa_tools.load_snapshot(engine)
    finally:
        engine.dispose()


@pytest.fixture()
def open_client(snap):
    with TestClient(create_app(snapshot=snap, api_keys=[], rate_per_minute=1000)) as client:
        yield client


@pytest.fixture()
def keyed_client(snap):
    app = create_app(snapshot=snap, api_keys=["secret-one", "secret-two"], rate_per_minute=1000)
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def segment_params(snap):
    """A segment the live endpoints know, with a stored alert score: the biggest in the batch."""
    pred = snap.pred
    one = pred[(pred["level"] == "segment") & (pred["horizon"] == 1) & pred["alert_score"].notna()]
    row = one.sort_values("origin_visits", ascending=False).iloc[0]
    return {
        "specialty": row["specialty_name"],
        "age_band": row["age_band"],
        "gender": row["gender"],
    }


def live_params(path, segment_params):
    return segment_params if path.endswith(("/segment", "/alert")) else {}


def _tool(snap, name, **arguments):
    return qa_tools.run_tool(name, arguments, snapshot=snap)


# ------------------------------------------------------------------ health and the documentation


def test_health_needs_no_key_and_says_how_current_the_data_are_and_whether_a_key_is_needed(
    open_client, keyed_client, snap
):
    expected_version = str(snap.pred["data_version"].iloc[0])
    assert len(expected_version) == 12 and expected_version != "unknown"  # a real run's hash
    body = open_client.get("/health").json()
    assert body == {
        "status": "ok",
        "version": VERSION,
        "data_through": "Jul 2025",
        "months_of_data": 72,
        "data_version": expected_version,
        "auth": "open",
    }
    assert keyed_client.get("/health").json()["auth"] == "api-key"  # no key sent, still answers


def test_every_endpoint_is_in_the_interactive_documentation_with_its_errors(open_client):
    spec = open_client.get("/openapi.json").json()
    assert spec["info"]["version"] == VERSION and "OA and RA" in spec["info"]["title"]
    assert "Read these before quoting a number" in spec["info"]["description"]
    assert set(spec["paths"]) == {"/health", *V1, *LIVE}
    for path in [*V1, *LIVE]:
        operation = spec["paths"][path]["get"]
        assert operation["summary"] and operation["description"] and operation["tags"], path
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema, path  # a named, typed response, not a free-form object
        assert {"401", "422", "429"} <= set(operation["responses"]), path
    assert open_client.get("/docs").status_code == 200  # the interactive page


def test_only_reading_is_allowed_and_an_unknown_path_is_a_clean_404(open_client):
    assert open_client.post("/v1/status").status_code == 405
    assert open_client.delete("/v1/findings").status_code == 405
    response = open_client.get("/v1/nothing-here")
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


# ------------------------------------------------------------------ the figures are the site's


def test_every_endpoint_answers_and_returns_what_the_lookup_returns(open_client, snap):
    pairs = {
        "/v1/status": ("get_data_status", {}),
        "/v1/segments": ("get_segments", {"by": "specialty", "top": 10}),
        "/v1/forecasts/national": ("get_forecasts", {"view": "national"}),
        "/v1/forecasts/specialty": ("get_forecasts", {"view": "specialty", "top": 10}),
        "/v1/forecasts/alerts": ("get_forecasts", {"view": "alerts", "top": 10}),
        "/v1/model-results": ("get_model_results", {}),
        "/v1/findings": ("get_findings", {}),
    }
    for path, (tool, arguments) in pairs.items():
        response = open_client.get(path)
        assert response.status_code == 200, path
        expected = _tool(snap, tool, **arguments)
        body = response.json()
        for key, value in expected.items():
            if isinstance(value, list):
                assert len(body[key]) == len(value), (path, key)
            elif isinstance(value, (str, bool)) or value is None:
                assert body[key] == value, (path, key)
            else:
                assert body[key] == pytest.approx(value), (path, key)


def test_the_headline_figures_equal_the_websites(open_client, snap):
    head = views.headline(snap.monthly, snap.pred)
    status = open_client.get("/v1/status").json()
    assert status["latest_share_pct"] == pytest.approx(100 * head["latest_share"], abs=0.005)
    assert status["peak_share_pct"] == pytest.approx(100 * head["peak_share"], abs=0.005)
    assert status["latest_month"] == head["latest_month"] == "Jul 2025"
    national = open_client.get("/v1/forecasts/national").json()["rows"]
    assert [r["months_ahead"] for r in national] == [1, 3, 6]
    assert {r["model"] for r in national} == {"same as last month"}
    assert national[0]["low_90_pct"] < national[0]["forecast_pct"] < national[0]["high_90_pct"]


def test_the_monthly_series_can_be_limited_to_a_range_and_is_in_order(open_client):
    rows = open_client.get(
        "/v1/share/monthly", params={"start_month_id": 202501, "end_month_id": 202506}
    ).json()["rows"]
    assert [r["month_id"] for r in rows] == [202501, 202502, 202503, 202504, 202505, 202506]
    everything = open_client.get("/v1/share/monthly").json()["rows"]
    assert len(everything) == 72 and everything[0]["month_id"] == 201908
    yearly = open_client.get("/v1/share/yearly").json()["rows"]
    assert [r["year"] for r in yearly] == [2019, 2020, 2021, 2022, 2023, 2024, 2025]
    assert yearly[0]["partial_year"] and yearly[-1]["partial_year"]


def test_segments_can_be_grouped_three_ways_and_the_forecast_caveats_are_there(open_client):
    for by in ("specialty", "age_band", "gender"):
        body = open_client.get("/v1/segments", params={"by": by, "top": 3}).json()
        assert body["by"] == by and 1 <= len(body["rows"]) <= 3
        assert all(r["category_visits"] >= 1000 for r in body["rows"])
    specialty = open_client.get("/v1/forecasts/specialty", params={"top": 2}).json()
    assert "run high" in specialty["bias"] and len(specialty["rows"]) == 2
    assert {"1_month_pct", "3_month_pct", "6_month_pct"} <= set(specialty["rows"][0])
    alerts = open_client.get("/v1/forecasts/alerts", params={"top": 3}).json()
    assert (
        "1-month" in alerts["note"] and alerts["summary"]["flagged"] <= alerts["summary"]["scored"]
    )
    national = open_client.get("/v1/forecasts/national").json()
    assert "simple rule" in national["note"].lower() or "same as last month" in national["note"]


def test_the_limits_of_the_analysis_travel_with_the_status(open_client):
    status = open_client.get("/v1/status").json()
    assert status["freshness"] == "stale" and status["age_days"] > 100
    assert status["limits"] and all(isinstance(line, str) for line in status["limits"])
    findings = open_client.get("/v1/findings").json()
    assert findings["count"] == 17 and len(findings["findings"]) == 17
    only = open_client.get("/v1/findings", params={"keyword": "alert"}).json()
    assert 1 <= only["count"] < 17 and "alert" in json.dumps(only["findings"]).lower()
    assert open_client.get("/v1/findings", params={"keyword": "zzzzzzqq"}).json()["count"] == 0


def _field_names(value):
    """Every field name in a JSON body, at any depth (values are text, not fields)."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _field_names(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _field_names(inner)


def test_only_aggregates_are_served_and_lists_are_capped(open_client, segment_params):
    for path in [*V1, *LIVE]:
        response = open_client.get(path, params=live_params(path, segment_params))
        names = list(_field_names(response.json()))
        assert names, path
        for name in names:
            assert not any(word in name.lower() for word in FORBIDDEN), (path, name)
    rows = open_client.get("/v1/segments", params={"by": "specialty", "top": 25}).json()["rows"]
    assert len(rows) <= qa_tools.MAX_TOP


# ------------------------------------------------------------------ bad input is a clean error


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/v1/segments", {"by": "payer"}),
        ("/v1/segments", {"top": 0}),
        ("/v1/segments", {"top": 500}),
        ("/v1/forecasts/specialty", {"top": -1}),
        ("/v1/forecasts/alerts", {"top": "many"}),
        ("/v1/share/monthly", {"start_month_id": 1}),
        ("/v1/share/monthly", {"end_month_id": "soon"}),
        ("/v1/findings", {"keyword": "x" * 200}),
    ],
)
def test_a_value_the_api_cannot_use_is_a_422_that_names_the_problem(open_client, path, params):
    response = open_client.get(path, params=params)
    assert response.status_code == 422
    assert response.json()["detail"]


def test_a_range_with_no_months_in_it_is_a_404_not_an_empty_success(open_client):
    response = open_client.get(
        "/v1/share/monthly", params={"start_month_id": 209901, "end_month_id": 209912}
    )
    assert response.status_code == 404 and "No months" in response.json()["detail"]


# ------------------------------------------------------------------ keys and rate limits


def test_with_keys_configured_every_v1_endpoint_needs_one_of_them(keyed_client, segment_params):
    for path in [*V1, *LIVE]:
        params = live_params(path, segment_params)
        response = keyed_client.get(path, params=params)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "ApiKey"
        wrong = keyed_client.get(path, params=params, headers={"X-API-Key": "wrong"})
        assert wrong.status_code == 401
        for key in ("secret-one", "secret-two"):
            ok = keyed_client.get(path, params=params, headers={"X-API-Key": key})
            assert ok.status_code == 200, path


def test_the_key_is_never_echoed_back_in_any_response(keyed_client, segment_params):
    for path in ("/health", *V1, *LIVE):
        response = keyed_client.get(
            path, params=live_params(path, segment_params), headers={"X-API-Key": "secret-one"}
        )
        assert "secret-one" not in response.text, path
        assert "secret-one" not in json.dumps(dict(response.headers)), path
    wrong = keyed_client.get("/v1/status", headers={"X-API-Key": "secret-wrong"})
    assert "secret-wrong" not in wrong.text


def test_each_client_has_its_own_rate_and_health_is_not_counted(snap):
    app = create_app(snapshot=snap, api_keys=["a", "b"], rate_per_minute=3)
    with TestClient(app) as client:
        for _ in range(10):
            assert client.get("/health").status_code == 200  # never limited
        ok = [client.get("/v1/status", headers={"X-API-Key": "a"}).status_code for _ in range(5)]
        assert ok == [200, 200, 200, 429, 429]
        limited = client.get("/v1/status", headers={"X-API-Key": "a"})
        assert limited.headers["retry-after"] == "60" and "Too many" in limited.json()["detail"]
        assert client.get("/v1/status", headers={"X-API-Key": "b"}).status_code == 200  # own rate


def test_an_open_api_limits_by_address_and_a_wrong_key_does_not_use_up_a_rate(snap):
    with TestClient(create_app(snapshot=snap, api_keys=[], rate_per_minute=2)) as client:
        codes = [client.get("/v1/status").status_code for _ in range(4)]
        assert codes == [200, 200, 429, 429]
    keyed = create_app(snapshot=snap, api_keys=["a"], rate_per_minute=2)
    with TestClient(keyed) as client:
        for _ in range(5):
            assert client.get("/v1/status", headers={"X-API-Key": "nope"}).status_code == 401
        assert client.get("/v1/status", headers={"X-API-Key": "a"}).status_code == 200


def test_keys_and_the_rate_come_from_the_environment(snap, monkeypatch):
    monkeypatch.setenv("API_KEYS", " alpha , beta ,, ")
    monkeypatch.setenv("API_RATE_PER_MINUTE", "1")
    with TestClient(create_app(snapshot=snap)) as client:
        assert client.get("/health").json()["auth"] == "api-key"
        assert client.get("/v1/status", headers={"X-API-Key": "alpha"}).status_code == 200
        assert client.get("/v1/status", headers={"X-API-Key": "alpha"}).status_code == 429
        assert client.get("/v1/status", headers={"X-API-Key": "beta"}).status_code == 200
        assert client.get("/v1/status", headers={"X-API-Key": "gamma"}).status_code == 401
    monkeypatch.delenv("API_KEYS")
    with TestClient(create_app(snapshot=snap)) as client:
        assert client.get("/health").json()["auth"] == "open"


# ------------------------------------------------------------------ live predictions (DL-117)


def test_the_saved_models_are_listed_with_what_they_were_fitted_on(open_client):
    body = open_client.get("/v1/models").json()
    assert body["count"] == len(body["models"]) == 4
    by_id = {m["model_id"]: m for m in body["models"]}
    assert list(by_id) == ["alert:1", "segment:1", "segment:3", "segment:6"]
    assert by_id["segment:1"]["model"] == by_id["segment:3"]["model"] == "logistic"
    assert by_id["segment:6"]["model"] == "last_month" and by_id["segment:6"]["n_parameters"] == 0
    for model in by_id.values():
        assert len(model["artifact_sha256"]) == 64 and model["trained_on_rows"] > 0
        assert model["run_month_id"] == 202507
    assert by_id["segment:1"]["known_bias_pct"] > 0  # the forecasts ran high in testing


def test_the_segments_the_live_endpoints_know_are_those_with_a_stored_forecast(open_client, snap):
    body = open_client.get("/v1/predict/segments").json()
    pred = snap.pred
    stored = pred[(pred["level"] == "segment") & (pred["horizon"] == 1)]
    assert body["count"] == len(body["segments"]) == len(stored)
    keys = {(s["specialty"], s["age_band"], s["gender"]) for s in body["segments"]}
    both = zip(stored["specialty_name"], stored["age_band"], stored["gender"], strict=True)
    assert keys == set(both)


def test_every_segment_and_horizon_is_predicted_live_to_the_stored_forecast(open_client, snap):
    pred = snap.pred
    segment = pred[pred["level"] == "segment"]
    assert len(segment) > 100
    for r in segment.itertuples():
        body = open_client.get(
            "/v1/predict/segment",
            params={
                "specialty": r.specialty_name,
                "age_band": r.age_band,
                "gender": r.gender,
                "horizon": int(r.horizon),
            },
        ).json()
        assert body["matches_stored"] is True, (r.specialty_name, r.age_band, r.gender, r.horizon)
        assert body["forecast_pct"] == pytest.approx(100 * r.point_share, abs=1e-3)
        assert body["target_month_id"] == r.target_month_id and body["model"] == r.model


def test_every_alert_is_scored_live_to_the_stored_score_and_flag(open_client, snap):
    pred = snap.pred
    one = pred[(pred["level"] == "segment") & (pred["horizon"] == 1)]
    scored = one[one["alert_score"].notna()]
    assert len(scored) > 10
    for r in scored.itertuples():
        body = open_client.get(
            "/v1/predict/alert",
            params={"specialty": r.specialty_name, "age_band": r.age_band, "gender": r.gender},
        ).json()
        assert body["eligible"] is True and body["matches_stored"] is True
        assert body["alert_score"] == pytest.approx(r.alert_score, abs=1e-6)
        assert body["flagged"] == bool(r.alert_flag)
    unscored = one[one["alert_score"].isna()].iloc[0]
    body = open_client.get(
        "/v1/predict/alert",
        params={
            "specialty": unscored["specialty_name"],
            "age_band": unscored["age_band"],
            "gender": unscored["gender"],
        },
    ).json()
    assert body["eligible"] is False and body["reason"] and body["alert_score"] is None
    assert body["matches_stored"] is None


def test_a_live_forecast_carries_its_limits_and_compares_with_last_month(
    open_client, segment_params
):
    body = open_client.get("/v1/predict/segment", params={**segment_params, "horizon": 3}).json()
    assert body["horizon"] == 3 and body["model"] == "logistic"
    change = body["forecast_pct"] - body["last_month_pct"]
    assert body["change_pp"] == pytest.approx(change, abs=2e-4)
    assert body["known_bias_pct"] > 0 and "optimistic" in body["note"]
    six = open_client.get("/v1/predict/segment", params={**segment_params, "horizon": 6}).json()
    assert six["model"] == "last_month" and six["forecast_pct"] == six["last_month_pct"]
    default = open_client.get("/v1/predict/segment", params=segment_params).json()
    assert default["horizon"] == 1  # one month when none is asked for
    alert = open_client.get("/v1/predict/alert", params=segment_params).json()
    assert "supporting flag" in alert["note"]


def test_names_are_matched_without_regard_to_case(open_client, segment_params):
    lower = {k: v.lower() for k, v in segment_params.items()}
    a = open_client.get("/v1/predict/segment", params=segment_params).json()
    b = open_client.get("/v1/predict/segment", params=lower).json()
    assert a == b


def test_an_unknown_segment_is_a_404_and_an_unserved_horizon_a_422(open_client, segment_params):
    missing = open_client.get(
        "/v1/predict/segment", params={**segment_params, "specialty": "NOT A SPECIALTY"}
    )
    assert missing.status_code == 404 and "No segment" in missing.json()["detail"]
    unknown = open_client.get("/v1/predict/alert", params={**segment_params, "gender": "UNKNOWN"})
    assert unknown.status_code == 404
    two = open_client.get("/v1/predict/segment", params={**segment_params, "horizon": 2})
    assert two.status_code == 422 and "served: [1, 3, 6]" in two.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [{"horizon": 0}, {"horizon": 13}, {"horizon": "soon"}, {"specialty": "x" * 200}],
)
def test_a_live_request_the_api_cannot_use_is_a_422(open_client, segment_params, params):
    response = open_client.get("/v1/predict/segment", params={**segment_params, **params})
    assert response.status_code == 422 and response.json()["detail"]


def test_the_segment_is_required_for_a_live_forecast(open_client):
    assert open_client.get("/v1/predict/segment").status_code == 422
    assert open_client.get("/v1/predict/alert").status_code == 422


def test_with_no_saved_models_the_live_endpoints_say_503_and_the_rest_still_work(snap):
    with TestClient(create_app(snapshot=snap, api_keys=[], live_predictions=False)) as client:
        for path in ("/v1/models", "/v1/predict/segments"):
            response = client.get(path)
            assert response.status_code == 503 and "no saved models" in response.json()["detail"]
        params = {"specialty": "RHEUMATOLOGY", "age_band": "65 TO 74", "gender": "FEMALE"}
        assert client.get("/v1/predict/segment", params=params).status_code == 503
        assert client.get("/v1/status").status_code == 200


def test_a_warehouse_without_the_saved_model_tables_gives_empty_frames_not_an_error(tmp_path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    artifacts, inputs = data.saved_models(engine)
    assert artifacts.empty and inputs.empty


# ------------------------------------------------------------------ logs, browsers, start-up


def test_each_request_logs_one_json_line_that_never_holds_a_key(keyed_client, caplog):
    caplog.set_level(logging.INFO, logger="oa_api")
    keyed_client.get("/v1/status", headers={"X-API-Key": "secret-one"})
    keyed_client.get("/v1/status", headers={"X-API-Key": "secret-wrong"})
    lines = [json.loads(r.message) for r in caplog.records if r.name == "oa_api"]
    assert [(r["method"], r["path"], r["status"]) for r in lines] == [
        ("GET", "/v1/status", 200),
        ("GET", "/v1/status", 401),
    ]
    assert all(isinstance(r["ms"], float) for r in lines)
    assert "secret" not in caplog.text


def test_a_browser_page_on_another_origin_may_read_but_not_write(open_client):
    response = open_client.get("/health", headers={"Origin": "https://example.org"})
    assert response.headers["access-control-allow-origin"] == "*"
    preflight = open_client.options(
        "/v1/status",
        headers={"Origin": "https://example.org", "Access-Control-Request-Method": "DELETE"},
    )
    assert preflight.status_code == 400  # DELETE is not an allowed method


def test_the_service_loads_the_published_warehouse_itself_when_given_no_snapshot():
    with TestClient(create_app(api_keys=[])) as client:  # the start-up loads it
        body = client.get("/health").json()
        assert body["data_through"] == "Jul 2025" and body["months_of_data"] == 72
        assert client.get("/v1/status").json()["latest_month"] == "Jul 2025"
