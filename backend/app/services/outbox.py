"""Transactional outbox service (PROJECT-SPEC §38.2, §38.3).

Two responsibilities, deliberately separated from the booking domain and the VK
transport:

* :func:`enqueue_online_booking_notification` — called *inside* the booking
  transaction (right after the booking/occupancy/event inserts) to add the outbox
  row. If the transaction rolls back the row disappears with everything else; if
  it commits the notification is guaranteed to exist (§38.2).
* the claim/deliver/release primitives used by the worker — see
  :mod:`app.services.outbox_worker`.

The payload stored here is intentionally minimal and non-PII (§38.2): only the
``booking_id``, the notification ``kind`` and the formatter version. The worker
loads the *current* booking before sending, so a canceled or anonymised booking is
never announced and no copy of the guest's name/phone is kept in the outbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, NotificationOutbox
from app.domain.outbox import (
    FORMATTER_VERSION,
    NOTIFICATION_KIND_BOOKING_CREATED,
    NOTIFICATION_TYPE_ONLINE_BOOKING,
    notification_dedup_key,
)


def notification_expires_at(
    *,
    created_at: datetime,
    booking_starts_at: datetime,
    booking_ends_at: datetime,
    late_grace_seconds: int,
    max_age_seconds: int,
) -> datetime:
    """Compute ``expires_at`` exactly as §38.2 defines it.

    ``min(booking.ends_at, booking.starts_at + LATE_GRACE, created_at + MAX_AGE)``.
    The late-grace term keeps a booking that is about to start but not yet stale;
    the max-age term stops a restored worker from replaying ancient notifications.
    """
    return min(
        booking_ends_at,
        booking_starts_at + timedelta(seconds=late_grace_seconds),
        created_at + timedelta(seconds=max_age_seconds),
    )


def build_booking_notification_payload(*, booking_id: int, kind: str) -> dict[str, Any]:
    """The minimal, non-PII outbox payload for a booking notification (§38.2)."""
    return {
        "booking_id": booking_id,
        "kind": kind,
        "formatter_version": FORMATTER_VERSION,
    }


async def enqueue_online_booking_notification(
    session: AsyncSession,
    *,
    booking: Booking,
    created_at: datetime,
    late_grace_seconds: int,
    max_age_seconds: int,
) -> NotificationOutbox:
    """Insert the ONLINE booking notification in the caller's transaction (§38.2).

    A row is always created for the ONLINE booking event, regardless of current VK
    configuration. The worker evaluates the current integration state at delivery
    time and records the appropriate terminal outcome without making HTTP when it
    is disabled or missing (§38.1–38.3).

    Must be called inside the booking transaction; it never commits.
    """

    kind = NOTIFICATION_KIND_BOOKING_CREATED
    outbox = NotificationOutbox(
        venue_id=booking.venue_id,
        type=NOTIFICATION_TYPE_ONLINE_BOOKING,
        dedup_key=notification_dedup_key(
            booking_id=booking.id,
            kind=kind,
            formatter_version=FORMATTER_VERSION,
        ),
        payload=build_booking_notification_payload(booking_id=booking.id, kind=kind),
        status="PENDING",
        attempts=0,
        next_attempt_at=created_at,
        locked_until=None,
        provider_dedup_id=None,
        last_error=None,
        expires_at=notification_expires_at(
            created_at=created_at,
            booking_starts_at=booking.starts_at,
            booking_ends_at=booking.ends_at,
            late_grace_seconds=late_grace_seconds,
            max_age_seconds=max_age_seconds,
        ),
        skipped_at=None,
        skip_reason=None,
        acknowledged_at=None,
        acknowledged_by_session_id=None,
        created_at=created_at,
        sent_at=None,
    )
    session.add(outbox)
    return outbox
