"""Load test for the API (DL-127), with Locust.

Two modes, chosen with LOAD_MODE:

    mix      (default) a realistic mix of every endpoint, with the key in LOAD_API_KEY
    health   only /health, which needs no key and is never rate-limited

The target is LOAD_BASE_URL (default http://127.0.0.1:8010). Anything that is not this machine is
refused unless LOAD_ALLOW_REMOTE=yes, so a run never reaches a live service by accident.

The key is read from the environment, put in the X-API-Key header and never printed or written:
Locust's CSV and logs hold request names and timings only. Typical runs (from the repository root):

    # the app on this machine (see docs/loadtest.md)
    LOAD_API_KEY=local-load-key locust -f loadtest/locustfile.py --headless -u 20 -r 10 -t 45s \\
        --csv results/local_u20 --host http://127.0.0.1:8010

    # the live function's /health, no key
    LOAD_MODE=health LOAD_ALLOW_REMOTE=yes locust -f loadtest/locustfile.py --headless \\
        -u 10 -r 5 -t 60s --csv results/live_health --host https://<the function address>
"""

from __future__ import annotations

import os
import random
from urllib.parse import urlparse

from locust import HttpUser, between, events, task

MODE = os.environ.get("LOAD_MODE", "mix").strip().lower()
KEY = os.environ.get("LOAD_API_KEY", "")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
HORIZONS = (1, 1, 1, 3, 3, 6)  # the 1-month model is asked about most


@events.init.add_listener
def _guard(environment, **_kwargs):
    """Refuse a remote target unless it was asked for, and a keyed mix without a key."""
    host = urlparse(environment.host or "").hostname or ""
    if host not in LOCAL_HOSTS and os.environ.get("LOAD_ALLOW_REMOTE") != "yes":
        raise SystemExit(
            "Refusing to load a remote service. Set LOAD_ALLOW_REMOTE=yes if you mean to "
            "(it spends the service's allowance and may trip its rate limit)."
        )
    if MODE not in {"mix", "health"}:
        raise SystemExit("LOAD_MODE must be 'mix' or 'health'")
    if MODE == "mix" and not KEY:
        raise SystemExit("LOAD_MODE=mix needs the key in the environment variable LOAD_API_KEY")


class HealthUser(HttpUser):
    """Only /health: no key, no rate limit."""

    abstract = MODE != "health"
    wait_time = between(0.05, 0.2)

    @task
    def health(self):
        self.client.get("/health", name="GET /health")


class ApiUser(HttpUser):
    """A visitor who uses the whole API, in proportions like the website's and an analyst's."""

    abstract = MODE != "mix"
    wait_time = between(0.05, 0.3)
    segments: list[dict] = []

    def on_start(self):
        self.client.headers["X-API-Key"] = KEY
        if not ApiUser.segments:  # the list of segments, fetched once and shared
            with self.client.get(
                "/v1/predict/segments", name="GET /v1/predict/segments", catch_response=True
            ) as reply:
                if reply.status_code == 200:
                    ApiUser.segments = reply.json()["segments"]
                else:
                    reply.failure(f"could not list segments: {reply.status_code}")

    def _segment(self) -> dict | None:
        return random.choice(ApiUser.segments) if ApiUser.segments else None

    @task(8)
    def health(self):
        self.client.get("/health", name="GET /health")

    @task(15)
    def status(self):
        self.client.get("/v1/status", name="GET /v1/status")

    @task(10)
    def share_monthly(self):
        self.client.get("/v1/share/monthly", name="GET /v1/share/monthly")

    @task(8)
    def segments_by_specialty(self):
        self.client.get(
            "/v1/segments", params={"by": "specialty", "top": 10}, name="GET /v1/segments"
        )

    @task(10)
    def forecasts_national(self):
        self.client.get("/v1/forecasts/national", name="GET /v1/forecasts/national")

    @task(6)
    def forecasts_specialty(self):
        self.client.get("/v1/forecasts/specialty", name="GET /v1/forecasts/specialty")

    @task(5)
    def forecasts_alerts(self):
        self.client.get("/v1/forecasts/alerts", name="GET /v1/forecasts/alerts")

    @task(5)
    def model_results(self):
        self.client.get("/v1/model-results", name="GET /v1/model-results")

    @task(4)
    def findings(self):
        self.client.get("/v1/findings", name="GET /v1/findings")

    @task(4)
    def models(self):
        self.client.get("/v1/models", name="GET /v1/models")

    @task(15)
    def predict_segment(self):
        pick = self._segment()
        if pick:
            params = {
                "specialty": pick["specialty"],
                "age_band": pick["age_band"],
                "gender": pick["gender"],
                "horizon": random.choice(HORIZONS),
            }
            self.client.get("/v1/predict/segment", params=params, name="GET /v1/predict/segment")

    @task(6)
    def predict_alert(self):
        pick = self._segment()
        if pick:
            params = {k: pick[k] for k in ("specialty", "age_band", "gender")}
            self.client.get("/v1/predict/alert", params=params, name="GET /v1/predict/alert")
