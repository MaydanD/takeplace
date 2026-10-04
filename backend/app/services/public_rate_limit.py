"""In-memory (per-process) public rate limiting seam (PROJECT-SPEC §40).

This is deliberately a soft limit scoped to a single API process, exactly as the
spec allows for an in-memory implementation: it is not treated as a security
boundary, only as a cheap first line that protects the database against cheap
flooding of public reads and public creates.

Counters are keyed by non-reversible HMAC fingerprints, never by raw client IP,
so the limiter itself holds no PII. The abstraction is split into read and create
limiters so production can later replace one or both with a shared backend without
rewriting the public API.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict

from app.settings import Settings


def _hmac_digest(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Fingerprint helpers (never store raw IP)
# ---------------------------------------------------------------------------


def public_get_fingerprint(venue_id: int, request_key: str) -> str:
    """Stable fingerprint for public availability reads (§40).

    ``request_key`` is a short, non-PII identifier for the selected date/hall/table
    combination (for example the value returned by ``public_availability_key``).
    """
    return _hmac_digest("pub-get", f"{venue_id}:{request_key}")


def public_create_fingerprint(venue_id: int, client_ip_hmac: str) -> str:
    """Stable fingerprint for public create attempts (§40).

    ``client_ip_hmac`` must already be the HMAC-SHA-256 of the canonical client IP
    under the abuse key (it is computed in the API layer from the trusted-proxy IP).
    """
    return _hmac_digest("pub-create", f"{venue_id}:{client_ip_hmac}")


# ---------------------------------------------------------------------------
# Sliding-window limiter
# ---------------------------------------------------------------------------


class SlidingWindowLimiter:
    """Allow at most ``max_requests`` events per ``window_seconds`` for a key."""

    def __init__(self, *, window_seconds: int, max_requests: int) -> None:
        self._window = float(window_seconds)
        self._max = max_requests
        self._events: defaultdict[str, list[float]] = defaultdict(list)
        self._next_cleanup = 0.0

    def is_allowed(self, key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        cutoff = moment - self._window
        if moment >= self._next_cleanup:
            self._events = defaultdict(
                list, {k: v for k, v in self._events.items() if v and v[-1] > cutoff}
            )
            self._next_cleanup = moment + self._window
        bucket = self._events[key]
        while bucket and bucket[0] <= cutoff:
            bucket.pop(0)
        if len(bucket) >= self._max:
            return False
        bucket.append(moment)
        return True

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else now
        bucket = self._events.get(key)
        if not bucket:
            return 0
        remaining = self._window - (moment - bucket[0])
        return max(0, int(remaining) + 1)

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._events.clear()
        else:
            self._events.pop(key, None)


# ---------------------------------------------------------------------------
# Public limiter facade
# ---------------------------------------------------------------------------


class PublicRateLimiters:
    """Two independent public limiters: GET/availability and POST/create."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._burst = SlidingWindowLimiter(
            window_seconds=settings.public_create_burst_window_seconds,
            max_requests=settings.public_create_burst_max_requests,
        )
        self._day = SlidingWindowLimiter(
            window_seconds=86400, max_requests=settings.public_create_day_max_requests
        )
        self._venue_creates: dict[int, list[float]] = {}
        self._alerts: dict[int, float] = {}
        self._get = SlidingWindowLimiter(
            window_seconds=settings.public_availability_rate_window_seconds,
            max_requests=settings.public_availability_rate_max_requests,
        )
        self._create = SlidingWindowLimiter(
            window_seconds=settings.public_create_rate_window_seconds,
            max_requests=settings.public_create_rate_max_requests,
        )

    @property
    def get(self) -> SlidingWindowLimiter:
        return self._get

    @property
    def create(self) -> SlidingWindowLimiter:
        return self._create

    def reset(self) -> None:
        self._get.reset()
        self._create.reset()
        self._burst.reset()
        self._day.reset()
        self._venue_creates.clear()
        self._alerts.clear()

    def check_create(self, key: str, *, now: float | None = None) -> int:
        for limiter in (self._burst, self._create, self._day):
            if not limiter.is_allowed(key, now=now):
                return limiter.retry_after(key, now=now)
        return 0

    def record_create(self, venue_id: int, *, now: float | None = None) -> None:
        """Count committed new ONLINE bookings only; replay is not a new event.

        Per-process aggregate signal with cooldown, containing no guest/IP data.
        It never changes a booking or the venue kill switch.
        """
        moment = time.monotonic() if now is None else now
        cutoff = moment - self._settings.public_abuse_window_seconds
        self._venue_creates = {
            k: [t for t in values if t > cutoff]
            for k, values in self._venue_creates.items()
            if values and values[-1] > cutoff
        }
        bucket = self._venue_creates.setdefault(venue_id, [])
        bucket.append(moment)
        # Threshold is sufficient to detect sustained traffic; bound memory.
        del bucket[: -self._settings.public_abuse_threshold]
        if len(bucket) >= self._settings.public_abuse_threshold:
            self._alerts[venue_id] = moment + self._settings.public_abuse_cooldown_seconds

    def alert_count(self, venue_id: int | None = None, *, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else now
        self._alerts = {k: expires for k, expires in self._alerts.items() if expires > moment}
        return len(self._alerts) if venue_id is None else int(venue_id in self._alerts)


# ---------------------------------------------------------------------------
# Process-wide singleton, mirroring the existing Stage 2 limiter pattern
# ---------------------------------------------------------------------------

_private: dict[str, PublicRateLimiters] = {}


def init_public_rate_limiters(settings: Settings) -> None:
    _private["limiters"] = PublicRateLimiters(settings)


def get_public_rate_limiters() -> PublicRateLimiters:
    limiter = _private.get("limiters")
    if limiter is None:
        raise RuntimeError("public rate limiters are not initialised")
    return limiter


def reset_public_rate_limiters() -> None:
    _private.pop("limiters", None)


def public_abuse_alert_count(venue_id: int | None = None) -> int:
    limiter = _private.get("limiters")
    return limiter.alert_count(venue_id) if limiter else 0
