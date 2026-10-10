"""Executable invariant audit (PROJECT-SPEC §60).

Read-only SQL checks that **detect** — never fix — violations of the critical
invariants. They run after the corresponding PostgreSQL integration tests, in the
pre-release audit, and from the periodic maintenance job. Production audit only
detects and alerts (§60): nothing here mutates data.

Two severities are reported:

* ``corruption`` — a state the schema and services are supposed to make
  impossible (a broken invariant); on a healthy database every one is ``0``;
* ``alert`` — an operational condition worth surfacing, not a data defect
  (for example an unacknowledged ``DEAD`` outbox row).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Severity = Literal["corruption", "alert"]

#: JSON object keys that must never appear in an event or outbox payload (§42.2).
#: The same list guards both ``booking_events.payload`` and
#: ``notification_outbox.payload`` so the two payload contracts cannot drift.
_FORBIDDEN_PII_KEYS = (
    "guest_name",
    "name",
    "phone",
    "guest_phone",
    "guest_phone_raw",
    "guest_phone_normalized",
    "comment",
    "guest_comment",
    "email",
    "ip",
    "request_ip",
    "request_ip_hmac",
)


@dataclass(frozen=True, slots=True)
class InvariantCheck:
    """One named audit check and the number of violating rows it found."""

    name: str
    count: int
    severity: Severity


@dataclass(frozen=True, slots=True)
class InvariantReport:
    """The result of a full audit pass."""

    checks: tuple[InvariantCheck, ...]

    @property
    def corruptions(self) -> dict[str, int]:
        return {c.name: c.count for c in self.checks if c.severity == "corruption" and c.count}

    @property
    def alerts(self) -> dict[str, int]:
        return {c.name: c.count for c in self.checks if c.severity == "alert" and c.count}

    @property
    def total_corruptions(self) -> int:
        return sum(c.count for c in self.checks if c.severity == "corruption")

    @property
    def is_clean(self) -> bool:
        """Whether every corruption check found zero rows."""
        return self.total_corruptions == 0


# Each entry is (name, severity, SQL). SQL always returns a single count(*).
_SCALAR_CHECKS: tuple[tuple[str, Severity, str], ...] = (
    (
        # The exclusion constraint already forbids this; the audit proves it holds.
        "overlapping_active_occupancies",
        "corruption",
        """
        SELECT count(*)
        FROM table_occupancies a
        JOIN table_occupancies b
          ON a.table_id = b.table_id AND a.id < b.id
        WHERE a.is_active AND b.is_active
          AND a.starts_at < b.ends_at AND b.starts_at < a.ends_at
        """,
    ),
    (
        "canceled_with_active_booking_occupancy",
        "corruption",
        """
        SELECT count(*)
        FROM bookings bk
        JOIN table_occupancies o
          ON o.booking_id = bk.id AND o.venue_id = bk.venue_id
        WHERE bk.status = 'CANCELED' AND o.is_active AND o.kind = 'BOOKING'
        """,
    ),
    (
        "closed_with_live_rows",
        "corruption",
        """
        SELECT count(*)
        FROM bookings bk
        JOIN booking_live_tables lt
          ON lt.booking_id = bk.id AND lt.venue_id = bk.venue_id
        WHERE bk.status = 'CLOSED'
        """,
    ),
    (
        "cross_tenant_broken_references",
        "corruption",
        """
        SELECT
            (SELECT count(*) FROM booking_events e
                JOIN bookings b ON b.id = e.booking_id
                WHERE e.venue_id <> b.venue_id)
          + (SELECT count(*) FROM table_occupancies o
                JOIN bookings b ON b.id = o.booking_id
                WHERE o.venue_id <> b.venue_id)
          + (SELECT count(*) FROM booking_live_tables l
                JOIN bookings b ON b.id = l.booking_id
                WHERE l.venue_id <> b.venue_id)
        """,
    ),
)

#: Checks that need the current time and/or the stuck-lease threshold.
_TIMED_CHECKS: tuple[tuple[str, Severity, str], ...] = (
    (
        "open_in_shift_without_live_rows",
        "corruption",
        """
        SELECT count(*)
        FROM bookings bk
        WHERE bk.status = 'OPEN'
          AND :now >= bk.shift_starts_at AND :now < bk.shift_ends_at
          AND NOT EXISTS (
              SELECT 1 FROM booking_live_tables lt
              WHERE lt.booking_id = bk.id AND lt.venue_id = bk.venue_id
          )
        """,
    ),
    (
        "stuck_processing_outbox_lease",
        "corruption",
        """
        SELECT count(*)
        FROM notification_outbox
        WHERE status = 'PROCESSING'
          AND (locked_until IS NULL OR locked_until < :stuck_before)
        """,
    ),
    (
        "unacknowledged_dead_outbox",
        "alert",
        """
        SELECT count(*)
        FROM notification_outbox
        WHERE status = 'DEAD' AND acknowledged_at IS NULL
        """,
    ),
)


async def _scalar(session: AsyncSession, statement: str, params: dict[str, object]) -> int:
    value = await session.scalar(text(statement), params)
    return int(value or 0)


async def event_payload_pii_violations(session: AsyncSession) -> int:
    """Count ``booking_events`` whose payload carries a forbidden PII key (§42.2)."""
    return await _scalar(
        session,
        """
        SELECT count(*)
        FROM booking_events e
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_object_keys(e.payload) AS key
            WHERE lower(key) = ANY(:forbidden)
        )
        """,
        {"forbidden": list(_FORBIDDEN_PII_KEYS)},
    )


async def outbox_payload_pii_violations(session: AsyncSession) -> int:
    """Count ``notification_outbox`` rows whose payload carries a PII key (§42.2).

    ``notification_outbox.payload`` is designed to hold only ``booking_id``,
    ``kind`` and ``formatter_version`` (§38.2). The audit mirrors the
    ``booking_events`` PII check so a forbidden key added to either payload over
    time is caught without changing any existing corruption/alert semantics.
    """
    return await _scalar(
        session,
        """
        SELECT count(*)
        FROM notification_outbox o
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_object_keys(o.payload) AS key
            WHERE lower(key) = ANY(:forbidden)
        )
        """,
        {"forbidden": list(_FORBIDDEN_PII_KEYS)},
    )


async def run_invariant_audit(
    session: AsyncSession, *, now: datetime, stuck_before: datetime
) -> InvariantReport:
    """Run every invariant check and return a read-only report (§60)."""
    checks: list[InvariantCheck] = []
    for name, severity, statement in _SCALAR_CHECKS:
        checks.append(InvariantCheck(name, await _scalar(session, statement, {}), severity))
    for name, severity, statement in _TIMED_CHECKS:
        checks.append(
            InvariantCheck(
                name,
                await _scalar(session, statement, {"now": now, "stuck_before": stuck_before}),
                severity,
            )
        )
    checks.append(
        InvariantCheck(
            "booking_events_pii_keys",
            await event_payload_pii_violations(session),
            "corruption",
        )
    )
    checks.append(
        InvariantCheck(
            "notification_outbox_pii_keys",
            await outbox_payload_pii_violations(session),
            "corruption",
        )
    )
    return InvariantReport(tuple(checks))


__all__ = [
    "InvariantCheck",
    "InvariantReport",
    "event_payload_pii_violations",
    "outbox_payload_pii_violations",
    "run_invariant_audit",
]
