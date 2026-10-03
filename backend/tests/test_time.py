"""5-minute grid helper tests (PROJECT-SPEC §2, §15, §18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.db.time import ceil_to_5_minutes, floor_to_5_minutes, is_on_5_minute_grid

MSK = timezone(timedelta(hours=3))


def _dt(hour: int, minute: int, second: int = 0, microsecond: int = 0) -> datetime:
    return datetime(2026, 10, 3, hour, minute, second, microsecond, tzinfo=MSK)


def test_ceil_examples_from_spec() -> None:
    assert ceil_to_5_minutes(_dt(20, 2)) == _dt(20, 5)
    assert ceil_to_5_minutes(_dt(20, 5, 0)) == _dt(20, 5)
    assert ceil_to_5_minutes(_dt(20, 5, 1)) == _dt(20, 10)


def test_ceil_preserves_timezone() -> None:
    value = ceil_to_5_minutes(_dt(20, 1))
    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(hours=3)


def test_ceil_is_idempotent_on_grid() -> None:
    value = _dt(21, 15)
    assert ceil_to_5_minutes(value) == value


def test_floor_rounds_down() -> None:
    assert floor_to_5_minutes(_dt(20, 9, 59)) == _dt(20, 5)


def test_floor_then_ceil_on_grid_is_stable() -> None:
    value = _dt(20, 5)
    assert floor_to_5_minutes(value) == ceil_to_5_minutes(value) == value


def test_grid_detection() -> None:
    assert is_on_5_minute_grid(_dt(20, 5))
    assert not is_on_5_minute_grid(_dt(20, 5, 1))
    assert not is_on_5_minute_grid(_dt(20, 7))
    assert not is_on_5_minute_grid(datetime(2026, 10, 3, 20, 5, 0, 1, tzinfo=UTC))


def test_ceil_handles_utc() -> None:
    value = datetime(2026, 10, 3, 23, 58, 30, tzinfo=UTC)
    assert ceil_to_5_minutes(value) == datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
