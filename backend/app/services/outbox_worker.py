"DB-backed outbox lifecycle and tenant-scoped DEAD administration."

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Booking,
    Hall,
    NotificationOutbox,
    Table,
    TableOccupancy,
    Venue,
    VenueVKIntegration,
)
from app.domain.outbox import NOTIFICATION_KIND_BOOKING_CREATED, is_notifiable_status
from app.integrations.vk.formatting import BookingNotificationData


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    outbox_id: int
    venue_id: int
    type: str
    dedup_key: str
    payload: dict[str, object]
    attempts: int
    expires_at: datetime
    provider_dedup_id: str


@dataclass(frozen=True, slots=True)
class SkipDecision:
    reason: str


def stable_provider_dedup_id(*, type: str, dedup_key: str) -> str:
    digest = hashlib.sha256(f"{type}:{dedup_key}".encode()).hexdigest()
    return f"takeplace-{digest[:40]}"


def compute_backoff_seconds(*, attempts: int, base_seconds: int, max_seconds: int) -> int:
    return int(min(base_seconds * (2 ** max(0, attempts - 1)), max_seconds))


def decide_skip(
    *, now: datetime, expires_at: datetime, booking: Booking | None
) -> SkipDecision | None:
    if now >= expires_at:
        return SkipDecision("EXPIRED")
    if booking is None or booking.anonymized_at is not None:
        return SkipDecision("BOOKING_ANONYMIZED")
    if not is_notifiable_status(booking.status):
        return SkipDecision("BOOKING_INACTIVE")
    return None


async def integration_skip_reason(session: AsyncSession, *, venue_id: int) -> str | None:
    enabled = await session.scalar(
        select(VenueVKIntegration.enabled).where(VenueVKIntegration.venue_id == venue_id)
    )
    return None if enabled is True else "INTEGRATION_DISABLED"


async def claim_jobs(
    session: AsyncSession,
    *,
    now: datetime,
    lease_seconds: int,
    batch_size: int,
    throttle_window_seconds: int = 60,
    throttle_max_per_window: int = 10,
) -> list[ClaimedJob]:
    due = (
        select(NotificationOutbox)
        .where(
            (
                (NotificationOutbox.status.in_(("PENDING", "RETRY")))
                & (NotificationOutbox.next_attempt_at <= now)
            )
            | (
                (NotificationOutbox.status == "PROCESSING")
                & (NotificationOutbox.locked_until <= now)
            )
        )
        .order_by(NotificationOutbox.next_attempt_at, NotificationOutbox.id)
        .limit(batch_size)
    )
    rows = (await session.scalars(due.with_for_update(skip_locked=True))).all()
    claimed = []
    for row in rows:
        booking_id = booking_id_of(row.payload)
        booking = (
            await reload_booking(session, venue_id=row.venue_id, booking_id=booking_id)
            if booking_id is not None
            else None
        )
        skip = decide_skip(now=now, expires_at=row.expires_at, booking=booking)
        reason = (
            skip.reason if skip else await integration_skip_reason(session, venue_id=row.venue_id)
        )
        if reason:
            row.status = "SKIPPED"
            row.skipped_at = now
            row.skip_reason = reason
            row.locked_until = None
            row.last_error = None
            continue
        # Reserve capacity under the integration row lock. Competing claimers
        # skip locked venues; PROCESSING reservations count toward the throttle.
        integration = await session.scalar(
            select(VenueVKIntegration)
            .where(VenueVKIntegration.venue_id == row.venue_id)
            .with_for_update(skip_locked=True)
        )
        if integration is None or await throttle_wait(
            session,
            venue_id=row.venue_id,
            now=now,
            window_seconds=throttle_window_seconds,
            max_per_window=throttle_max_per_window,
        ):
            row.status = "RETRY"
            row.next_attempt_at = now + timedelta(seconds=throttle_window_seconds)
            row.locked_until = None
            continue
        provider_id = row.provider_dedup_id or stable_provider_dedup_id(
            type=row.type, dedup_key=row.dedup_key
        )
        row.status = "PROCESSING"
        row.locked_until = now + timedelta(seconds=lease_seconds)
        row.attempts += 1
        row.provider_dedup_id = provider_id
        row.last_error = None
        await session.flush()
        claimed.append(
            ClaimedJob(
                row.id,
                row.venue_id,
                row.type,
                row.dedup_key,
                dict(row.payload),
                row.attempts,
                row.expires_at,
                provider_id,
            )
        )
    await session.flush()
    return claimed


