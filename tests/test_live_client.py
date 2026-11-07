"""Tests for the website's client of the model API (src/.../serving/live_client.py, DL-122).

The network is replaced by a stub, so these run offline and exactly. What matters: the key goes
only in the header of the calls that need it, every failure becomes a fixed plain message, and
neither the address nor the key can reach a visitor through a message, however a call fails."""

import io
import json
import urllib.error
import urllib.parse

import pytest

from oa_market_intelligence.serving.live_client import (
    MESSAGES,
    REQUIRED,
    LiveClient,
    LiveUnavailable,
)

URL = "https://abc123example.lambda-url.us-east-1.on.aws/"
KEY = "key-for-the-tests-0123456789"

GOOD = {
    "specialty": "RHEUMATOLOGY",
    "age_band": "65 TO 74",
    "gender": "FEMALE",
    "horizon": 3,
    "target_month_id": 202510,
    "target_month": "Oct 2025",
    "model": "logistic",
    "model_version": "ec08de463b",
    "forecast_pct": 1.7326,
    "last_month_pct": 2.6471,
    "change_pp": -0.9145,
    "origin_visits": 169,
    "small": False,
    "known_bias_pct": 10.92,
    "stored_forecast_pct": 1.7326,
    "matches_stored": True,
    "note": "…",
}


class Reply:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Stub:
    """Records every request and answers from a script: a dict (a JSON body), bytes, or an error."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        body = self.answer if isinstance(self.answer, bytes) else json.dumps(self.answer).encode()
        return Reply(body)


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "x", {}, io.BytesIO(b'{"detail":"%s"}' % KEY.encode()))


def client(answer) -> tuple[LiveClient, Stub]:
    stub = Stub(answer)
    return LiveClient(URL, KEY, opener=stub), stub


# ------------------------------------------------------------------ a good call


def test_a_forecast_call_sends_the_key_in_the_header_and_the_choice_in_the_query():
    c, stub = client(GOOD)
    out = c.forecast("RHEUMATOLOGY", "65 TO 74", "FEMALE", 3)
    assert out["forecast_pct"] == 1.7326
    ((request, timeout),) = stub.requests
    assert request.get_header("X-api-key") == KEY and request.get_method() == "GET"
    assert timeout == 15.0
    parsed = urllib.parse.urlparse(request.full_url)
    assert parsed.scheme == "https" and parsed.path == "/v1/predict/segment"
    assert urllib.parse.parse_qs(parsed.query) == {
        "specialty": ["RHEUMATOLOGY"],
        "age_band": ["65 TO 74"],  # the spaces survive the encoding
        "gender": ["FEMALE"],
        "horizon": ["3"],
    }
    assert request.full_url.startswith("https://abc123example.lambda-url.us-east-1.on.aws/v1/")


def test_the_health_call_sends_no_key_at_all():
    c, stub = client({"status": "ok", "data_version": "28b9a7bd3234"})
    assert c.health()["data_version"] == "28b9a7bd3234"
    ((request, _),) = stub.requests
    assert request.get_header("X-api-key") is None
    assert urllib.parse.urlparse(request.full_url).path == "/health"


def test_the_address_may_end_in_a_slash_or_not():
    for address in (URL, URL.rstrip("/")):
        stub = Stub({"status": "ok"})
        LiveClient(address, KEY, opener=stub).health()
        assert "//health" not in stub.requests[0][0].full_url.replace("https://", "")


# ------------------------------------------------------------------ settings


def test_the_client_is_made_only_from_a_usable_address_and_key():
    assert LiveClient.from_settings(URL, KEY) is not None
    for address, key in [
        (None, KEY), (URL, None), ("", KEY), (URL, ""), (URL, "   "),
        ("http://plain.example.com", KEY),  # not https
        ("https://", KEY), ("ftp://x.example.com", KEY), ("abc123example", KEY),
    ]:  # fmt: skip
        assert LiveClient.from_settings(address, key) is None, (address, key)
    with pytest.raises(ValueError, match="https://"):
        LiveClient("http://plain.example.com", KEY)


# -------------------------------------------------- every failure is a plain message


@pytest.mark.parametrize(
    ("code", "reason"),
    [(401, "rejected"), (403, "rejected"), (404, "not_found"), (422, "not_served"),
     (429, "busy"), (500, "error"), (502, "error"), (503, "error")],
)  # fmt: skip
def test_each_http_failure_maps_to_one_fixed_reason(code, reason):
    c, _ = client(http_error(code))
    with pytest.raises(LiveUnavailable) as caught:
        c.forecast("RHEUMATOLOGY", "65 TO 74", "FEMALE", 3)
    assert caught.value.reason == reason and caught.value.message == MESSAGES[reason]


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("timed out"), ConnectionRefusedError("refused"), OSError("no route"),
     urllib.error.URLError("name or service not known")],
)  # fmt: skip
def test_a_network_failure_is_unreachable(failure):
    c, _ = client(failure)
    with pytest.raises(LiveUnavailable) as caught:
        c.forecast("RHEUMATOLOGY", "65 TO 74", "FEMALE", 3)
    assert caught.value.reason == "unreachable"


@pytest.mark.parametrize("body", [b"not json", b"", b"[1, 2, 3]", b'"text"', b"null"])
def test_an_answer_that_is_not_a_json_object_is_an_error(body):
    c, _ = client(body)
    with pytest.raises(LiveUnavailable) as caught:
        c.forecast("RHEUMATOLOGY", "65 TO 74", "FEMALE", 3)
    assert caught.value.reason == "error"


@pytest.mark.parametrize("missing", REQUIRED)
def test_an_answer_missing_a_field_the_panel_shows_is_an_error(missing):
    answer = {k: v for k, v in GOOD.items() if k != missing}
    c, _ = client(answer)
    with pytest.raises(LiveUnavailable) as caught:
        c.forecast("RHEUMATOLOGY", "65 TO 74", "FEMALE", 3)
    assert caught.value.reason == "error"


def test_a_health_answer_that_is_not_ok_is_an_error():
    c, _ = client({"status": "down"})
    with pytest.raises(LiveUnavailable):
        c.health()


# -------------------------------------------------- nothing secret reaches a visitor


def test_neither_the_address_nor_the_key_can_appear_in_any_message_or_exception():
    failures = [http_error(c) for c in (401, 404, 422, 429, 500)] + [
        TimeoutError(URL + KEY), OSError(f"cannot reach {URL} with {KEY}"),
        urllib.error.URLError(f"{URL} {KEY}"), b"garbage " + KEY.encode(),
    ]  # fmt: skip
    for failure in failures:
        c, _ = client(failure)
        with pytest.raises(LiveUnavailable) as caught:
            c.forecast("RHEUMATOLOGY", "65 TO 74", "FEMALE", 3)
        shown = f"{caught.value} {caught.value.message} {caught.value!r} {caught.value.args}"
        assert KEY not in shown and "abc123example" not in shown and "amazonaws" not in shown
        assert caught.value.__cause__ is None  # the original error, which may hold them, is dropped
    for text in MESSAGES.values():
        assert "http" not in text.lower() and "key-" not in text and "401" not in text


def test_an_unknown_reason_is_an_error_not_a_crash():
    assert LiveUnavailable("surprise").reason == "error"
