"""Limits in front of the public Claude question box.

The Claude API is pay-as-you-go, so the box has an optional access code, a per-visitor rate
limit and a daily token budget for the whole site. The clock is injectable for tests.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from collections.abc import Callable

DAY_SECONDS = 86_400


class AccessGate:
    """Optional shared access code. No code configured means the box is open."""

    def __init__(self, code: str | None):
        self._code = code or ""

    def allows(self, attempt: str | None) -> bool:
        if not self._code:
            return True
        return hmac.compare_digest(self._code.encode(), (attempt or "").encode())


class RateLimiter:
    """At most `max_per_window` questions per visitor in any sliding window."""

    def __init__(
        self,
        *,
        max_per_window: int = 10,
        window_seconds: int = 3600,
        clock: Callable[[], float] = time.time,
    ):
        self._max = max_per_window
        self._window = window_seconds
        self._clock = clock
        self._seen: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, visitor: str) -> bool:
        now = self._clock()
        stamps = self._seen[visitor]
        while stamps and now - stamps[0] >= self._window:
            stamps.popleft()
        if len(stamps) >= self._max:
            return False
        stamps.append(now)
        return True


class TokenBudget:
    """A daily cap on tokens for the whole site; resets 24 hours after it started counting."""

    def __init__(self, *, daily_tokens: int, clock: Callable[[], float] = time.time):
        self._cap = daily_tokens
        self._clock = clock
        self._started = clock()
        self._used = 0

    def _roll(self) -> None:
        if self._clock() - self._started >= DAY_SECONDS:
            self._started = self._clock()
            self._used = 0

    def has_room(self) -> bool:
        self._roll()
        return self._used < self._cap

    def charge(self, tokens: int) -> None:
        self._roll()
        self._used += tokens


def validate_question(question: str | None, *, max_chars: int = 500) -> str:
    """Trim a question and reject empty or very long ones."""
    cleaned = (question or "").strip()
    if not cleaned:
        raise ValueError("The question is empty.")
    if len(cleaned) > max_chars:
        raise ValueError(f"The question is too long (limit {max_chars} characters).")
    return cleaned
