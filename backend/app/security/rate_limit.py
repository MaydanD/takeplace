"""In-memory sliding-window rate limiting (PROJECT-SPEC §39.5, §40).

This is deliberately a soft limit scoped to a single API process, exactly as the
spec allows for an in-memory implementation: it is not treated as a security
boundary, only as a cheap first line that protects the Argon2id pool. Counters
are keyed by non-reversible values (HMAC fingerprints, never raw IPs), so the
limiter itself stores no PII.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Allow at most ``max_requests`` events per ``window_seconds`` for a key."""

    def __init__(self, *, window_seconds: int, max_requests: int) -> None:
        self._window = float(window_seconds)
        self._max = max_requests
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, key: str, *, now: float | None = None) -> bool:
        """Record an attempt and return whether it is within the limit."""
        moment = time.monotonic() if now is None else now
        cutoff = moment - self._window
        bucket = self._events[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._max:
            return False
        bucket.append(moment)
        return True

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        """Seconds until the oldest event leaves the window."""
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