async def mark_skipped(
    session: AsyncSession, *, job: ClaimedJob, now: datetime, reason: str
) -> None:
    await session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == job.outbox_id,
            NotificationOutbox.status == "PROCESSING",
            NotificationOutbox.attempts == job.attempts,
        )
        .values(
            status="SKIPPED", skipped_at=now, skip_reason=reason, locked_until=None, last_error=None
        )
    )


async def mark_sent(session: AsyncSession, *, job: ClaimedJob, now: datetime) -> None:
    await session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == job.outbox_id,
            NotificationOutbox.status == "PROCESSING",
            NotificationOutbox.attempts == job.attempts,
        )
        .values(status="SENT", sent_at=now, locked_until=None, last_error=None)
    )


async def mark_retry(
    session: AsyncSession,
    *,
    job: ClaimedJob,
    now: datetime,
    error: str,
    base_seconds: int,
    max_seconds: int,
) -> None:
    await session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == job.outbox_id,
            NotificationOutbox.status == "PROCESSING",
            NotificationOutbox.attempts == job.attempts,
        )
        .values(
            status="RETRY",
            next_attempt_at=now
            + timedelta(
                seconds=compute_backoff_seconds(
                    attempts=job.attempts, base_seconds=base_seconds, max_seconds=max_seconds
                )
            ),
            locked_until=None,
            last_error=error,
        )
    )


async def mark_dead(session: AsyncSession, *, job: ClaimedJob, error: str) -> None:
    await session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == job.outbox_id,
            NotificationOutbox.status == "PROCESSING",
            NotificationOutbox.attempts == job.attempts,
        )
        .values(status="DEAD", locked_until=None, last_error=error)
    )


async def reload_booking(
    session: AsyncSession, *, venue_id: int, booking_id: int
) -> Booking | None:
    return (
        await session.execute(
            select(Booking).where(Booking.id == booking_id, Booking.venue_id == venue_id)
        )
    ).scalar_one_or_none()


async def build_notification_data(
    session: AsyncSession, *, booking: Booking
) -> BookingNotificationData:
    rows = (
        await session.execute(
            select(Hall.name, Table.number)
            .join(Table, Table.hall_id == Hall.id)
            .join(
                TableOccupancy,
                (TableOccupancy.table_id == Table.id) & (TableOccupancy.venue_id == Table.venue_id),
            )
            .where(
                TableOccupancy.venue_id == booking.venue_id,
                TableOccupancy.booking_id == booking.id,
                TableOccupancy.kind == "BOOKING",
                TableOccupancy.is_active.is_(True),
            )
            .order_by(Hall.name, Table.number)
        )
    ).all()
    timezone = await session.scalar(select(Venue.timezone).where(Venue.id == booking.venue_id))
    return BookingNotificationData(
        booking_number=booking.number,
        starts_at=booking.starts_at,
        ends_at=booking.ends_at,
        guest_name=booking.guest_name or "",
        party_size=booking.party_size,
        guest_phone_raw=booking.guest_phone_raw,
        tables=tuple((str(h), str(t)) for h, t in rows),
        timezone=timezone or "UTC",
    )


def kind_of(payload: dict[str, object]) -> str:
    value = payload.get("kind")
    return value if isinstance(value, str) and value else NOTIFICATION_KIND_BOOKING_CREATED


def booking_id_of(payload: dict[str, object]) -> int | None:
    value = payload.get("booking_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True, slots=True)
class OutboxMetrics:
    oldest_pending_age_seconds: float | None
    pending: int
    retry: int
    processing: int
    unacknowledged_dead: int
    total_dead: int
    skipped_by_reason: dict[str, int]


async def collect_metrics(session: AsyncSession, *, now: datetime) -> OutboxMetrics:
    counts = {
        str(s): int(c)
        for s, c in (
            await session.execute(
                select(NotificationOutbox.status, func.count()).group_by(NotificationOutbox.status)
            )
        ).all()
    }
    skipped = (
        await session.execute(
            select(NotificationOutbox.skip_reason, func.count())
            .where(NotificationOutbox.status == "SKIPPED")
            .group_by(NotificationOutbox.skip_reason)
        )
    ).all()
    unack = int(
        await session.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(
                NotificationOutbox.status == "DEAD", NotificationOutbox.acknowledged_at.is_(None)
            )
        )
        or 0
    )
    oldest = await session.scalar(
        select(func.min(NotificationOutbox.created_at)).where(
            NotificationOutbox.status.in_(("PENDING", "RETRY"))
        )
    )
    return OutboxMetrics(
        (now - oldest).total_seconds() if oldest else None,
        counts.get("PENDING", 0),
        counts.get("RETRY", 0),
        counts.get("PROCESSING", 0),
        unack,
        counts.get("DEAD", 0),
        {str(r): int(c) for r, c in skipped},
    )


