"""Booking domain rules (PROJECT-SPEC §2, §5.5, §9, §10, §14, §15.1, §18, §44).

This is the single canonical place for booking interval, capacity, status and
occupancy-segment algebra. Services call into it rather than re-deriving a rule
per endpoint. The schedule/business-date resolution it depends on lives in
:mod:`app.domain.schedule` (also canonical) and is never duplicated here.

The functions are pure — no database, no clock — so they can be property-tested
exhaustively and unit-tested without PostgreSQL.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta

from app.db.time import SLOT_MINUTES, ceil_to_5_minutes, floor_to_5_minutes, is_on_5_minute_grid
from app.domain.schedule import Shift

# Fixed product rules (PROJECT-SPEC §2).
MIN_BOOKING_MINUTES = 45
BOOKING_HORIZON_MONTHS = 2

# Enum-like values (kept as plain tuples so the ORM/migration CHECK statements
# and the domain validators share one source of truth, §44).
BOOKING_SOURCES: tuple[str, ...] = ("ONLINE", "PHONE", "VK", "WALK_IN", "OTHER")
# ONLINE is reserved for the public endpoint and cannot be chosen by an admin
# create (§8).
ADMIN_BOOKING_SOURCES: tuple[str, ...] = ("PHONE", "VK", "WALK_IN", "OTHER")
# Phone is mandatory for these manual sources; VK/WALK_IN may omit it (§6.6).
SOURCES_REQUIRING_PHONE: tuple[str, ...] = ("ONLINE", "PHONE", "OTHER")

BOOKING_STATUSES: tuple[str, ...] = ("NEW", "WAITING", "OPEN", "CLOSED", "CANCELED")

BOOKING_REASONS: tuple[str, ...] = (
    "GUEST_CANCELED",
    "NO_SHOW",
    "DUPLICATE",
    "UNREACHABLE",
    "RESCHEDULED",
    "GUEST_LATE",
    "CREATION_ERROR",
    "TERMS_REFUSED",
    "INVALID_DATA",
    "MOVED_ELSEWHERE",
    "NO_TABLES",
    "VENUE_CLOSED",
    "ENTRY_REFUSED",
    "OTHER",
)

OCCUPANCY_KINDS: tuple[str, ...] = ("BOOKING", "BLOCK")

# Allowed state transitions (§9). ``UNDO_OPEN`` is a Stage 8 lifecycle command,
# but the transition table already encodes its target set so the model does not
# need to change later.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "NEW": frozenset({"WAITING", "OPEN", "CANCELED"}),
    "WAITING": frozenset({"OPEN", "CANCELED"}),
    "OPEN": frozenset({"CLOSED"}),
    "CLOSED": frozenset(),
    "CANCELED": frozenset(),
}

_PHONE_ALLOWED = re.compile(r"[^0-9+]")


class BookingRuleError(ValueError):
    """A booking payload violates a domain rule (interval/grid/shift/capacity)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def add_months_clamped(value: date_type, months: int) -> date_type:
    """Add calendar months to a date, clamping to the target month's last day (§2).

    ``2026-12-31 + 2 months == 2027-02-28`` (``2027-02-29`` in a leap year).
    """
    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date_type(year, month, day)


def booking_horizon_end(from_date: date_type) -> date_type:
    """The inclusive maximum business date for a booking created at ``from_date``."""
    return add_months_clamped(from_date, BOOKING_HORIZON_MONTHS)


def normalize_phone(raw: str) -> str:
    """Return a deterministic phone fingerprint used for exact admin search.

    Only digits and a leading ``+`` survive, so ``+7 (999) 123-45-67`` and
    ``+79991234567`` match. This is *not* the abuse fingerprint (which is an
    HMAC over the raw client IP) and it is not a phone confirmation.
    """
    digits = _PHONE_ALLOWED.sub("", raw)
    if digits.startswith("+"):
        return "+" + digits[1:].replace("+", "")
    return digits


@dataclass(frozen=True, slots=True)
class Segment:
    """An effective occupancy segment used by ``truncate_segment_at``."""

    starts_at: datetime
    ends_at: datetime
    is_active: bool = True


def truncate_segment_at(segment: Segment, t: datetime) -> Segment:
    """End an effective segment at actual time ``t`` (PROJECT-SPEC §15.1).

    This is the *only* implementation of that rule. ``close``, ``remove table``
    and ``replace table`` must call it instead of re-deriving the boundary, so
    no ``ends_at <= starts_at`` can appear::

        if segment.starts_at >= t:      segment stays entirely in the future
        elif segment.starts_at < t < segment.ends_at:  segment.ends_at = t
        else:                            segment is fully in the past, unchanged
    """
    if segment.starts_at >= t:
        return Segment(segment.starts_at, segment.ends_at, False)
    if t < segment.ends_at:
        return Segment(segment.starts_at, t, segment.is_active)
    return segment


