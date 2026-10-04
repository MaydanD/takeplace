"""Unit and property tests for the booking domain rules (PROJECT-SPEC §2, §14, §15.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from app.domain.booking import (
    ALLOWED_TRANSITIONS,
    BookingRuleError,
    Segment,
    add_months_clamped,
    booking_horizon_end,
    can_transition,
    capacity_sufficient,
    intervals_overlap,
    normalize_phone,
    status_timestamps_consistent,
    truncate_segment_at,
    validate_booking_interval,
)
from app.domain.schedule import Shift
from hypothesis import given
from hypothesis import strategies as st

MSK = timezone(timedelta(hours=3))


def _dt(day: int, hour: int, minute: int = 0, tz=MSK) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=tz)


# --- calendar horizon -------------------------------------------------------


@pytest.mark.parametrize(
    "value,months,expected",
    [
        (date(2026, 12, 31), 2, date(2027, 2, 28)),
        (date(2028, 12, 31), 2, date(2029, 2, 28)),
        (date(2027, 3, 31), -1, date(2027, 2, 28)),
        (date(2026, 1, 15), 2, date(2026, 3, 15)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),  # leap year
    ],
)
def test_add_months_clamped(value: date, months: int, expected: date) -> None:
    assert add_months_clamped(value, months) == expected


def test_booking_horizon_end_is_inclusive_two_months() -> None:
    assert booking_horizon_end(date(2026, 12, 31)) == date(2027, 2, 28)


# --- phone normalization ----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+7 (999) 123-45-67", "+79991234567"),
        ("8-999-123-45-67", "89991234567"),
        ("+7 999 123 45 67 доб. 5", "+799912345675"),
        ("", ""),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


# --- intervals --------------------------------------------------------------


def test_intervals_overlap_half_open() -> None:
    a = (_dt(2, 20), _dt(2, 22))
    # Adjacent intervals touch but do not overlap ([start, end)).
    assert not intervals_overlap(*a, _dt(2, 22), _dt(3, 0))
    assert not intervals_overlap(*a, _dt(2, 18), _dt(2, 20))
    assert intervals_overlap(*a, _dt(2, 21, 55), _dt(2, 23))
    assert intervals_overlap(*a, _dt(2, 19), _dt(2, 21))


# --- truncate_segment_at ----------------------------------------------------


def test_truncate_segment_before_start_deactivates() -> None:
    seg = Segment(_dt(2, 20), _dt(2, 22), True)
    result = truncate_segment_at(seg, _dt(2, 19))
    assert result == Segment(_dt(2, 20), _dt(2, 22), False)


def test_truncate_segment_inside_moves_end() -> None:
    seg = Segment(_dt(2, 20), _dt(2, 23), True)
    result = truncate_segment_at(seg, _dt(2, 21, 7))
    assert result.ends_at == _dt(2, 21, 7)
    assert result.starts_at == _dt(2, 20)


def test_truncate_segment_after_end_leaves_segment() -> None:
    seg = Segment(_dt(2, 20), _dt(2, 22), True)
    assert truncate_segment_at(seg, _dt(2, 22)) == seg
    assert truncate_segment_at(seg, _dt(2, 23)) == seg


@given(
    start=st.integers(min_value=0, max_value=10_000),
    length=st.integers(min_value=1, max_value=10_000),
    point=st.integers(min_value=0, max_value=10_000),
    active=st.booleans(),
)
def test_truncate_never_inverts_and_never_grows(
    start: int, length: int, point: int, active: bool
) -> None:
    """Property: ``ends_at >= starts_at`` and the end never moves later (§15.1)."""
    seg = Segment(
        datetime.fromtimestamp(start * 60, tz=UTC),
        datetime.fromtimestamp((start + length) * 60, tz=UTC),
        active,
    )
    t = datetime.fromtimestamp(point * 60, tz=UTC)
    result = truncate_segment_at(seg, t)
    assert result.ends_at >= result.starts_at
    assert result.ends_at <= seg.ends_at
    assert result.starts_at == seg.starts_at
    if seg.starts_at >= t:
        assert result.is_active is False


# --- interval validation ----------------------------------------------------


def _shift() -> Shift:
    return Shift(business_date=date(2026, 10, 2), start=_dt(2, 16), end=_dt(3, 2))


def test_valid_interval_inside_shift() -> None:
    validate_booking_interval(starts_at=_dt(2, 20), ends_at=_dt(2, 23), shift=_shift())


def test_interval_must_be_grid_aligned() -> None:
    with pytest.raises(BookingRuleError):
        validate_booking_interval(starts_at=_dt(2, 20, 3), ends_at=_dt(2, 22), shift=_shift())


def test_interval_minimum_duration() -> None:
    with pytest.raises(BookingRuleError):
        validate_booking_interval(starts_at=_dt(2, 20), ends_at=_dt(2, 20, 40), shift=_shift())


def test_interval_exact_45_minutes_is_allowed() -> None:
    validate_booking_interval(starts_at=_dt(2, 20), ends_at=_dt(2, 20, 45), shift=_shift())


def test_interval_must_stay_in_shift() -> None:
    with pytest.raises(BookingRuleError):
        validate_booking_interval(starts_at=_dt(2, 15), ends_at=_dt(2, 17), shift=_shift())
    with pytest.raises(BookingRuleError):
        validate_booking_interval(starts_at=_dt(3, 0), ends_at=_dt(3, 5), shift=_shift())


def test_booking_may_end_exactly_at_shift_end() -> None:
    validate_booking_interval(starts_at=_dt(3, 0), ends_at=_dt(3, 2), shift=_shift())


def test_interval_cannot_start_in_the_past_for_manual() -> None:
    now = _dt(2, 21)
    with pytest.raises(BookingRuleError):
        validate_booking_interval(starts_at=_dt(2, 20), ends_at=_dt(2, 23), shift=_shift(), now=now)
    # ceil(21:02) -> 21:05, so 21:05 is allowed.
    validate_booking_interval(
        starts_at=_dt(2, 21, 5), ends_at=_dt(2, 23), shift=_shift(), now=_dt(2, 21, 2)
    )


# --- capacity sweep ---------------------------------------------------------


def test_capacity_exact_sum_is_sufficient() -> None:
    start, end = _dt(2, 20), _dt(2, 22)
    assert capacity_sufficient(
        party_size=6,
        assigned=[(4, start, end), (2, start, end)],
        interval_start=start,
        interval_end=end,
    )


def test_capacity_below_party_size_is_insufficient() -> None:
    start, end = _dt(2, 20), _dt(2, 22)
    assert not capacity_sufficient(
        party_size=5,
        assigned=[(2, start, end), (2, start, end)],
        interval_start=start,
        interval_end=end,
    )


def test_capacity_sweep_detects_a_hole() -> None:
    """A segment ending mid-interval leaves the tail uncovered (§14)."""
    start, end = _dt(2, 20), _dt(2, 23)
    assert not capacity_sufficient(
        party_size=4,
        assigned=[(4, start, _dt(2, 21))],
        interval_start=start,
        interval_end=end,
    )


@given(
    tables=st.lists(st.integers(min_value=1, max_value=10), min_size=1, max_size=6),
    party=st.integers(min_value=1, max_value=60),
)
def test_capacity_property_matches_direct_sum(tables: list[int], party: int) -> None:
    """For full-interval segments the sweep must equal the plain sum (§14)."""
    start, end = _dt(2, 20), _dt(2, 22)
    assigned = [(capacity, start, end) for capacity in tables]
    assert capacity_sufficient(
        party_size=party, assigned=assigned, interval_start=start, interval_end=end
    ) == (party <= sum(tables))


# --- state machine ----------------------------------------------------------


def test_state_machine_transitions() -> None:
    assert can_transition("NEW", "WAITING")
    assert can_transition("NEW", "OPEN")
    assert can_transition("NEW", "CANCELED")
    assert can_transition("WAITING", "OPEN")
    assert can_transition("OPEN", "CLOSED")
    assert not can_transition("OPEN", "CANCELED")
    assert not can_transition("CLOSED", "OPEN")
    assert not can_transition("CANCELED", "OPEN")


def test_allowed_transitions_cover_every_status() -> None:
    assert set(ALLOWED_TRANSITIONS) == {"NEW", "WAITING", "OPEN", "CLOSED", "CANCELED"}


def test_status_timestamp_predicate() -> None:
    assert status_timestamps_consistent(
        status="NEW",
        waiting_at=None,
        opened_at=None,
        closed_at=None,
        canceled_at=None,
        cancellation_reason=None,
    )
    assert not status_timestamps_consistent(
        status="NEW",
        waiting_at=_dt(2, 20),
        opened_at=None,
        closed_at=None,
        canceled_at=None,
        cancellation_reason=None,
    )
    assert status_timestamps_consistent(
        status="CANCELED",
        waiting_at=None,
        opened_at=None,
        closed_at=None,
        canceled_at=_dt(2, 20),
        cancellation_reason="GUEST_CANCELED",
    )
    assert not status_timestamps_consistent(
        status="CANCELED",
        waiting_at=None,
        opened_at=None,
        closed_at=None,
        canceled_at=_dt(2, 20),
        cancellation_reason=None,
    )