async def throttle_wait(
    session: AsyncSession, *, venue_id: int, now: datetime, window_seconds: int, max_per_window: int
) -> bool:
    if max_per_window <= 0:
        return False
    sent = int(
        await session.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(
                NotificationOutbox.venue_id == venue_id,
                (
                    (NotificationOutbox.status == "SENT")
                    & (NotificationOutbox.sent_at >= now - timedelta(seconds=window_seconds))
                )
                | (
                    (NotificationOutbox.status == "PROCESSING")
                    & (NotificationOutbox.locked_until > now)
                ),
            )
        )
        or 0
    )
    return sent >= max_per_window


@dataclass(frozen=True, slots=True)
class DeadJobSummary:
    id: int
    type: str
    status: str
    attempts: int
    created_at: datetime
    expires_at: datetime
    last_error: str | None
    acknowledged_at: datetime | None


def summary_of(row: NotificationOutbox) -> DeadJobSummary:
    return DeadJobSummary(
        row.id,
        row.type,
        row.status,
        row.attempts,
        row.created_at,
        row.expires_at,
        row.last_error,
        row.acknowledged_at,
    )


async def unacknowledged_dead_count(session: AsyncSession, *, venue_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(
                NotificationOutbox.venue_id == venue_id,
                NotificationOutbox.status == "DEAD",
                NotificationOutbox.acknowledged_at.is_(None),
            )
        )
        or 0
    )


async def list_dead_jobs(
    session: AsyncSession, *, venue_id: int, limit: int = 100, before_id: int | None = None
) -> list[DeadJobSummary]:
    statement = select(NotificationOutbox).where(
        NotificationOutbox.venue_id == venue_id, NotificationOutbox.status == "DEAD"
    )
    if before_id is not None:
        statement = statement.where(NotificationOutbox.id < before_id)
    rows = await session.scalars(statement.order_by(NotificationOutbox.id.desc()).limit(limit))
    return [summary_of(row) for row in rows]


async def manual_retry(
    session: AsyncSession, *, venue_id: int, outbox_id: int, now: datetime
) -> DeadJobSummary | None:
    row = (
        await session.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.id == outbox_id, NotificationOutbox.venue_id == venue_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status not in ("DEAD", "RETRY"):
        return None
    row.status = "RETRY"
    row.next_attempt_at = now
    row.locked_until = None
    row.acknowledged_at = None
    row.acknowledged_by_session_id = None
    row.last_error = "manual_retry"
    await session.flush()
    return summary_of(row)


async def acknowledge(
    session: AsyncSession,
    *,
    venue_id: int,
    outbox_id: int,
    now: datetime,
    admin_session_id: int | None,
) -> DeadJobSummary | None:
    row = (
        await session.execute(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.id == outbox_id,
                NotificationOutbox.venue_id == venue_id,
                NotificationOutbox.status == "DEAD",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.acknowledged_at = now
    row.acknowledged_by_session_id = admin_session_id
    await session.flush()
    return summary_of(row)


__all__ = [
    "ClaimedJob",
    "OutboxMetrics",
    "DeadJobSummary",
    "SkipDecision",
    "booking_id_of",
    "build_notification_data",
    "claim_jobs",
    "collect_metrics",
    "compute_backoff_seconds",
    "decide_skip",
    "integration_skip_reason",
    "kind_of",
    "mark_dead",
    "mark_retry",
    "mark_sent",
    "mark_skipped",
    "reload_booking",
    "stable_provider_dedup_id",
    "throttle_wait",
    "list_dead_jobs",
    "manual_retry",
    "acknowledge",
]
