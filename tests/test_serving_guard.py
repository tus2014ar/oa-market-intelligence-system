"""Tests for the guard in front of the Claude question box (src/.../serving/guard.py).

The Claude API is pay-as-you-go, so the public box needs three limits: an optional access
code, a per-visitor rate limit, and a daily token budget for the whole site.
"""

import pytest

from oa_market_intelligence.serving.guard import (
    AccessGate,
    RateLimiter,
    TokenBudget,
    validate_question,
)


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def test_no_access_code_means_the_box_is_open():
    assert AccessGate(None).allows("") is True
    assert AccessGate("").allows("anything") is True


def test_an_access_code_must_match_exactly():
    gate = AccessGate("open-sesame")
    assert gate.allows("open-sesame") is True
    assert gate.allows("open-sesame ") is False
    assert gate.allows("") is False
    assert gate.allows(None) is False


def test_a_visitor_is_limited_per_window_and_other_visitors_are_not():
    clock = Clock()
    limiter = RateLimiter(max_per_window=2, window_seconds=3600, clock=clock)
    assert limiter.allow("a") and limiter.allow("a")
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True


def test_the_window_slides():
    clock = Clock()
    limiter = RateLimiter(max_per_window=1, window_seconds=60, clock=clock)
    assert limiter.allow("a")
    clock.now += 30
    assert limiter.allow("a") is False
    clock.now += 31
    assert limiter.allow("a") is True


def test_the_token_budget_stops_the_box_when_spent_and_resets_next_day():
    clock = Clock()
    budget = TokenBudget(daily_tokens=1_000, clock=clock)
    assert budget.has_room()
    budget.charge(600)
    assert budget.has_room()
    budget.charge(500)
    assert budget.has_room() is False
    clock.now += 86_400 + 1
    assert budget.has_room()


@pytest.mark.parametrize("question", ["", "   ", None])
def test_empty_questions_are_rejected(question):
    with pytest.raises(ValueError, match="empty"):
        validate_question(question)


def test_long_questions_are_rejected_and_whitespace_is_trimmed():
    with pytest.raises(ValueError, match="long"):
        validate_question("x" * 600, max_chars=500)
    assert validate_question("  how is the share trending?  ") == "how is the share trending?"
