"""Process-wide login rate limiters (PROJECT-SPEC §39.5).

Two layers, both applied before the expensive Argon2 verify:

1. a cheap per-client-network burst limit;
2. a main limit keyed by ``login + client network``.

Keys are HMAC fingerprints, never raw IPs, so the limiter holds no PII.
"""

from __future__ import annotations

from app.security.rate_limit import SlidingWindowRateLimiter
from app.settings import Settings

_burst: SlidingWindowRateLimiter | None = None
_login: SlidingWindowRateLimiter | None = None


def init_limiters(settings: Settings) -> None:
    global _burst, _login
    _burst = SlidingWindowRateLimiter(
        window_seconds=settings.login_burst_window_seconds,
        max_requests=settings.login_burst_max_requests,
    )
    _login = SlidingWindowRateLimiter(
        window_seconds=settings.login_rate_window_seconds,
        max_requests=settings.login_rate_max_requests,
    )


def get_burst_limiter() -> SlidingWindowRateLimiter:
    if _burst is None:
        raise RuntimeError("rate limiters are not initialised")
    return _burst


def get_login_limiter() -> SlidingWindowRateLimiter:
    if _login is None:
        raise RuntimeError("rate limiters are not initialised")
    return _login


def reset_limiters() -> None:
    global _burst, _login
    _burst = None
    _login = None
