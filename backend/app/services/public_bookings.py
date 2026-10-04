"""Public booking orchestration for Stage 6 (PROJECT-SPEC §18, §32.2, §40, §50).

This module is the thin public-facing wrapper that knows about:

* public slug-based venue addressing;
* the CAPTCHA adapter / feature-flag hook (disabled by default, no provider);

Everything that mutates bookings is delegated to the existing booking-core
``create_public_booking`` (§32.2), which shares the canonical lock order,
schedule validation, capacity, exclusion constraint, event creation and retry
logic with the admin path. This module does **not** insert
bookings/occupancies/events directly.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Venue
from app.services.errors import VenueNotFoundError


async def resolve_public_venue(session: AsyncSession, slug: str) -> Venue:
    """Resolve a venue by its public slug (§34).

    Returns the venue row without any mutable-state gate: the idempotency
    lookup (§18.2 step 2) and the kill-switch gate (§32.2) happen later, so a
    replay of an existing booking is never turned into a 404 by a venue that
    was suspended after the first successful commit.
    """
    row = await session.execute(select(Venue).where(Venue.slug == slug))
    venue = row.scalar_one_or_none()
    if venue is None:
        raise VenueNotFoundError(slug)
    return venue


# ---------------------------------------------------------------------------
# CAPTCHA adapter / feature flag hook (PROJECT-SPEC §50)
# ---------------------------------------------------------------------------


class CaptchaVerificationError(Exception):
    """Raised when the enabled CAPTCHA hook rejects the request."""


class BaseCaptchaAdapter:
    """Hook point for a future CAPTCHA provider (§50).

    The adapter is deliberately abstract and disabled by default. The booking
    domain and the create transaction do **not** know about any specific provider.

    When the feature flag is enabled (``settings.public_captcha_enabled``) but no
    provider is configured, :meth:`verify` raises :class:`CaptchaVerificationError`
    so the create path rejects the request instead of silently skipping the check.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled

    async def verify(self, token: str | None) -> None:
        if not self._enabled:
            return
        if not token:
            raise CaptchaVerificationError("captcha verification is required")
        # Fail closed until a provider actually verifies the token.
        raise CaptchaVerificationError("captcha provider is not configured")


def build_captcha_adapter(*, enabled: bool = False) -> BaseCaptchaAdapter:
    """Public hook for wiring a real CAPTCHA provider later (§50)."""
    return BaseCaptchaAdapter(enabled=enabled)
