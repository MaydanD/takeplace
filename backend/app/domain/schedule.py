"""Schedule resolution and business date (PROJECT-SPEC §5).

This module is the **single canonical place** for all schedule and
business-date logic. Later stages (bookings, availability, lifecycle) must call
into it rather than re-deriving a business date per endpoint.

Concept map (PROJECT-SPEC §4.1):

* UTC instant          — an aware ``datetime`` in UTC;
* venue-local datetime — ``instant.astimezone(venue_tz)``;
* local date           — the calendar date of a venue-local datetime;
* business date        — the date a shift *starts* on; a shift that crosses
  midnight belongs to the date it began (§5.1);
* local schedule time  — a naive ``time`` on the 5-minute grid.

The rules are pure functions of a schedule table (weekly rows + date
exceptions), a timezone and an instant, so they can be tested exhaustively
without a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time, timedelta, tzinfo

# 5-minute scheduling grid shared with bookings (PROJECT-SPEC §2).
SCHEDULE_GRID_MINUTES = 5
WEEKDAY_MIN = 0
WEEKDAY_MAX = 6

# A fixed Monday used to materialise one representative week for the weekly
# adjacency check. The date itself is irrelevant; only the weekday sequence is.
_WEEK_REFERENCE_DATE = date_type(2026, 1, 5)


class InvalidScheduleError(ValueError):
    """A schedule row violates the grid / consistency rules (§5.2, §5.3)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ScheduleRule:
    """One resolved "shift or closed" rule for a business date.

    Both ``weekly_schedules.is_open`` and ``schedule_exceptions.is_closed`` map
    onto this shape, so resolution has a single representation.
    """

    is_open: bool
    open_time: time | None
    close_time: time | None

    @classmethod
    def closed(cls) -> ScheduleRule:
        return cls(is_open=False, open_time=None, close_time=None)

    @classmethod
    def open(cls, open_time: time, close_time: time) -> ScheduleRule:
        rule = cls(is_open=True, open_time=open_time, close_time=close_time)
        validate_rule(rule)
        return rule


def _is_on_grid(value: time) -> bool:
    return (
        value.second == 0 and value.microsecond == 0 and value.minute % SCHEDULE_GRID_MINUTES == 0
    )


def validate_rule(rule: ScheduleRule) -> ScheduleRule:
    """Validate a rule's internal consistency and 5-minute grid alignment.

    Mirrors the DB CHECK (§5.2, §5.3): an open rule has both times, they are
    grid-aligned and different; a closed rule has no times.
    """
    if rule.is_open:
        if rule.open_time is None or rule.close_time is None:
            raise InvalidScheduleError("an open day requires both open_time and close_time")
        if not _is_on_grid(rule.open_time) or not _is_on_grid(rule.close_time):
            raise InvalidScheduleError(
                f"times must be on the {SCHEDULE_GRID_MINUTES}-minute grid (whole minutes, "
                "multiples of 5, no seconds)"
            )
        if rule.open_time == rule.close_time:
            raise InvalidScheduleError("open_time and close_time must differ")
    else:
        if rule.open_time is not None or rule.close_time is not None:
            raise InvalidScheduleError("a closed day must not carry open_time/close_time")
    return rule


def validate_weekday(weekday: int) -> int:
    if not WEEKDAY_MIN <= weekday <= WEEKDAY_MAX:
        raise InvalidScheduleError(f"weekday must be between {WEEKDAY_MIN} and {WEEKDAY_MAX}")
    return weekday


@dataclass(frozen=True, slots=True)
class Shift:
    """A resolved shift in venue-local/absolute time.

    ``business_date`` is the date the shift *starts* on. ``start`` and ``end``
    are absolute (timezone-aware) instants.
    """

    business_date: date_type
    start: datetime
    end: datetime

    def __contains__(self, instant: datetime) -> bool:
        # Half-open interval [start, end): a booking ending exactly at ``end``
        # is outside the shift.
        return self.start <= instant < self.end

    def overlaps(self, other: Shift) -> bool:
        return self.start < other.end and other.start < self.end


def resolve_shift(rule: ScheduleRule, business_date: date_type, tz: tzinfo) -> Shift | None:
    """Resolve a rule for ``business_date`` into an absolute shift, or ``None``.

    If ``close_time < open_time`` the shift ends on the next calendar day
    (§5.2). ``close_time == open_time`` is invalid and rejected earlier.
    """
    if not rule.is_open:
        return None
    if rule.open_time is None or rule.close_time is None:  # pragma: no cover - validated
        raise InvalidScheduleError("an open rule requires both open_time and close_time")
    start = datetime.combine(business_date, rule.open_time, tzinfo=tz)
    close_date = business_date
    if rule.close_time < rule.open_time:
        close_date = business_date + timedelta(days=1)
    end = datetime.combine(close_date, rule.close_time, tzinfo=tz)
    return Shift(business_date=business_date, start=start, end=end)


