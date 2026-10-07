"""Unit tests for outbox domain rules and worker primitives (§38.2-38.4, §56).

These cover the deterministic, database-free logic: the expiry formula, the
dedup key, the retry backoff and the skip classification. PostgreSQL claim/lease
behaviour is proven separately in the integration suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.outbox import (
    FORMATTER_VERSION,
    is_notifiable_status,
    notification_dedup_key,
)
from app.services.outbox import (
    build_booking_notification_payload,
    notification_expires_at,
)
from app.services.outbox_worker import (
    compute_backoff_seconds,
    decide_skip,
    stable_provider_dedup_id,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


class _Booking:
    """Minimal booking stand-in for the pure skip classifier."""

    def __init__(self, *, status: str, anonymized_at: datetime | None = None) -> None:
        self.status = status
        self.anonymized_at = anonymized_at


def test_expiry_is_min_of_ends_grace_and_max_age() -> None:
    created = NOW
    # ends_at is the earliest term: starts+grace = 13:30, ends = 12:30, max-age = 18:00.
    expires = notification_expires_at(
        created_at=created,
        booking_starts_at=created + timedelta(minutes=30),
        booking_ends_at=created + timedelta(minutes=30, seconds=-1),
        late_grace_seconds=3600,
        max_age_seconds=6 * 3600,
    )
    assert expires == created + timedelta(minutes=29, seconds=59)


def test_expiry_uses_late_grace_when_it_is_earliest() -> None:
    created = NOW
    # starts+grace (13:00) is earlier than ends (15:00) and max-age (18:00).
    expires = notification_expires_at(
        created_at=created,
        booking_starts_at=created + timedelta(hours=1),
        booking_ends_at=created + timedelta(hours=3),
        late_grace_seconds=3600,
        max_age_seconds=6 * 3600,
    )
    assert expires == created + timedelta(hours=2)


def test_expiry_uses_max_age_when_it_is_earliest() -> None:
    created = NOW
    # An old booking: ends_at and starts+grace are both in the past, so the
    # max-age term (created + 6h = 18:00) is not the min; the earliest is ends_at.
    # Use a case where max-age is the strict minimum: a long future booking.
    expires = notification_expires_at(
        created_at=created,
        booking_starts_at=created + timedelta(hours=10),
        booking_ends_at=created + timedelta(hours=12),
        late_grace_seconds=3600,
        max_age_seconds=6 * 3600,
    )
    assert expires == created + timedelta(hours=6)


def test_dedup_key_is_deterministic_per_booking_and_kind() -> None:
    first = notification_dedup_key(booking_id=7, kind="BOOKING_CREATED", formatter_version=1)
    second = notification_dedup_key(booking_id=7, kind="BOOKING_CREATED", formatter_version=1)
    assert first == second
    other = notification_dedup_key(booking_id=8, kind="BOOKING_CREATED", formatter_version=1)
    assert other != first


def test_payload_is_minimal_and_non_pii() -> None:
    payload = build_booking_notification_payload(booking_id=42, kind="BOOKING_CREATED")
    assert payload == {
        "booking_id": 42,
        "kind": "BOOKING_CREATED",
        "formatter_version": FORMATTER_VERSION,
    }
    # No guest name/phone/comment ever lives in the outbox payload (§38.2).
    assert not ({"guest_name", "guest_phone_raw", "guest_comment"} & set(payload))


def test_backoff_is_exponential_and_capped() -> None:
    first = compute_backoff_seconds(attempts=1, base_seconds=30, max_seconds=3600)
    second = compute_backoff_seconds(attempts=2, base_seconds=30, max_seconds=3600)
    third = compute_backoff_seconds(attempts=3, base_seconds=30, max_seconds=3600)
    assert (first, second, third) == (30, 60, 120)
    # A very high attempt count never overflows the cap (no tight loop, §38.3).
    assert compute_backoff_seconds(attempts=40, base_seconds=30, max_seconds=3600) == 3600


def test_skip_expired_takes_precedence() -> None:
    decision = decide_skip(
        now=NOW,
        expires_at=NOW - timedelta(seconds=1),
        booking=_Booking(status="NEW"),
    )
    assert decision is not None and decision.reason == "EXPIRED"


def test_skip_missing_booking_is_anonymized() -> None:
    decision = decide_skip(now=NOW, expires_at=NOW + timedelta(hours=1), booking=None)
    assert decision is not None and decision.reason == "BOOKING_ANONYMIZED"


def test_skip_anonymized_booking() -> None:
    decision = decide_skip(
        now=NOW,
        expires_at=NOW + timedelta(hours=1),
        booking=_Booking(status="NEW", anonymized_at=NOW),
    )
    assert decision is not None and decision.reason == "BOOKING_ANONYMIZED"


def test_skip_inactive_booking_states() -> None:
    for status in ("OPEN", "CLOSED", "CANCELED"):
        decision = decide_skip(
            now=NOW,
            expires_at=NOW + timedelta(hours=1),
            booking=_Booking(status=status),
        )
        assert decision is not None and decision.reason == "BOOKING_INACTIVE", status


def test_new_and_waiting_booking_is_not_skipped() -> None:
    for status in ("NEW", "WAITING"):
        assert (
            decide_skip(
                now=NOW,
                expires_at=NOW + timedelta(hours=1),
                booking=_Booking(status=status),
            )
            is None
        ), status


def test_booking_past_starts_at_inside_grace_is_not_skipped_by_time() -> None:
    """Crossing ``starts_at`` alone is not a skip reason (§38.3, §56)."""
    decision = decide_skip(
        now=NOW,
        expires_at=NOW + timedelta(minutes=20),  # still inside the late-grace/TTL
        booking=_Booking(status="NEW"),
    )
    assert decision is None


def test_is_notifiable_status_only_new_and_waiting() -> None:
    assert is_notifiable_status("NEW") and is_notifiable_status("WAITING")
    assert not any(is_notifiable_status(status) for status in ("OPEN", "CLOSED", "CANCELED"))


def test_provider_dedup_id_is_stable_and_secret_free() -> None:
    first = stable_provider_dedup_id(
        type="ONLINE_BOOKING", dedup_key="booking:1:BOOKING_CREATED:v1"
    )
    second = stable_provider_dedup_id(
        type="ONLINE_BOOKING", dedup_key="booking:1:BOOKING_CREATED:v1"
    )
    assert first == second
    assert first.startswith("takeplace-")
