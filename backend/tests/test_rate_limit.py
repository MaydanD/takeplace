"""Sliding-window rate limiter (PROJECT-SPEC §39.5, §40)."""

from __future__ import annotations

from app.security.rate_limit import SlidingWindowRateLimiter


def test_allows_up_to_the_limit_then_blocks() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60, max_requests=3)
    assert limiter.is_allowed("k", now=0.0)
    assert limiter.is_allowed("k", now=1.0)
    assert limiter.is_allowed("k", now=2.0)
    assert limiter.is_allowed("k", now=3.0) is False


def test_window_slides() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60, max_requests=2)
    assert limiter.is_allowed("k", now=0.0)
    assert limiter.is_allowed("k", now=10.0)
    assert limiter.is_allowed("k", now=20.0) is False
    # The first event leaves the window at t=60.
    assert limiter.is_allowed("k", now=61.0) is True


def test_keys_are_isolated() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60, max_requests=1)
    assert limiter.is_allowed("a", now=0.0)
    assert limiter.is_allowed("b", now=0.0)
    assert limiter.is_allowed("a", now=0.0) is False


def test_retry_after_reports_time_until_oldest_event_expires() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60, max_requests=1)
    assert limiter.is_allowed("k", now=10.0)
    assert limiter.is_allowed("k", now=20.0) is False
    assert limiter.retry_after("k", now=20.0) == 51


def test_retry_after_for_unknown_key_is_zero() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60, max_requests=1)
    assert limiter.retry_after("missing", now=0.0) == 0


def test_reset_clears_state() -> None:
    limiter = SlidingWindowRateLimiter(window_seconds=60, max_requests=1)
    assert limiter.is_allowed("k", now=0.0)
    assert limiter.is_allowed("k", now=1.0) is False
    limiter.reset()
    assert limiter.is_allowed("k", now=2.0) is True
