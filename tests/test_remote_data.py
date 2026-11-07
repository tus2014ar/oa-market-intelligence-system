"""Tests for how the API finds its warehouse (src/.../api/remote_data.py, DL-124).

S3 is replaced by a fake that can fail in every way that matters. What must hold: a release is
used only when it passes every check; anything that goes wrong keeps what is being served (the
image's copy at start-up); a pointer can never make the loader fetch any other object; and the
loader never writes outside its own cache directory."""

import io
import json
import sys
from datetime import UTC, datetime

import pytest

from oa_market_intelligence import data_release as dr
from oa_market_intelligence.api import remote_data as rd
from oa_market_intelligence.api.remote_data import RemoteData, Source
from test_data_release import make_db, make_log

BUCKET = "oa-data-test"
NOW = datetime(2026, 11, 7, tzinfo=UTC)
OLD, NEW = "aaaaaaaaaaaa", "bbbbbbbbbbbb"


class NoSuchKey(Exception):
    pass


class FakeS3:
    """A bucket in a dict. `fail` makes every call raise; `calls` records the keys asked for."""

    def __init__(self, objects=None, fail=None):
        self.objects = dict(objects or {})
        self.fail = fail
        self.calls = []

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3's argument names
        self.calls.append((Bucket, Key))
        if self.fail:
            raise self.fail
        if Bucket != BUCKET or Key not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def world(tmp_path):
    """A baked copy (data version OLD), a release of a newer warehouse (NEW), and the pieces."""
    baked = make_db(tmp_path / "baked.db", version=OLD, month=202507)
    release_db = make_db(tmp_path / "release.db", version=NEW, month=202508)
    log = make_log(tmp_path / "log.jsonl", [{"status": "ok", "from": "release"}])
    pointer = dr.build_pointer(release_db, log, now=NOW)
    s3 = FakeS3(
        {"latest.json": json.dumps(pointer).encode(), pointer["key"]: release_db.read_bytes()}
    )
    clock = Clock()
    cache = tmp_path / "cache"

    def loader(**kwargs):
        options = {"client": s3, "baked": baked, "cache_dir": cache, "clock": clock}
        return RemoteData(BUCKET, **{**options, **kwargs})

    return type("W", (), {"baked": baked, "release_db": release_db, "pointer": pointer, "s3": s3,
                          "clock": clock, "cache": cache, "loader": staticmethod(loader),
                          "log": log, "tmp": tmp_path})  # fmt: skip


def put_release(world, tmp_path, name, version, month):
    """Another release in the bucket; returns its pointer."""
    db = make_db(tmp_path / f"{name}.db", version=version, month=month)
    pointer = dr.build_pointer(db, world.log, now=NOW)
    world.s3.objects[pointer["key"]] = db.read_bytes()
    world.s3.objects["latest.json"] = json.dumps(pointer).encode()
    return pointer


# ------------------------------------------------------------------ at start-up


def test_with_a_valid_release_the_loader_starts_from_it_not_from_the_image(world):
    source = world.loader().initial()
    assert source.origin == "s3" and source.data_version == NEW
    assert source.sha256 == world.pointer["sha256"] and source.run == {
        "status": "ok",
        "from": "release",
    }
    assert source.path.parent == world.cache and source.path.exists()
    dr.verify_database(source.path, world.pointer)  # the cached file is the verified one
    assert [k for _, k in world.s3.calls] == ["latest.json", world.pointer["key"]]


def test_with_no_pointer_yet_the_loader_serves_the_images_copy(world, caplog):
    world.s3.objects.clear()
    source = world.loader().initial()
    assert source.origin == "image" and source.data_version == OLD and source.path == world.baked
    assert "starting from the image's copy" in caplog.text
    assert not world.cache.exists() or not list(world.cache.iterdir())


@pytest.mark.parametrize(
    "error", [PermissionError("denied"), TimeoutError("slow"), OSError("down")]
)
def test_an_s3_error_at_start_up_falls_back_to_the_image(world, error):
    world.s3.fail = error
    assert world.loader().initial().origin == "image"


def test_no_s3_client_at_all_falls_back_to_the_image(world, monkeypatch):
    monkeypatch.setattr(rd, "default_client", lambda: None)
    assert world.loader(client=None).initial().origin == "image"


@pytest.mark.parametrize("damage", ["checksum", "truncated", "oversized", "not_a_database"])
def test_a_release_that_fails_a_check_is_not_used_and_leaves_nothing_behind(world, damage):
    key = world.pointer["key"]
    blob = world.release_db.read_bytes()
    world.s3.objects[key] = {
        "checksum": blob[:-1] + bytes([blob[-1] ^ 0xFF]),
        "truncated": blob[:-100],
        "oversized": blob + b"x" * 100,
        "not_a_database": b"x" * len(blob),
    }[damage]
    source = world.loader().initial()
    assert source.origin == "image"
    assert not list(world.cache.glob("*")), "a failed download must not stay in the cache"


def test_a_pointer_cannot_make_the_loader_fetch_any_other_object(world):
    evil = {**world.pointer, "key": "../../secrets/other.db"}
    world.s3.objects["latest.json"] = json.dumps(evil).encode()
    world.s3.objects["../../secrets/other.db"] = b"secret"
    assert world.loader().initial().origin == "image"
    assert all(key == "latest.json" for _, key in world.s3.calls)  # nothing else was requested


@pytest.mark.parametrize("body", ["not json", "[]", '{"schema": 1}', "", "huge"])
def test_a_bad_pointer_is_not_used(world, body):
    world.s3.objects["latest.json"] = (
        b"x" * (70 * 1024)
        if body == "huge"
        else body.encode()  # built here: a long id breaks Windows
    )
    assert world.loader().initial().origin == "image"


