"""The API following the data release in S3 (DL-124), through the real app and the real warehouse.

S3 is a fake. The warehouse copies are the published one with only the data version changed (or a
table removed), so the whole snapshot, the saved models and the status page are really built from
each. What must hold: a release is picked up without a restart; a bad pointer, a damaged file and a
release the code cannot load all leave the data being served as it is; a release that cannot be
loaded at start-up falls back to the image's copy.
"""

import json
import shutil
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from oa_market_intelligence import data_release as dr  # noqa: E402
from oa_market_intelligence.api.main import create_app  # noqa: E402
from oa_market_intelligence.api.remote_data import RemoteData  # noqa: E402
from oa_market_intelligence.serving import data  # noqa: E402
from test_remote_data import BUCKET, Clock, FakeS3  # noqa: E402

pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")

REAL = "28b9a7bd3234"
A, B, C = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"


def variant(tmp_path, name, version, *, drop=None):
    """A copy of the published warehouse with another data version (and, optionally, a table
    removed so that the app cannot build its snapshot from it)."""
    dest = tmp_path / f"{name}.db"
    shutil.copy(data.DB_PATH, dest)
    con = sqlite3.connect(dest)
    con.execute("UPDATE pred_batch SET data_version = ?", (version,))
    if drop:
        con.execute(f"DROP TABLE {drop}")
    con.commit()
    con.close()
    return dest


def release(s3, db):
    """Put a warehouse in the fake bucket and point `latest.json` at it."""
    pointer = dr.build_pointer(db, data.RUN_LOG)
    s3.objects[pointer["key"]] = db.read_bytes()
    s3.objects["latest.json"] = json.dumps(pointer).encode()
    return pointer


@pytest.fixture()
def env(tmp_path):
    s3 = FakeS3()
    remote = RemoteData(
        BUCKET, client=s3, baked=data.DB_PATH, cache_dir=tmp_path / "cache",
        refresh_seconds=0, clock=Clock(),
    )  # fmt: skip
    return type("E", (), {"s3": s3, "remote": remote, "tmp": tmp_path})


def health(client):
    body = client.get("/health").json()
    return body["data_version"], body["data_source"]


def test_with_no_release_yet_the_api_serves_the_images_copy(env):
    with TestClient(create_app(api_keys=[], remote=env.remote)) as client:
        assert health(client) == (REAL, "image")


def test_the_api_starts_from_a_release_and_serves_its_data_run_record_and_models(env):
    pointer = release(env.s3, variant(env.tmp, "a", A))
    with TestClient(create_app(api_keys=[], remote=env.remote)) as client:
        assert health(client) == (A, "s3")
        assert client.app.state.snapshot.run == pointer["run"]
        # the saved models and their inputs come from the release too
        assert client.get("/v1/predict/segments").json()["count"] == 113
        models = client.get("/v1/models").json()["models"]
        assert len(models) == 4


def test_a_new_release_is_picked_up_without_a_restart_and_a_rollback_is_followed(env):
    release(env.s3, variant(env.tmp, "a", A))
    with TestClient(create_app(api_keys=[], remote=env.remote)) as client:
        assert health(client) == (A, "s3")
        pointer_b = release(env.s3, variant(env.tmp, "b", B))
        assert health(client)[0] == B  # the next request after the interval switches
        assert client.get("/v1/status").status_code == 200
        assert client.app.state.snapshot.run == pointer_b["run"]
        # the pointer is moved back to the first release: a rollback
        release(env.s3, env.tmp / "a.db")
        assert health(client)[0] == A


@pytest.mark.parametrize("damage", ["garbage_pointer", "damaged_file", "missing_file", "s3_down"])
def test_a_bad_pointer_or_file_or_outage_leaves_the_data_being_served(env, damage):
    release(env.s3, variant(env.tmp, "a", A))
    with TestClient(create_app(api_keys=[], remote=env.remote)) as client:
        assert health(client) == (A, "s3")
        pointer = release(env.s3, variant(env.tmp, "b", B))
        if damage == "garbage_pointer":
            env.s3.objects["latest.json"] = b"not a pointer"
        elif damage == "damaged_file":
            env.s3.objects[pointer["key"]] = b"x" * pointer["size"]
        elif damage == "missing_file":
            del env.s3.objects[pointer["key"]]
        else:
            env.s3.fail = TimeoutError("down")
        for _ in range(3):
            assert health(client) == (A, "s3")  # still the last good release, request after request
        assert client.get("/v1/models").status_code == 200


def test_a_release_the_code_cannot_load_is_refused_and_the_old_data_stay(env, caplog):
    release(env.s3, variant(env.tmp, "a", A))
    with TestClient(create_app(api_keys=[], remote=env.remote)) as client:
        release(env.s3, variant(env.tmp, "c", C, drop="model_scores"))
        assert health(client) == (A, "s3")  # it passed the file checks but cannot be loaded
        assert "could not be loaded" in caplog.text
        assert client.get("/v1/model-results").status_code == 200


def test_a_release_the_code_cannot_load_at_start_up_falls_back_to_the_image(env, caplog):
    release(env.s3, variant(env.tmp, "c", C, drop="model_scores"))
    with TestClient(create_app(api_keys=[], remote=env.remote)) as client:
        assert health(client) == (REAL, "image")
        assert "using the image's copy" in caplog.text
        assert client.get("/v1/status").status_code == 200


def test_the_keys_and_the_limits_apply_whichever_data_are_served(env):
    release(env.s3, variant(env.tmp, "a", A))
    app = create_app(api_keys=["k1"], rate_per_minute=1000, remote=env.remote)
    with TestClient(app) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/v1/models", headers={"X-API-Key": "k1"}).status_code == 200
        assert health(client) == (A, "s3")  # /health needs no key and reports the source
