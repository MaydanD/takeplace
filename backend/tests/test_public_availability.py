"""Unit tests for the public availability service (PROJECT-SPEC §16, §17, §34)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.schedule import Shift
from app.services.public_availability import (
    PublicAvailability,
    PublicAvailabilitySlot,
    _build_slots,
    _ceil_to_5_minutes,
    _floor_to_5_minutes,
    public_availability_key,
)

MSK = timezone(timedelta(hours=3))


# --- grid helpers -----------------------------------------------------------


def test_ceil_to_5_minutes_exact() -> None:
    dt = datetime(2026, 10, 5, 20, 0, tzinfo=MSK)
    assert _ceil_to_5_minutes(dt) == dt


def test_ceil_to_5_minutes_rounds_up() -> None:
    dt = datetime(2026, 10, 5, 20, 3, tzinfo=MSK)
    assert _ceil_to_5_minutes(dt) == datetime(2026, 10, 5, 20, 5, tzinfo=MSK)


def test_ceil_to_5_minutes_already_on_grid() -> None:
    dt = datetime(2026, 10, 5, 20, 15, tzinfo=MSK)
    assert _ceil_to_5_minutes(dt) == dt


def test_floor_to_5_minutes_exact() -> None:
    dt = datetime(2026, 10, 5, 20, 10, tzinfo=MSK)
    assert _floor_to_5_minutes(dt) == dt


def test_floor_to_5_minutes_rounds_down() -> None:
    dt = datetime(2026, 10, 5, 20, 7, tzinfo=MSK)
    assert _floor_to_5_minutes(dt) == datetime(2026, 10, 5, 20, 5, tzinfo=MSK)


# --- public_availability_key ------------------------------------------------


def test_availability_key_is_stable() -> None:
    k1 = public_availability_key(1, "2026-10-05", hall_id=1, table_id=2)
    k2 = public_availability_key(1, "2026-10-05", hall_id=1, table_id=2)
    assert k1 == k2


def test_availability_key_differs_by_venue() -> None:
    k1 = public_availability_key(1, "2026-10-05", hall_id=1)
    k2 = public_availability_key(2, "2026-10-05", hall_id=1)
    assert k1 != k2


def test_availability_key_differs_by_date() -> None:
    k1 = public_availability_key(1, "2026-10-05", hall_id=1)
    k2 = public_availability_key(1, "2026-10-06", hall_id=1)
    assert k1 != k2


def test_availability_key_with_and_without_table() -> None:
    k1 = public_availability_key(1, "2026-10-05", hall_id=1)
    k2 = public_availability_key(1, "2026-10-05", hall_id=1, table_id=5)
    assert k1 != k2


def test_availability_key_no_pii() -> None:
    key = public_availability_key(42, "2026-10-05", hall_id=3, table_id=7)
    assert "42" in key
    assert "2026-10-05" in key
    assert "3" in key
    assert "7" in key


# --- _build_slots -----------------------------------------------------------


def _shift(start_hour: int = 16, end_hour: int = 23) -> Shift:
    return Shift(
        business_date=datetime(2026, 10, 5).date(),
        start=datetime(2026, 10, 5, start_hour, 0, tzinfo=MSK),
        end=datetime(2026, 10, 5, end_hour, 0, tzinfo=MSK),
    )


def test_build_slots_empty_when_party_exceeds_capacity() -> None:
    shift = _shift()
    now = datetime(2026, 10, 5, 15, 0, tzinfo=MSK)
    slots = _build_slots(shift, now, [], party_size=5, table_capacity=4)
    assert slots == []


def test_build_slots_emits_grid_starts() -> None:
    shift = _shift(16, 18)
    now = datetime(2026, 10, 5, 15, 0, tzinfo=MSK)
    slots = _build_slots(shift, now, [], party_size=2, table_capacity=4)
    assert len(slots) > 0
    for slot in slots:
        start = datetime.fromisoformat(slot.start)
        assert start.minute % 5 == 0


def test_build_slots_respects_minimum_duration() -> None:
    shift = _shift(16, 16)
    now = datetime(2026, 10, 5, 15, 0, tzinfo=MSK)
    slots = _build_slots(shift, now, [], party_size=2, table_capacity=4)
    assert slots == []


def test_build_slots_skips_busy_intervals() -> None:
    shift = _shift(16, 18)
    now = datetime(2026, 10, 5, 15, 0, tzinfo=MSK)
    busy = [(datetime(2026, 10, 5, 16, 0, tzinfo=MSK), datetime(2026, 10, 5, 16, 30, tzinfo=MSK))]
    slots = _build_slots(shift, now, busy, party_size=2, table_capacity=4)
    for slot in slots:
        start = datetime.fromisoformat(slot.start)
        assert start >= datetime(2026, 10, 5, 16, 30, tzinfo=MSK)


def test_build_slots_earliest_end_is_start_plus_min_duration() -> None:
    shift = _shift(16, 18)
    now = datetime(2026, 10, 5, 15, 0, tzinfo=MSK)
    slots = _build_slots(shift, now, [], party_size=2, table_capacity=4)
    assert len(slots) > 0
    for slot in slots:
        start = datetime.fromisoformat(slot.start)
        earliest = datetime.fromisoformat(slot.earliest_end)
        assert (earliest - start).total_seconds() >= 30 * 60


def test_build_slots_latest_end_does_not_exceed_shift_end() -> None:
    shift = _shift(16, 18)
    now = datetime(2026, 10, 5, 15, 0, tzinfo=MSK)
    slots = _build_slots(shift, now, [], party_size=2, table_capacity=4)
    shift_end = shift.end
    for slot in slots:
        latest = datetime.fromisoformat(slot.latest_end)
        assert latest <= shift_end


def test_build_slots_latest_end_stops_before_next_busy() -> None:
    shift = _shift(16, 18)
    now = datetime(2026, 10, 5, 15, 0, tzinfo=MSK)
    busy_start = datetime(2026, 10, 5, 17, 0, tzinfo=MSK)
    busy_end = datetime(2026, 10, 5, 17, 30, tzinfo=MSK)
    slots = _build_slots(shift, now, [(busy_start, busy_end)], party_size=2, table_capacity=4)
    for slot in slots:
        start = datetime.fromisoformat(slot.start)
        latest = datetime.fromisoformat(slot.latest_end)
        if start < busy_start:
            assert latest <= busy_start


def test_build_slots_starts_at_or_after_now() -> None:
    shift = _shift(16, 18)
    now = datetime(2026, 10, 5, 16, 12, tzinfo=MSK)
    slots = _build_slots(shift, now, [], party_size=2, table_capacity=4)
    for slot in slots:
        start = datetime.fromisoformat(slot.start)
        assert start >= now


# --- dataclasses ------------------------------------------------------------


def test_public_availability_dataclass() -> None:
    avail = PublicAvailability(
        business_date=datetime(2026, 10, 5).date(),
        venue_timezone="Europe/Moscow",
        shift_start="2026-10-05T16:00:00+03:00",
        shift_end="2026-10-05T23:00:00+03:00",
        is_open=True,
        tables=[],
    )
    assert avail.is_open is True
    assert avail.tables == []


def test_public_availability_slot_dataclass() -> None:
    slot = PublicAvailabilitySlot(
        start="2026-10-05T16:00:00+03:00",
        earliest_end="2026-10-05T16:30:00+03:00",
        latest_end="2026-10-05T23:00:00+03:00",
        end_options=[],
    )
    assert slot.start == "2026-10-05T16:00:00+03:00"


def test_fractional_now_never_offers_a_past_start() -> None:
    now = datetime(2026, 10, 5, 20, 0, 0, 1, tzinfo=MSK)
    assert _ceil_to_5_minutes(now) == datetime(2026, 10, 5, 20, 5, tzinfo=MSK)


def test_end_options_are_backend_grid_within_free_interval() -> None:
    shift = Shift(
        business_date=datetime(2026, 10, 5).date(),
        start=datetime(2026, 10, 5, 20, tzinfo=MSK),
        end=datetime(2026, 10, 5, 21, tzinfo=MSK),
    )
    slots = _build_slots(shift, shift.start, [], 4, 4)
    assert slots[0].end_options == [
        datetime(2026, 10, 5, 20, 45, tzinfo=MSK).isoformat(),
        datetime(2026, 10, 5, 20, 50, tzinfo=MSK).isoformat(),
        datetime(2026, 10, 5, 20, 55, tzinfo=MSK).isoformat(),
        shift.end.isoformat(),
    ]


def _at(hour: int) -> datetime:
    return datetime(2026, 10, 5, hour, tzinfo=MSK)


def test_adjacent_and_overlapping_busy_segments_never_offer_occupied_start() -> None:
    shift = _shift(16, 23)
    busy = [(_at(16), _at(17)), (_at(17), _at(18)), (_at(17), _at(19))]
    slots = _build_slots(shift, _at(15), busy, 2, 4)
    assert slots
    assert all(datetime.fromisoformat(slot.start) >= _at(19) for slot in slots)