def intervals_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """Half-open interval overlap: ``[a_start, a_end)`` vs ``[b_start, b_end)``.

    Touching at exactly one endpoint is *not* an overlap (§12).
    """
    return a_start < b_end and b_start < a_end


def validate_booking_interval(
    *,
    starts_at: datetime,
    ends_at: datetime,
    shift: Shift,
    now: datetime | None = None,
    min_minutes: int = MIN_BOOKING_MINUTES,
) -> None:
    """Validate a planned booking interval against its shift snapshot (§18).

    Raises ``BookingRuleError`` on the first violation. ``now`` enables the
    "not in the past" rule for manual create/reschedule.
    """
    if ends_at <= starts_at:
        raise BookingRuleError("ends_at must be after starts_at")
    if not is_on_5_minute_grid(starts_at) or not is_on_5_minute_grid(ends_at):
        raise BookingRuleError(f"starts_at and ends_at must be on the {SLOT_MINUTES}-minute grid")
    if starts_at < shift.start or ends_at > shift.end:
        raise BookingRuleError("the booking interval must stay inside one shift")
    if ends_at - starts_at < timedelta(minutes=min_minutes):
        raise BookingRuleError(f"a booking must last at least {min_minutes} minutes")
    if now is not None and starts_at < ceil_to_5_minutes(now):
        raise BookingRuleError("a manual booking cannot start in the past")


def earliest_start(now: datetime) -> datetime:
    """The earliest allowed manual start: ``ceil_to_5_minutes(operation_now)``."""
    return ceil_to_5_minutes(now)


def grid_shift_end(shift: Shift) -> datetime:
    """Return ``floor_to_5_minutes(shift.end)`` and assert it is grid-aligned.

    Schedule boundaries are DB-CHECKed onto the grid, so the assertion documents
    the assumption instead of silently tolerating an invalid schedule (§19.1).
    """
    value = floor_to_5_minutes(shift.end)
    if value != shift.end:
        raise BookingRuleError("the resolved shift end is not on the 5-minute grid")
    return value


def walk_in_start(*, now: datetime, ends_at: datetime, shift: Shift) -> datetime:
    """Derive the plan from locked server time, including the near-close exception."""
    if not shift.start <= now < shift.end:
        raise BookingRuleError("WALK_IN requires a current open shift")
    start = ceil_to_5_minutes(now)
    end = grid_shift_end(shift)
    required = min(timedelta(minutes=MIN_BOOKING_MINUTES), end - start)
    validate_booking_interval(starts_at=start, ends_at=ends_at, shift=shift, min_minutes=0)
    if ends_at - start < required:
        raise BookingRuleError("WALK_IN must cover 45 minutes or the remaining shift")
    return start


def capacity_sufficient(
    *,
    party_size: int,
    assigned: list[tuple[int, datetime, datetime]],
    interval_start: datetime,
    interval_end: datetime,
) -> bool:
    """Return whether every sub-interval of ``[start, end)`` has enough capacity (§14).

    ``assigned`` is ``(capacity, segment_start, segment_end)`` for each assigned
    table segment. The set of covering segments can change at segment
    boundaries, so the check sweeps all boundary points and requires
    ``sum(capacity covering the sub-interval) >= party_size`` on each.
    """
    boundaries = {interval_start, interval_end}
    for _capacity, seg_start, seg_end in assigned:
        if interval_start < seg_start < interval_end:
            boundaries.add(seg_start)
        if interval_start < seg_end < interval_end:
            boundaries.add(seg_end)
    points = sorted(boundaries)
    for left, right in zip(points, points[1:], strict=False):
        if left >= right:
            continue
        total = 0
        for capacity, seg_start, seg_end in assigned:
            if seg_start <= left and right <= seg_end:
                total += capacity
        if party_size > total:
            return False
    return True


def can_transition(current: str, target: str) -> bool:
    """Return whether the state machine permits ``current -> target`` (§9)."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def status_timestamps_consistent(
    *,
    status: str,
    waiting_at: datetime | None,
    opened_at: datetime | None,
    closed_at: datetime | None,
    canceled_at: datetime | None,
    cancellation_reason: str | None,
) -> bool:
    """Mirror the DB status<->timestamp CHECK as a pure predicate (§10)."""
    if status == "NEW":
        return (
            waiting_at is None and opened_at is None and closed_at is None and canceled_at is None
        )
    if status == "WAITING":
        return (
            waiting_at is not None
            and opened_at is None
            and closed_at is None
            and canceled_at is None
        )
    if status == "OPEN":
        return opened_at is not None and closed_at is None and canceled_at is None
    if status == "CLOSED":
        return opened_at is not None and closed_at is not None and canceled_at is None
    if status == "CANCELED":
        return (
            opened_at is None
            and closed_at is None
            and canceled_at is not None
            and cancellation_reason is not None
        )
    return False
