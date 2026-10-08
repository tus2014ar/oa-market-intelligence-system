"""The live-service helper skips only on the other side's failures, never on real mismatches."""

import pytest
import requests

from live_service import skip_if_service_unavailable


def _http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} error", response=response)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_is_skipped(status):
    with pytest.raises(pytest.skip.Exception, match=str(status)):
        with skip_if_service_unavailable():
            raise _http_error(status)


@pytest.mark.parametrize(
    "error", [requests.exceptions.ConnectionError("down"), requests.exceptions.Timeout("slow")]
)
def test_an_unreachable_service_is_skipped(error):
    with pytest.raises(pytest.skip.Exception):
        with skip_if_service_unavailable():
            raise error


@pytest.mark.parametrize("status", [400, 403, 404])
def test_a_client_error_still_fails(status):
    with pytest.raises(requests.exceptions.HTTPError):
        with skip_if_service_unavailable():
            raise _http_error(status)


def test_a_real_mismatch_still_fails():
    with pytest.raises(AssertionError):
        with skip_if_service_unavailable():
            assert 1 == 2  # noqa: PLR0133


def test_no_error_means_no_skip():
    with skip_if_service_unavailable():
        value = 3
    assert value == 3
