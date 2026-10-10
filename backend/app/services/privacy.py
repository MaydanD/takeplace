"""Booking anonymization and technical-data retention (PROJECT-SPEC §40, §42.2, §60).

Two responsibilities, kept together because both are retention housekeeping and
share the same "never destroy business history" rule:

* :func:`anonymize_terminal_bookings` — irreversibly clears the PII fields of
  **terminal** (``CLOSED``/``CANCELED``) bookings once they reach the configured
  retention age (§42.2). It never touches ``NEW``/``WAITING``/``OPEN``: an
  unresolved active booking is an operational incident, not a reason to erase the
  guest's data. ``anonymized_at`` is the idempotency guard.
* the ``purge_*`` helpers — short-lived technical data: the abuse
  ``request_ip_hmac`` fingerprint (§40), expired admin sessions (§6.3) and
  terminal outbox rows (§6.11).

Everything is batch-based, tenant-scoped where a tenant is supplied, and safe to
re-run: a second pass over already-processed rows is a no-op. No function here
deletes a booking or a booking event; §42.2 keeps operational history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth import purge_expired_sessions

#: Hard cap on batches per invocation so one run can never loop forever even if
#: rows keep arriving; the next maintenance tick continues where this one stopped.
MAX_BATCHES = 100


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """Counts from one retention pass (all non-PII numbers)."""

    anonymized_bookings: int
    cleared_ip_hmacs: int
    deleted_sessions: int
    deleted_outbox_rows: int


def _bounded_batches(processed: int, batch_size: int) -> bool:
    """Whether to keep issuing batches after ``processed`` rows in this pass."""
    return processed == batch_size


async def anonymize_terminal_bookings(
    session: AsyncSession,
    *,
    now: datetime,
    retention_days: int,
    batch_size: int,
    venue_id: int | None = None,
) -> int:
    """Anonymize one batch of terminal bookings older than the retention window.

    Returns the number of rows anonymized. The row lock (``FOR UPDATE SKIP
    LOCKED``) makes concurrent maintenance passes or an in-flight admin mutation
    safe: a booking being edited is skipped, not partially cleared.
    """
    cutoff = now - timedelta(days=retention_days)
    statement = text(
        """
        WITH candidates AS (
            SELECT id
            FROM bookings
            WHERE anonymized_at IS NULL
              AND status IN ('CLOSED', 'CANCELED')
              AND COALESCE(closed_at, canceled_at) IS NOT NULL
              AND COALESCE(closed_at, canceled_at) <= :cutoff
              AND (
                  CAST(:venue_id AS bigint) IS NULL
                  OR venue_id = CAST(:venue_id AS bigint)
              )
            ORDER BY id
            LIMIT :batch_size
            FOR UPDATE SKIP LOCKED
        )
        UPDATE bookings AS b
        SET guest_name = NULL,
            guest_phone_raw = NULL,
            guest_phone_normalized = NULL,
            guest_comment = NULL,
            cancellation_note = NULL,
            public_idempotency_key = NULL,
            public_request_hmac = NULL,
            admin_idempotency_key = NULL,
            admin_request_hmac = NULL,
            request_ip_hmac = NULL,
            request_ip_hmac_expires_at = NULL,
            anonymized_at = :now,
            updated_at = :now
        FROM candidates AS c
        WHERE b.id = c.id
        """
    )
    result = await session.execute(
        statement,
        {
            "cutoff": cutoff,
            "now": now,
            "batch_size": batch_size,
            "venue_id": venue_id,
        },
    )
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def purge_expired_request_ip_hmacs(
    session: AsyncSession, *, now: datetime, batch_size: int | None = None
) -> int:
    """Null the short-lived abuse fingerprint once its TTL has elapsed (§40).

    ``batch_size`` bounds the number of rows cleared per statement; the candidate
    sub-query takes ``FOR UPDATE SKIP LOCKED`` so concurrent maintenance passes do
    not block each other or clear the same row twice. ``None`` keeps the historical
    unbounded behavior for direct callers/tests (audit FIX-05).
    """
    if batch_size is None:
        statement = text(
            """
            UPDATE bookings
            SET request_ip_hmac = NULL, request_ip_hmac_expires_at = NULL
            WHERE request_ip_hmac IS NOT NULL
              AND request_ip_hmac_expires_at IS NOT NULL
              AND request_ip_hmac_expires_at <= :now
            """
        )
    else:
        statement = text(
            """
            UPDATE bookings AS b
            SET request_ip_hmac = NULL, request_ip_hmac_expires_at = NULL
            FROM (
                SELECT id
                FROM bookings
                WHERE request_ip_hmac IS NOT NULL
                  AND request_ip_hmac_expires_at IS NOT NULL
                  AND request_ip_hmac_expires_at <= :now
                ORDER BY id
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
            ) AS candidates
            WHERE b.id = candidates.id
            """
        )
    result = await session.execute(statement, {"now": now, "batch_size": batch_size})
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def purge_expired_admin_sessions(
    session: AsyncSession, *, now: datetime, batch_size: int | None = None
) -> int:
    """Delete expired sessions; history rows keep their ``venue_id`` (ON DELETE SET NULL).

    Delegates to the session-store helper so there is exactly one retention rule
    for ``admin_sessions``. The tenant-safe FK uses ``ON DELETE SET NULL
    (column)`` so a referenced ``booking_events``/``notification_outbox`` row
    survives this delete (§6.10). ``batch_size`` bounds each delete statement.
    """
    return await purge_expired_sessions(session, now=now, batch_size=batch_size)


async def purge_terminal_outbox(
    session: AsyncSession, *, now: datetime, retention_days: int, batch_size: int
) -> int:
    """Delete only terminal outbox rows older than the retention window (§6.11).

    ``PENDING``/``RETRY``/``PROCESSING`` and **unacknowledged** ``DEAD`` are never
    deleted: an unresolved failure must stay visible until an operator handles it.
    """
    cutoff = now - timedelta(days=retention_days)
    result = await session.execute(
        text(
            """
            DELETE FROM notification_outbox
            WHERE id IN (
                SELECT id FROM notification_outbox
                WHERE (status = 'SENT' AND sent_at IS NOT NULL AND sent_at <= :cutoff)
                   OR (status = 'SKIPPED' AND skipped_at IS NOT NULL AND skipped_at <= :cutoff)
                   OR (
                        status = 'DEAD'
                        AND acknowledged_at IS NOT NULL
                        AND acknowledged_at <= :cutoff
                   )
                ORDER BY id
                LIMIT :batch_size
            )
            """
        ),
        {"cutoff": cutoff, "batch_size": batch_size},
    )
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def run_retention(
    session: AsyncSession,
    *,
    now: datetime,
    pii_retention_days: int,
    outbox_retention_days: int,
    batch_size: int,
    venue_id: int | None = None,
) -> RetentionReport:
    """Run anonymization plus technical cleanup until batches are exhausted.

    Each loop iteration re-reads the batch size result: a full batch means "more
    may remain", an empty/short batch ends the pass. ``MAX_BATCHES`` bounds the
    work of a single invocation so maintenance cannot starve the process.
    """
    anonymized = 0
    for _ in range(MAX_BATCHES):
        processed = await anonymize_terminal_bookings(
            session,
            now=now,
            retention_days=pii_retention_days,
            batch_size=batch_size,
            venue_id=venue_id,
        )
        anonymized += processed
        if not _bounded_batches(processed, batch_size):
            break

    deleted_outbox = 0
    for _ in range(MAX_BATCHES):
        processed = await purge_terminal_outbox(
            session, now=now, retention_days=outbox_retention_days, batch_size=batch_size
        )
        deleted_outbox += processed
        if not _bounded_batches(processed, batch_size):
            break

    cleared = 0
    for _ in range(MAX_BATCHES):
        processed = await purge_expired_request_ip_hmacs(session, now=now, batch_size=batch_size)
        cleared += processed
        if not _bounded_batches(processed, batch_size):
            break

    sessions = 0
    for _ in range(MAX_BATCHES):
        processed = await purge_expired_admin_sessions(session, now=now, batch_size=batch_size)
        sessions += processed
        if not _bounded_batches(processed, batch_size):
            break

    return RetentionReport(
        anonymized_bookings=anonymized,
        cleared_ip_hmacs=cleared,
        deleted_sessions=sessions,
        deleted_outbox_rows=deleted_outbox,
    )


__all__ = [
    "MAX_BATCHES",
    "RetentionReport",
    "anonymize_terminal_bookings",
    "purge_expired_admin_sessions",
    "purge_expired_request_ip_hmacs",
    "purge_terminal_outbox",
    "run_retention",
]
