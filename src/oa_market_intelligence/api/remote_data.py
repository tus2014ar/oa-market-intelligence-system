"""Where the API gets its warehouse from (DL-124): the image, or the current release in S3.

The API image carries a copy of the published warehouse. With `OA_DATA_BUCKET` set it also looks at
the bucket's pointer (`latest.json`, see `data_release`) when it starts and again at most every
`OA_DATA_REFRESH_SECONDS` (default 300) while it runs, and switches to the warehouse the pointer
names, so a new month of data reaches the API with no image rebuild.

Rules that keep this safe:
- A file is used only after it passes `data_release.verify_database` (size, SHA-256, a readable
  warehouse, the data version and month the pointer names). A pointer may only name keys of one
  fixed shape (`published/<version>-<checksum>/warehouse.db`).
- Anything that goes wrong (no pointer yet, no access, a network error, a bad or half-written file)
  keeps what the API is already serving. The copy in the image is the fallback at start-up.
- It never writes anywhere but its own cache directory, one file per checksum.
- Messages in the log name the failure, never a key or a credential.

The S3 client is passed in, so the tests use a fake; the default is boto3, which the Lambda
runtime provides (the API's own requirements do not list it).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oa_market_intelligence import data_release
from oa_market_intelligence.data_release import ReleaseError
from oa_market_intelligence.serving import data

log = logging.getLogger("oa_api")

DEFAULT_REFRESH_SECONDS = 300
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Source:
    """A warehouse the API can serve: where it is, which data version it holds and where it came
    from ('image' or 's3'). `run` is the run record the status page describes (None: read the
    run log in the image)."""

    path: Path
    data_version: str
    origin: str
    sha256: str
    run: dict | None = None


def default_client():
    """A boto3 S3 client with short timeouts, or None where boto3 is not installed."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return None
    config = Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 2})
    return boto3.client("s3", config=config)


class RemoteData:
    """Finds the warehouse to serve. Construct once per app; `initial()` at start-up, then
    `due()` and `poll(current)` while it runs."""

    def __init__(
        self,
        bucket: str,
        *,
        client=None,
        baked: Path | str = data.DB_PATH,
        cache_dir: Path | str | None = None,
        refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._bucket = bucket
        self._client = client
        self._baked = Path(baked)
        self._cache = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "oa-data"
        self._every = refresh_seconds
        self._clock = clock
        self._checked_at = clock()
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls, **kwargs) -> RemoteData | None:
        """The loader for `OA_DATA_BUCKET`, or None when it is not set."""
        bucket = os.environ.get("OA_DATA_BUCKET", "").strip()
        if not bucket:
            return None
        seconds = float(os.environ.get("OA_DATA_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS))
        return cls(bucket, refresh_seconds=seconds, **kwargs)

    # ---- the copy in the image

    def baked(self) -> Source:
        facts = data_release.database_facts(self._baked)
        return Source(
            self._baked, facts["data_version"], "image", data_release.sha256_file(self._baked)
        )

    # ---- the bucket

    def _s3(self):
        if self._client is None:
            self._client = default_client()
        return self._client

    def _pointer(self) -> dict:
        client = self._s3()
        if client is None:
            raise ReleaseError("no S3 client is available")
        reply = client.get_object(Bucket=self._bucket, Key=data_release.POINTER_KEY)
        body = reply["Body"].read(data_release.MAX_POINTER_BYTES + 1)
        if len(body) > data_release.MAX_POINTER_BYTES:
            raise ReleaseError("the pointer is too large")
        try:
            return data_release.validate_pointer(json.loads(body))
        except ValueError as error:
            if isinstance(error, ReleaseError):
                raise
            raise ReleaseError("the pointer is not valid JSON") from None

    def _download(self, pointer: dict) -> Path:
        """The file the pointer names, in the cache, only if it passes every check."""
        self._cache.mkdir(parents=True, exist_ok=True)
        final = self._cache / f"{pointer['sha256'][:16]}.db"
        if final.exists():
            try:
                data_release.verify_database(final, pointer)
                return final
            except ReleaseError:
                final.unlink(missing_ok=True)
        reply = self._s3().get_object(Bucket=self._bucket, Key=pointer["key"])
        handle, name = tempfile.mkstemp(dir=self._cache, suffix=".part")
        written = 0
        try:
            with os.fdopen(handle, "wb") as out:
                while chunk := reply["Body"].read(CHUNK):
                    written += len(chunk)
                    if written > pointer["size"]:
                        raise ReleaseError("the download is larger than the pointer promises")
                    out.write(chunk)
            data_release.verify_database(name, pointer)
            os.replace(name, final)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        for old in self._cache.glob("*.db"):  # keep only the file in use
            if old != final:
                old.unlink(missing_ok=True)
        return final

    def _source(self, pointer: dict) -> Source:
        path = self._download(pointer)
        return Source(path, pointer["data_version"], "s3", pointer["sha256"], pointer.get("run"))

    # ---- start-up and refresh

    def initial(self) -> Source:
        """What to serve at start-up: the release the pointer names, or the copy in the image."""
        try:
            return self._source(self._pointer())
        except Exception as error:  # noqa: BLE001 - any failure falls back to the image
            log.warning("starting from the image's copy of the data: %s", _reason(error))
            return self.baked()

    def due(self) -> bool:
        return self._clock() - self._checked_at >= self._every

    def poll(self, current: Source) -> Source | None:
        """A newer (or different) release than `current`, verified, or None to keep serving it.
        Checks the pointer at most once per interval and never raises."""
        if not self.due() or not self._lock.acquire(blocking=False):
            return None
        try:
            self._checked_at = self._clock()
            pointer = self._pointer()
            # a pointer always wins over the image's copy (as it does at start-up), even when it
            # names a file with the same bytes, so the source then reads 's3'
            if current.origin == "s3" and pointer["sha256"] == current.sha256:
                return None
            return self._source(pointer)
        except Exception as error:  # noqa: BLE001 - keep what is being served
            log.warning("keeping the current data: %s", _reason(error))
            return None
        finally:
            self._lock.release()


def _reason(error: Exception) -> str:
    """What failed, without anything that could hold a key, an address or a path."""
    if isinstance(error, ReleaseError):
        return str(error)
    return f"{type(error).__name__} while reading the data release"
