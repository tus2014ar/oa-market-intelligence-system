"""Helper for tests that call a live outside service (openFDA).

A server error, timeout or connection failure on the other side is not a defect in this project,
so the test is skipped with a clear reason. A genuine mismatch (a wrong date, a changed response
shape, a client error such as 400 or 404) still fails.
"""

from contextlib import contextmanager

import pytest
import requests


@contextmanager
def skip_if_service_unavailable(service: str = "openFDA"):
    try:
        yield
    except requests.exceptions.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        if status is not None and status >= 500:
            pytest.skip(f"{service} returned HTTP {status}; the live check could not run")
        raise
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as error:
        reason = f"{service} was unreachable ({type(error).__name__})"
        pytest.skip(f"{reason}; the live check could not run")