@dataclass(frozen=True, slots=True)
class ScheduleTable:
    """The effective schedule of one venue: weekly rows plus date exceptions.

    A missing weekday row is treated as closed, so an incomplete table can only
    ever be *more* closed, never accidentally open.
    """

    weekly: Mapping[int, ScheduleRule]
    exceptions: Mapping[date_type, ScheduleRule]

    def rule_for(self, business_date: date_type) -> ScheduleRule:
        exception = self.exceptions.get(business_date)
        if exception is not None:
            return exception
        return self.weekly.get(business_date.weekday(), ScheduleRule.closed())

    def shift_for(self, business_date: date_type, tz: tzinfo) -> Shift | None:
        return resolve_shift(self.rule_for(business_date), business_date, tz)

    def with_exception(self, business_date: date_type, rule: ScheduleRule) -> ScheduleTable:
        updated = dict(self.exceptions)
        updated[business_date] = rule
        return ScheduleTable(weekly=self.weekly, exceptions=updated)

    def without_exception(self, business_date: date_type) -> ScheduleTable:
        updated = dict(self.exceptions)
        updated.pop(business_date, None)
        return ScheduleTable(weekly=self.weekly, exceptions=updated)


def _shift_containing(
    table: ScheduleTable, now: datetime, tz: tzinfo
) -> tuple[date_type, Shift] | None:
    """Return the shift whose [start, end) contains ``now``, if any (§5.6)."""
    local_date = now.astimezone(tz).date()
    for candidate in (local_date - timedelta(days=1), local_date):
        shift = table.shift_for(candidate, tz)
        if shift is not None and now in shift:
            return candidate, shift
    return None


def current_business_date(now: datetime, table: ScheduleTable, tz: tzinfo) -> date_type:
    """Return the business date ``now`` belongs to (PROJECT-SPEC §5.6).

    For the local calendar date ``D`` of ``now``:

    1. resolve the shift of ``D - 1``;
    2. resolve the shift of ``D``;
    3. if ``now`` is inside ``D - 1``'s shift, the business date is ``D - 1``;
    4. otherwise if it is inside ``D``'s shift, it is ``D``;
    5. otherwise it is ``D``.

    This is why a booking at 02:45 after a 16:00→02:00 shift still belongs to
    the previous day's book.
    """
    containing = _shift_containing(table, now, tz)
    if containing is not None:
        return containing[0]
    return now.astimezone(tz).date()


def is_open_at(now: datetime, table: ScheduleTable, tz: tzinfo) -> bool:
    """Return whether the venue is open at instant ``now`` (§5)."""
    return _shift_containing(table, now, tz) is not None


@dataclass(frozen=True, slots=True)
class ShiftConflict:
    """Two shifts of adjacent business dates that overlap (§5.4)."""

    earlier: Shift
    later: Shift


def find_weekly_overlaps(weekly: Mapping[int, ScheduleRule], tz: tzinfo) -> list[ShiftConflict]:
    """Return overlapping shifts across adjacent weekdays (§5.4).

    Materialises one representative week and compares every pair of consecutive
    business dates, so an overnight shift that runs into the next day is checked
    against the next weekday's start — including the Sunday→Monday wrap.
    """
    shifts: list[Shift] = []
    for offset in range(WEEKDAY_MAX - WEEKDAY_MIN + 2):  # 8 dates: covers the wrap
        day = _WEEK_REFERENCE_DATE + timedelta(days=offset)
        shift = resolve_shift(weekly.get(day.weekday(), ScheduleRule.closed()), day, tz)
        if shift is not None:
            shifts.append(shift)
    return [ShiftConflict(a, b) for a, b in zip(shifts, shifts[1:], strict=False) if a.overlaps(b)]


def find_adjacent_overlaps(
    table: ScheduleTable, business_date: date_type, tz: tzinfo
) -> list[ShiftConflict]:
    """Return overlaps between a business date's shift and its neighbours (§5.4).

    Used when an exception changes (PUT/DELETE): the previous and the next
    business date are resolved with the *effective* table, so an overnight
    exception is checked against both neighbours.
    """
    shifts: list[Shift] = []
    for offset in (-1, 0, 1):
        day = business_date + timedelta(days=offset)
        shift = table.shift_for(day, tz)
        if shift is not None:
            shifts.append(shift)
    return [ShiftConflict(a, b) for a, b in zip(shifts, shifts[1:], strict=False) if a.overlaps(b)]


@dataclass(frozen=True, slots=True)
class BusinessDayState:
    """Computed schedule state for one business date (read model)."""

    business_date: date_type
    is_open: bool
    shift_start: datetime | None
    shift_end: datetime | None
    current_business_date: date_type
    is_open_now: bool


def describe_business_day(
    table: ScheduleTable, tz: tzinfo, now: datetime, business_date: date_type
) -> BusinessDayState:
    """Describe the resolved shift of ``business_date`` and the current state."""
    shift = table.shift_for(business_date, tz)
    return BusinessDayState(
        business_date=business_date,
        is_open=shift is not None,
        shift_start=shift.start if shift else None,
        shift_end=shift.end if shift else None,
        current_business_date=current_business_date(now, table, tz),
        is_open_now=is_open_at(now, table, tz),
    )
