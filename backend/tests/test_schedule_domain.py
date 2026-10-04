"""Unit tests for schedule resolution and business date (PROJECT-SPEC §5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from app.domain.schedule import (
    InvalidScheduleError,
    ScheduleRule,
    ScheduleTable,
    Shift,
    current_business_date,
    describe_business_day,
    find_adjacent_overlaps,
    find_weekly_overlaps,
    is_open_at,
    resolve_shift,
    validate_rule,
    validate_weekday,
)

MSK = ZoneInfo("Europe/Moscow")
UTC_TZ = UTC


def _instant(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """A UTC instant from a Moscow-local wall time (keeps tests readable)."""
    return datetime(year, month, day, hour, minute, tzinfo=MSK).astimezone(UTC)


# 2026-10-05 is a Monday.
MONDAY = date(2026, 10, 5)
assert MONDAY.weekday() == 0


def _daily(open_time: time, close_time: time) -> ScheduleTable:
    rule = ScheduleRule.open(open_time, close_time)
    return ScheduleTable(weekly=dict.fromkeys(range(7), rule), exceptions={})


# --- rule validation -------------------------------------------------------


def test_grid_times_are_accepted() -> None:
    assert ScheduleRule.open(time(10, 5), time(22, 30)).is_open


@pytest.mark.parametrize(
    "open_time,close_time",
    [
        (time(10, 3), time(22, 0)),  # minute not multiple of 5
        (time(10, 0, 30), time(22, 0)),  # seconds
        (time(10, 0, 0, 1), time(22, 0)),  # microseconds
        (time(10, 0), time(10, 0)),  # equal
    ],
)
def test_invalid_rule_times_are_rejected(open_time: time, close_time: time) -> None:
    with pytest.raises(InvalidScheduleError):
        ScheduleRule.open(open_time, close_time)


def test_closed_rule_must_not_carry_times() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_rule(ScheduleRule(is_open=False, open_time=time(10, 0), close_time=None))
    assert validate_rule(ScheduleRule.closed()).is_open is False


def test_open_rule_requires_both_times() -> None:
    with pytest.raises(InvalidScheduleError):
        validate_rule(ScheduleRule(is_open=True, open_time=None, close_time=None))


def test_weekday_bounds() -> None:
    assert validate_weekday(0) == 0
    assert validate_weekday(6) == 6
    with pytest.raises(InvalidScheduleError):
        validate_weekday(7)
    with pytest.raises(InvalidScheduleError):
        validate_weekday(-1)


# --- shift resolution ------------------------------------------------------


def test_daytime_shift_stays_on_the_same_calendar_day() -> None:
    table = _daily(time(10, 0), time(22, 0))
    shift = table.shift_for(MONDAY, MSK)
    assert shift is not None
    assert shift.start == datetime(2026, 10, 5, 10, 0, tzinfo=MSK)
    assert shift.end == datetime(2026, 10, 5, 22, 0, tzinfo=MSK)


def test_overnight_shift_ends_the_next_calendar_day() -> None:
    table = _daily(time(16, 0), time(2, 0))
    shift = table.shift_for(MONDAY, MSK)
    assert shift is not None
    assert shift.start == datetime(2026, 10, 5, 16, 0, tzinfo=MSK)
    assert shift.end == datetime(2026, 10, 6, 2, 0, tzinfo=MSK)
    # The business date is the date the shift started.
    assert shift.business_date == MONDAY


def test_closed_day_resolves_to_no_shift() -> None:
    assert resolve_shift(ScheduleRule.closed(), MONDAY, MSK) is None


# --- boundary: open / close ------------------------------------------------


def test_before_opening() -> None:
    table = _daily(time(10, 0), time(22, 0))
    assert is_open_at(_instant(2026, 10, 5, 9, 55), table, MSK) is False


def test_exactly_at_opening_is_open() -> None:
    table = _daily(time(10, 0), time(22, 0))
    assert is_open_at(_instant(2026, 10, 5, 10, 0), table, MSK) is True


def test_exactly_at_closing_is_closed() -> None:
    # The shift interval is half-open [start, end).
    table = _daily(time(10, 0), time(22, 0))
    assert is_open_at(_instant(2026, 10, 5, 22, 0), table, MSK) is False


def test_after_closing() -> None:
    table = _daily(time(10, 0), time(22, 0))
    assert is_open_at(_instant(2026, 10, 5, 23, 0), table, MSK) is False


def test_one_minute_before_close_is_open() -> None:
    table = _daily(time(10, 0), time(22, 0))
    assert is_open_at(_instant(2026, 10, 5, 21, 55), table, MSK) is True


# --- overnight: calendar-date transition -----------------------------------


def test_overnight_after_midnight_belongs_to_previous_business_date() -> None:
    table = _daily(time(16, 0), time(2, 0))
    now = _instant(2026, 10, 6, 1, 0)  # Tue 01:00 local, still Mon's shift
    assert is_open_at(now, table, MSK) is True
    assert current_business_date(now, table, MSK) == MONDAY


def test_overnight_exactly_at_close_is_not_contained() -> None:
    table = _daily(time(16, 0), time(2, 0))
    now = _instant(2026, 10, 6, 2, 0)  # close on Tue 02:00
    assert is_open_at(now, table, MSK) is False
    # Not in Mon's (ended) shift, not in Tue's (starts 16:00) -> local date.
    assert current_business_date(now, table, MSK) == date(2026, 10, 6)


def test_overnight_exactly_at_open_is_contained() -> None:
    table = _daily(time(16, 0), time(2, 0))
    now = _instant(2026, 10, 5, 16, 0)
    assert is_open_at(now, table, MSK) is True
    assert current_business_date(now, table, MSK) == MONDAY


# --- current_business_date --------------------------------------------------


def test_current_business_date_mid_day_shift() -> None:
    table = _daily(time(10, 0), time(22, 0))
    assert current_business_date(_instant(2026, 10, 5, 15, 0), table, MSK) == MONDAY


def test_current_business_date_outside_any_shift_defaults_to_local_date() -> None:
    table = _daily(time(10, 0), time(22, 0))
    # 03:00 on Tuesday is outside both Monday's (ended) and Tuesday's shift.
    assert current_business_date(_instant(2026, 10, 6, 3, 0), table, MSK) == date(2026, 10, 6)


def test_current_business_date_depends_on_venue_timezone() -> None:
    # 2026-10-05 22:30 UTC == 2026-10-06 01:30 Moscow; still Monday's overnight.
    table = _daily(time(16, 0), time(2, 0))
    now_utc = datetime(2026, 10, 5, 22, 30, tzinfo=UTC)
    assert current_business_date(now_utc, table, MSK) == MONDAY
    # The default step 5 uses the *venue-local* calendar date, not UTC.
    closed_table = _daily(time(10, 0), time(11, 0))
    assert current_business_date(now_utc, closed_table, MSK) == date(2026, 10, 6)
    assert current_business_date(now_utc, closed_table, UTC_TZ) == date(2026, 10, 5)


# --- exceptions override weekly --------------------------------------------


def test_exception_overrides_weekly_rule() -> None:
    weekly = ScheduleRule.open(time(10, 0), time(22, 0))
    table = ScheduleTable(
        weekly=dict.fromkeys(range(7), weekly),
        exceptions={
            MONDAY: ScheduleRule.open(time(12, 0), time(18, 0)),
            date(2026, 10, 6): ScheduleRule.closed(),
        },
    )
    assert table.shift_for(MONDAY, MSK).start == datetime(2026, 10, 5, 12, 0, tzinfo=MSK)
    # Tuesday is closed by exception.
    assert table.shift_for(date(2026, 10, 6), MSK) is None
    # Wednesday falls back to the weekly rule.
    assert table.shift_for(date(2026, 10, 7), MSK).start == datetime(2026, 10, 7, 10, 0, tzinfo=MSK)


def test_exception_removal_falls_back_to_weekly() -> None:
    weekly = ScheduleRule.open(time(10, 0), time(22, 0))
    table = ScheduleTable(weekly=dict.fromkeys(range(7), weekly), exceptions={})
    with_exc = table.with_exception(MONDAY, ScheduleRule.closed())
    assert with_exc.shift_for(MONDAY, MSK) is None
    assert with_exc.without_exception(MONDAY).shift_for(MONDAY, MSK).start == datetime(
        2026, 10, 5, 10, 0, tzinfo=MSK
    )


# --- adjacent-shift overlap ------------------------------------------------


def test_adjacent_overnight_overlap_is_detected() -> None:
    # Friday 16:00 -> Sat 04:00 and Saturday 03:00 -> Sun 02:00 overlap (§5.4).
    friday, saturday = 4, 5
    weekly = {
        friday: ScheduleRule.open(time(16, 0), time(4, 0)),
        saturday: ScheduleRule.open(time(3, 0), time(2, 0)),
    }
    conflicts = find_weekly_overlaps(weekly, MSK)
    assert len(conflicts) == 1
    assert conflicts[0].earlier.business_date.weekday() == friday
    assert conflicts[0].later.business_date.weekday() == saturday


def test_valid_overnight_neighbours_do_not_conflict() -> None:
    # Friday 16:00 -> 02:00, Saturday 10:00 -> 22:00: no overlap.
    weekly = {
        4: ScheduleRule.open(time(16, 0), time(2, 0)),
        5: ScheduleRule.open(time(10, 0), time(22, 0)),
    }
    assert find_weekly_overlaps(weekly, MSK) == []


def test_weekly_overlap_across_sunday_monday_wrap_is_detected() -> None:
    # Sunday 22:00 -> 06:00 (Mon) would overlap Monday 05:00 opening.
    weekly = {
        6: ScheduleRule.open(time(22, 0), time(6, 0)),
        0: ScheduleRule.open(time(5, 0), time(12, 0)),
    }
    conflicts = find_weekly_overlaps(weekly, MSK)
    assert any(c.later.business_date.weekday() == 0 for c in conflicts)


def test_adjacent_exception_overlap_checks_previous_and_next() -> None:
    weekly = {w: ScheduleRule.open(time(10, 0), time(22, 0)) for w in range(7)}
    table = ScheduleTable(weekly=weekly, exceptions={})
    # An overnight exception on Monday 20:00 -> 06:00 collides with Tuesday 10:00?
    # It ends Tue 06:00, before Tue 10:00 -> no conflict.
    safe = table.with_exception(MONDAY, ScheduleRule.open(time(20, 0), time(6, 0)))
    assert find_adjacent_overlaps(safe, MONDAY, MSK) == []
    # An overnight exception ending after Tuesday's opening does conflict.
    unsafe = table.with_exception(MONDAY, ScheduleRule.open(time(20, 0), time(12, 0)))
    conflicts = find_adjacent_overlaps(unsafe, MONDAY, MSK)
    assert len(conflicts) == 1
    assert conflicts[0].earlier.business_date == MONDAY


def test_shift_containment_helper() -> None:
    shift = Shift(
        business_date=MONDAY,
        start=datetime(2026, 10, 5, 16, 0, tzinfo=MSK),
        end=datetime(2026, 10, 6, 2, 0, tzinfo=MSK),
    )
    assert datetime(2026, 10, 6, 1, 59, tzinfo=MSK) in shift
    assert datetime(2026, 10, 6, 2, 0, tzinfo=MSK) not in shift


# --- describe_business_day -------------------------------------------------


def test_describe_business_day_combines_shift_and_current_state() -> None:
    table = _daily(time(16, 0), time(2, 0))
    state = describe_business_day(table, MSK, _instant(2026, 10, 5, 17, 0), MONDAY)
    assert state.is_open is True
    assert state.shift_start == datetime(2026, 10, 5, 16, 0, tzinfo=MSK)
    assert state.shift_end == datetime(2026, 10, 6, 2, 0, tzinfo=MSK)
    assert state.current_business_date == MONDAY
    assert state.is_open_now is True