def test_a_pointer_that_promises_a_different_size_is_caught_while_downloading(world):
    lying = {**world.pointer, "size": world.pointer["size"] - 50}
    world.s3.objects["latest.json"] = json.dumps(lying).encode()
    assert world.loader().initial().origin == "image"
    assert not list(world.cache.glob("*"))


def test_the_cached_file_is_reused_without_downloading_again(world):
    first = world.loader().initial()
    world.s3.calls.clear()
    second = world.loader().initial()  # a warm start in the same environment
    assert second.path == first.path
    assert [k for _, k in world.s3.calls] == ["latest.json"]  # only the pointer was fetched


def test_a_damaged_cached_file_is_replaced_not_trusted(world):
    first = world.loader().initial()
    first.path.write_bytes(b"corrupt")
    again = world.loader().initial()
    assert again.origin == "s3"
    dr.verify_database(again.path, world.pointer)


# ------------------------------------------------------------------ while running


def test_nothing_is_checked_until_the_interval_has_passed(world):
    loader = world.loader(refresh_seconds=300)
    current = loader.initial()
    world.s3.calls.clear()
    world.clock.now += 299
    assert loader.due() is False and loader.poll(current) is None
    assert world.s3.calls == []  # no request to S3 inside the interval
    world.clock.now += 2
    assert loader.due() is True


def test_when_the_pointer_is_unchanged_the_loader_keeps_serving_what_it_has(world):
    loader = world.loader()
    current = loader.initial()
    world.clock.now += 400
    world.s3.calls.clear()
    assert loader.poll(current) is None
    assert [k for _, k in world.s3.calls] == ["latest.json"]  # one small request, no download
    assert loader.due() is False  # the interval starts again


def test_a_new_release_is_verified_and_replaces_the_old_file_in_the_cache(world):
    loader = world.loader()
    current = loader.initial()
    newer = put_release(world, world.tmp, "third", "cccccccccccc", 202509)
    world.clock.now += 400
    switched = loader.poll(current)
    assert switched is not None and switched.data_version == "cccccccccccc"
    assert switched.sha256 == newer["sha256"] and switched.origin == "s3"
    assert [p.name for p in world.cache.glob("*.db")] == [switched.path.name]  # one file only


def test_a_pointer_moved_back_to_an_older_release_is_followed(world):
    loader = world.loader()
    current = loader.initial()
    put_release(world, world.tmp, "third", "cccccccccccc", 202509)
    world.clock.now += 400
    current = loader.poll(current)
    # an operator moves the pointer back to the first release: a rollback
    world.s3.objects["latest.json"] = json.dumps(world.pointer).encode()
    world.clock.now += 400
    back = loader.poll(current)
    assert back is not None and back.data_version == NEW


@pytest.mark.parametrize("failure", ["s3_down", "bad_pointer", "bad_file", "missing_file"])
def test_when_anything_goes_wrong_the_loader_keeps_what_it_is_serving(world, failure, caplog):
    loader = world.loader()
    current = loader.initial()
    newer = put_release(world, world.tmp, "third", "cccccccccccc", 202509)
    if failure == "s3_down":
        world.s3.fail = TimeoutError("down")
    elif failure == "bad_pointer":
        world.s3.objects["latest.json"] = b"garbage"
    elif failure == "bad_file":
        world.s3.objects[newer["key"]] = b"x" * newer["size"]
    else:
        del world.s3.objects[newer["key"]]
    world.clock.now += 400
    assert loader.poll(current) is None
    assert "keeping the current data" in caplog.text
    assert current.path.exists()  # the file being served is untouched
    world.clock.now += 1
    assert loader.due() is False  # a failure does not make the loader retry on every request


def test_a_second_check_while_one_is_running_does_nothing(world):
    loader = world.loader()
    current = loader.initial()
    world.clock.now += 400
    world.s3.calls.clear()
    assert loader._lock.acquire(blocking=False)
    try:
        assert loader.poll(current) is None
        assert world.s3.calls == []
    finally:
        loader._lock.release()


# ------------------------------------------------------------------ settings and the image copy


def test_the_loader_is_made_from_the_environment_only_when_a_bucket_is_named(monkeypatch):
    monkeypatch.delenv("OA_DATA_BUCKET", raising=False)
    assert RemoteData.from_environment() is None
    monkeypatch.setenv("OA_DATA_BUCKET", "   ")
    assert RemoteData.from_environment() is None
    monkeypatch.setenv("OA_DATA_BUCKET", "my-bucket")
    monkeypatch.setenv("OA_DATA_REFRESH_SECONDS", "42")
    loader = RemoteData.from_environment(client=FakeS3())
    assert loader is not None and loader._bucket == "my-bucket" and loader._every == 42.0


def test_the_default_client_is_none_where_boto3_is_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)  # makes `import boto3` raise ImportError
    assert rd.default_client() is None


def test_the_images_copy_is_described_by_its_own_facts(world):
    source = world.loader().baked()
    assert isinstance(source, Source) and source.origin == "image"
    assert source.data_version == OLD and source.sha256 == dr.sha256_file(world.baked)


def test_no_message_in_the_log_names_the_bucket_a_key_or_a_path(world, caplog):
    world.s3.objects["latest.json"] = b"garbage"
    world.loader().initial()
    world.s3.fail = PermissionError(f"denied for {BUCKET} {world.pointer['key']} {world.cache}")
    world.loader().initial()
    for secret in (BUCKET, world.pointer["key"], str(world.cache), str(world.baked)):
        assert secret not in caplog.text
