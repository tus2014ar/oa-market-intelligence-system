"""A small client for the model API (DL-122), for the website's "Run the model live" panel.

The website runs on Streamlit Community Cloud and the API on AWS Lambda. This module is the only
place the website talks to the API. It uses the standard library only (no new dependency for the
site), sends the key in the `X-API-Key` header, and turns every failure into a `LiveUnavailable`
with a fixed reason and a plain sentence.

Nothing here puts the address or the key into a message, a log line or an exception: the owner
sets both in the host's secrets, and a visitor must never see either.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

TIMEOUT_SECONDS = 15.0

# What each failure means to a visitor. The wording never names the address, the key or a code.
MESSAGES = {
    "unreachable": "The live model service did not answer.",
    "rejected": (
        "The website's key for the live model service was refused. "
        "The site owner needs to update it."
    ),
    "busy": "The live model service is busy. Try again in a minute.",
    "not_found": "The live model service has no model for that choice.",
    "not_served": "The live model service does not serve that horizon.",
    "error": "The live model service gave an answer the website could not use.",
}

REQUIRED = (
    "forecast_pct",
    "last_month_pct",
    "change_pp",
    "horizon",
    "target_month",
    "model",
    "model_version",
    "origin_visits",
    "small",
)


class LiveUnavailable(Exception):
    """The live service could not give an answer. `reason` is one of the keys of `MESSAGES`."""

    def __init__(self, reason: str):
        self.reason = reason if reason in MESSAGES else "error"
        super().__init__(MESSAGES[self.reason])

    @property
    def message(self) -> str:
        return MESSAGES[self.reason]


class LiveClient:
    """Calls the API's `/health` (no key) and `/v1/predict/segment` (key)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        opener: Callable = urllib.request.urlopen,
    ):
        base = (base_url or "").strip().rstrip("/")
        if not base.startswith("https://") or len(base) <= len("https://"):
            raise ValueError("the live service address must start with https://")
        if not (api_key or "").strip():
            raise ValueError("the live service key is empty")
        self._base = base
        self._key = api_key.strip()
        self._timeout = timeout
        self._open = opener

    @classmethod
    def from_settings(cls, base_url: str | None, api_key: str | None, **kwargs):
        """The client when both the address and the key are set and usable, else None."""
        if not (base_url and api_key):
            return None
        try:
            return cls(base_url, api_key, **kwargs)
        except ValueError:
            return None

    def _get(self, path: str, params: dict | None = None, *, keyed: bool = True) -> dict:
        url = self._base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": "oa-market-site/1"}
        if keyed:
            headers["X-API-Key"] = self._key
        request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 - https only
        try:
            with self._open(request, timeout=self._timeout) as response:  # noqa: S310
                body = response.read()
        except urllib.error.HTTPError as error:
            raise LiveUnavailable(_reason_for(error.code)) from None
        except Exception:  # noqa: BLE001 - a timeout, a refused connection, a bad certificate
            raise LiveUnavailable("unreachable") from None
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            raise LiveUnavailable("error") from None
        if not isinstance(parsed, dict):
            raise LiveUnavailable("error")
        return parsed

    def health(self) -> dict:
        """The service's `/health`: whether it is up and which data version it serves."""
        body = self._get("/health", keyed=False)
        if body.get("status") != "ok":
            raise LiveUnavailable("error")
        return body

    def forecast(self, specialty: str, age_band: str, gender: str, horizon: int) -> dict:
        """The live forecast for one segment, checked for the fields the panel shows."""
        body = self._get(
            "/v1/predict/segment",
            {"specialty": specialty, "age_band": age_band, "gender": gender, "horizon": horizon},
        )
        if any(field not in body for field in REQUIRED):
            raise LiveUnavailable("error")
        return body


def _reason_for(code: int) -> str:
    if code in (401, 403):
        return "rejected"
    if code == 404:
        return "not_found"
    if code == 422:
        return "not_served"
    if code == 429:
        return "busy"
    return "error"
