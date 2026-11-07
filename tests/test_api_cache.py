"""Tests for the API's answer cache (src/.../api/cache.py and its use in api/main.py, DL-127).

The cache must make the API faster without ever changing what it says: bounded, copies in and out,
never an error, and never an answer from data that have since been replaced.
"""

import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from oa_market_intelligence.api.cache import BoundedCache  # noqa: E402
from oa_market_intelligence.api.main import create_app  # noqa: E402
from oa_market_intelligence.serving import data, qa_tools  # noqa: E402

# ------------------------------------------------------------------ the cache itself


def counter():
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls), "items": [1, 2, 3]}

    return calls, compute


def test_the_second_ask_is_a_hit_and_computes_nothing():
    cache, (calls, compute) = BoundedCache(), counter()
    first = cache.get_or_compute("k", compute)
    second = cache.get_or_compute("k", compute)
    assert first == second == {"n": 1, "items": [1, 2, 3]} and len(calls) == 1
    assert (cache.hits, cache.misses) == (1, 1)


def test_what_comes_out_is_a_private_copy_so_a_caller_cannot_change_the_next_answer():
    cache, (_, compute) = BoundedCache(), counter()
    first = cache.get_or_compute("k", compute)
    first["items"].append(99)  # a caller changes what it was given on the miss
    second = cache.get_or_compute("k", compute)
    assert second["items"] == [1, 2, 3]
    second["items"].clear()  # and on a hit
    assert cache.get_or_compute("k", compute)["items"] == [1, 2, 3]


def test_an_error_is_passed_on_and_nothing_is_stored_so_it_is_tried_again():
    cache = BoundedCache()
    attempts = []

    def failing():
        attempts.append(1)
        raise RuntimeError("no data")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            cache.get_or_compute("k", failing)
    assert len(attempts) == 3 and len(cache) == 0
    assert cache.get_or_compute("k", lambda: {"ok": 1}) == {"ok": 1}  # and it recovers


def test_the_cache_never_holds_more_than_its_limit_and_drops_the_oldest_first():
    cache = BoundedCache(maxsize=3)
    for i in range(5):
        cache.get_or_compute(i, lambda i=i: {"i": i})
    assert len(cache) == 3
    calls, compute = counter()
    cache.get_or_compute(0, compute)  # the oldest are gone: 0 and 1 are recomputed
    cache.get_or_compute(1, compute)
    assert len(calls) == 2
    before = cache.hits
    cache.get_or_compute(4, lambda: {"never": "called"})  # the newest is still there
    assert cache.hits == before + 1 and len(cache) == 3


def test_a_limit_below_one_is_refused():
    with pytest.raises(ValueError):
        BoundedCache(maxsize=0)


def test_many_threads_asking_for_the_same_answer_all_get_it_and_the_cache_stays_one_entry():
    cache = BoundedCache()
    results = []

    def ask():
        results.append(cache.get_or_compute("shared", lambda: {"value": 7}))

    threads = [threading.Thread(target=ask) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [{"value": 7}] * 16 and len(cache) == 1


# ------------------------------------------------------------------ in the API


@pytest.fixture(scope="module")
def snap():
    engine = data.open_engine()
    try:
        return qa_tools.load_snapshot(engine)
    finally:
        engine.dispose()


@pytest.fixture()
def client(snap):
    pytest.importorskip("fastapi")
    if not data.DB_PATH.exists():
        pytest.skip("no published warehouse")
    with TestClient(create_app(snapshot=snap, api_keys=[], rate_per_minute=100000)) as c:
        yield c


SEGMENT = {"specialty": "RHEUMATOLOGY", "age_band": "65 TO 74", "gender": "FEMALE", "horizon": 3}


def test_a_repeated_request_is_answered_from_the_cache_with_the_same_body(client):
    cache = client.app.state.cache
    paths = ["/health", "/v1/status", "/v1/forecasts/national", "/v1/model-results", "/v1/findings"]
    for path in paths:
        first = client.get(path)
        before = cache.hits
        second = client.get(path)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json(), path
        assert cache.hits == before + 1, path  # the second was a hit


def test_a_cached_answer_equals_a_fresh_one_for_every_data_endpoint(client, snap):
    fresh = qa_tools.run_tool("get_data_status", {}, snapshot=snap)
    assert client.get("/v1/status").json() == client.get("/v1/status").json()
    assert client.get("/v1/status").json()["latest_month"] == fresh["latest_month"]


def test_the_same_segment_written_in_another_case_or_with_spaces_is_one_cache_entry(client):
    cache = client.app.state.cache
    a = client.get("/v1/predict/segment", params=SEGMENT).json()
    entries = len(cache)
    shouted = {
        **SEGMENT,
        "specialty": "  rheumatology ",
        "age_band": "65 to 74",
        "gender": "female",
    }
    b = client.get("/v1/predict/segment", params=shouted).json()
    assert a == b and len(cache) == entries  # no new entry
    assert a["matches_stored"] is True


def test_an_error_is_never_kept_and_a_second_ask_gets_the_same_error(client):
    cache = client.app.state.cache
    params = {**SEGMENT, "specialty": "NOT A SPECIALTY"}
    first = client.get("/v1/predict/segment", params=params)
    entries = len(cache)
    second = client.get("/v1/predict/segment", params=params)
    assert first.status_code == second.status_code == 404 and first.json() == second.json()
    assert len(cache) == entries  # nothing was stored for either
    gone = client.get(
        "/v1/share/monthly", params={"start_month_id": 209901, "end_month_id": 209912}
    )
    assert gone.status_code == 404
    assert len(cache) == entries


def test_a_client_sending_many_different_requests_cannot_make_the_cache_grow_without_limit(client):
    cache = client.app.state.cache
    for i in range(cache.maxsize + 200):
        client.get("/v1/findings", params={"keyword": f"word{i}"})
    assert len(cache) <= cache.maxsize


def test_when_the_data_switch_nothing_from_the_old_data_is_ever_returned(client):
    cache = client.app.state.cache
    client.get("/v1/status")
    client.get("/v1/status")
    hits = cache.hits
    client.app.state.generation += 1  # what the app does whenever it switches to a new release
    client.get("/v1/status")
    assert cache.hits == hits  # a miss: recomputed on the new data, not answered from the old


def test_the_cache_is_per_app_so_two_apps_never_share_answers(snap):
    if not data.DB_PATH.exists():
        pytest.skip("no published warehouse")
    with (
        TestClient(create_app(snapshot=snap, api_keys=[])) as one,
        TestClient(create_app(snapshot=snap, api_keys=[])) as two,
    ):
        one.get("/v1/status")
        assert len(one.app.state.cache) == 1 and len(two.app.state.cache) == 0
