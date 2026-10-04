"""Grid/near-close WALK_IN rules are independent of client timestamps."""

from datetime import UTC, datetime, timedelta

import pytest
from app.domain.booking import BookingRuleError, walk_in_start
from app.domain.schedule import Shift


@pytest.mark.parametrize(
    "minute,duration,valid",
    [
        (0, 45, True),
        (0, 30, False),
        (90, 30, True),
        (90, 25, False),
        (119, 5, False),
        (60, 60, True),
    ],
)
def test_required_duration(minute, duration, valid):
    start = datetime(2026, 10, 5, 16, tzinfo=UTC)
    shift = Shift(start.date(), start, start + timedelta(hours=2))
    now = start + timedelta(minutes=minute)
    if valid:
        assert walk_in_start(now=now, ends_at=now + timedelta(minutes=duration), shift=shift) == now
    else:
        with pytest.raises(BookingRuleError):
            walk_in_start(now=now, ends_at=now + timedelta(minutes=duration), shift=shift)


def test_walk_in_rounds_server_seconds_and_rejects_outside_shift():
    start = datetime(2026, 10, 5, 16, tzinfo=UTC)
    shift = Shift(start.date(), start, start + timedelta(hours=2))
    assert walk_in_start(
        now=start + timedelta(seconds=1), ends_at=start + timedelta(minutes=50), shift=shift
    ) == start + timedelta(minutes=5)
    with pytest.raises(BookingRuleError):
        walk_in_start(now=shift.end, ends_at=shift.end + timedelta(minutes=5), shift=shift)
