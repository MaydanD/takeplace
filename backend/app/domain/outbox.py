"""Notification outbox domain rules (PROJECT-SPEC §6.11, §38.2-38.4).

Pure, database-free definitions shared by the ORM, the migration, the booking
service (which inserts the outbox row) and the worker (which claims and delivers
it). Keeping the statuses, skip reasons and delivery-type vocabulary in one place
prevents the CHECK constraints and the application from drifting apart.

The outbox exists to satisfy the §38.2 invariant: a VK notification is created in
the *same* PostgreSQL transaction as the booking mutation, so a rollback leaves
no notification and a VK outage can never fail the booking.
"""

from __future__ import annotations

# The only delivery type in release v1 (§38.1, Stage 12). An abuse alert is an
# operational signal, never an outbox type (§40).
NOTIFICATION_TYPE_ONLINE_BOOKING = "ONLINE_BOOKING"

#: Notification kinds the booking formatter understands.
NOTIFICATION_KIND_BOOKING_CREATED = "BOOKING_CREATED"
NOTIFICATION_KINDS: tuple[str, ...] = (NOTIFICATION_KIND_BOOKING_CREATED,)

#: Outbox lifecycle (§6.11).
OUTBOX_STATUSES: tuple[str, ...] = (
    "PENDING",
    "PROCESSING",
    "RETRY",
    "SENT",
    "DEAD",
    "SKIPPED",
)

#: Stable, minimal set of reasons a notification was deliberately not sent (§6.11).
SKIP_REASONS: tuple[str, ...] = (
    "EXPIRED",
    "BOOKING_INACTIVE",
    "BOOKING_ANONYMIZED",
    "INTEGRATION_DISABLED",
)

#: Booking statuses for which the notification is still operationally meaningful.
#: A booking that already started but is still NEW/WAITING inside the late-grace
#: window is *not* stale (§38.3); OPEN/CLOSED/CANCELED are.
NOTIFIABLE_BOOKING_STATUSES: tuple[str, ...] = ("NEW", "WAITING")

#: Bumped whenever the message body changes so a re-sent notification is
#: attributable to a specific formatter revision (§38.2).
FORMATTER_VERSION = 1


def notification_dedup_key(*, booking_id: int, kind: str, formatter_version: int) -> str:
    """Deterministic dedup key for one logical notification.

    A replayed idempotent booking create (same booking row) therefore maps to the
    same key and the ``UNIQUE (type, dedup_key)`` constraint keeps exactly one row
    — no duplicate VK notification for the same event (§18.2, §38.2).
    """
    return f"booking:{booking_id}:{kind}:v{formatter_version}"


def is_notifiable_status(status: str) -> bool:
    """Whether a booking in ``status`` still warrants a VK notification (§38.3)."""
    return status in NOTIFIABLE_BOOKING_STATUSES
