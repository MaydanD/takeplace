"""Unit tests for the public rate limiter seam (PROJECT-SPEC §40)."""

from __future__ import annotations

import pytest
from app.services.public_rate_limit import (
    PublicRateLimiters,
    SlidingWindowLimiter,
    get_public_rate_limiters,
    init_public_rate_limiters,
    public_create_fingerprint,
    public_get_fingerprint,
    reset_public_rate_limiters,
)
from app.settings import Settings

# --- SlidingWindowLimiter ---------------------------------------------------


def test_allows_up_to_max_then_blocks() -> None:
    limiter = SlidingWindowLimiter(window_seconds=60, max_requests=3)
    assert limiter.is_allowed("k", now=0.0)
    assert limiter.is_allowed("k", now=1.0)
    assert limiter.is_allowed("k", now=2.0)
    assert limiter.is_allowed("k", now=3.0) is False


def test_window_slides() -> None:
    limiter = SlidingWindowLimiter(window_seconds=60, max_requests=2)
    assert limiter.is_allowed("k", now=0.0)
    assert limiter.is_allowed("k", now=10.0)
    assert limiter.is_allowed("k", now=20.0) is False
    assert limiter.is_allowed("k", now=61.0) is True


def test_keys_are_isolated() -> None:
    limiter = SlidingWindowLimiter(window_seconds=60, max_requests=1)
    assert limiter.is_allowed("a", now=0.0)
    assert limiter.is_allowed("b", now=0.0)
    assert limiter.is_allowed("a", now=0.0) is False


def test_retry_after_reports_remaining() -> None:
    limiter = SlidingWindowLimiter(window_seconds=60, max_requests=1)
    assert limiter.is_allowed("k", now=10.0)
    assert limiter.is_allowed("k", now=20.0) is False
    assert limiter.retry_after("k", now=20.0) == 51


def test_retry_after_unknown_key_is_zero() -> None:
    limiter = SlidingWindowLimiter(window_seconds=60, max_requests=1)
    assert limiter.retry_after("missing", now=0.0) == 0


def test_reset_clears_all() -> None:
    limiter = SlidingWindowLimiter(window_seconds=60, max_requests=1)
    assert limiter.is_allowed("k", now=0.0)
    assert limiter.is_allowed("k", now=1.0) is False
    limiter.reset()
    assert limiter.is_allowed("k", now=2.0) is True


def test_reset_single_key() -> None:
    limiter = SlidingWindowLimiter(window_seconds=60, max_requests=1)
    assert limiter.is_allowed("a", now=0.0)
    assert limiter.is_allowed("b", now=0.0)
    limiter.reset("a")
    assert limiter.is_allowed("a", now=1.0) is True
    assert limiter.is_allowed("b", now=1.0) is False


# --- Fingerprints -----------------------------------------------------------


def test_get_fingerprint_is_stable() -> None:
    fp1 = public_get_fingerprint(1, "pub-availability:1:2026-10-05:hall:1")
    fp2 = public_get_fingerprint(1, "pub-availability:1:2026-10-05:hall:1")
    assert fp1 == fp2


def test_get_fingerprint_differs_by_venue() -> None:
    fp1 = public_get_fingerprint(1, "key")
    fp2 = public_get_fingerprint(2, "key")
    assert fp1 != fp2


def test_get_fingerprint_differs_by_request_key() -> None:
    fp1 = public_get_fingerprint(1, "key-a")
    fp2 = public_get_fingerprint(1, "key-b")
    assert fp1 != fp2


def test_create_fingerprint_is_stable() -> None:
    fp1 = public_create_fingerprint(1, "ip-hmac-abc")
    fp2 = public_create_fingerprint(1, "ip-hmac-abc")
    assert fp1 == fp2


def test_create_fingerprint_differs_by_venue() -> None:
    fp1 = public_create_fingerprint(1, "ip-hmac")
    fp2 = public_create_fingerprint(2, "ip-hmac")
    assert fp1 != fp2


def test_create_fingerprint_differs_by_ip_hmac() -> None:
    fp1 = public_create_fingerprint(1, "ip-a")
    fp2 = public_create_fingerprint(1, "ip-b")
    assert fp1 != fp2


def test_fingerprints_are_hex_strings() -> None:
    fp = public_get_fingerprint(1, "key")
    assert len(fp) == 64
    int(fp, 16)


# --- PublicRateLimiters facade ----------------------------------------------


def _make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "idempotency_hmac_key": "test-key",
        "abuse_hmac_key": "test-abuse",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_facade_has_independent_limiters() -> None:
    settings = _make_settings()
    limiters = PublicRateLimiters(settings)
    assert limiters.get is not limiters.create


def test_facade_reset_clears_both() -> None:
    settings = _make_settings()
    limiters = PublicRateLimiters(settings)
    limiters.get.is_allowed("k")
    limiters.create.is_allowed("k")
    limiters.reset()
    assert limiters.get.is_allowed("k") is True
    assert limiters.create.is_allowed("k") is True


# --- Process singleton ------------------------------------------------------


def test_singleton_init_get_reset() -> None:
    reset_public_rate_limiters()
    with pytest.raises(RuntimeError, match="not initialised"):
        get_public_rate_limiters()
    settings = _make_settings()
    init_public_rate_limiters(settings)
    limiter = get_public_rate_limiters()
    assert isinstance(limiter, PublicRateLimiters)
    reset_public_rate_limiters()
    with pytest.raises(RuntimeError, match="not initialised"):
        get_public_rate_limiters()


def test_create_burst_and_day_limits_are_independent() -> None:
    limiter = PublicRateLimiters(
        _make_settings(public_create_burst_max_requests=1, public_create_day_max_requests=2)
    )
    assert limiter.check_create("same-network", now=0) == 0
    assert limiter.check_create("same-network", now=1) > 0
    assert limiter.check_create("same-network", now=11) == 0
    assert limiter.check_create("same-network", now=22) > 80000
    assert limiter.check_create("different-network", now=22) == 0


def test_abuse_alert_is_tenant_scoped_and_expires_after_cooldown() -> None:
    limiter = PublicRateLimiters(
        _make_settings(
            public_abuse_threshold=2,
            public_abuse_window_seconds=600,
            public_abuse_cooldown_seconds=900,
        )
    )
    limiter.record_create(1, now=0)
    assert limiter.alert_count(1, now=1) == 0
    limiter.record_create(1, now=2)
    assert limiter.alert_count(1, now=3) == 1
    assert limiter.alert_count(2, now=3) == 0
    assert limiter.alert_count(now=3) == 1
    assert limiter.alert_count(now=902) == 0
