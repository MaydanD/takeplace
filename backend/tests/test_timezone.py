"""Timezone validation and DST-capability scanning (PROJECT-SPEC §53)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.timezone import (
    DEFAULT_HORIZON_DAYS,
    UnknownTimezoneError,
    UnsupportedTimezoneError,
    assert_supported_timezone,
    is_offset_stable,
    load_timezone,
    offset_transitions,
)


def test_default_horizon_is_400_days() -> None:
    assert DEFAULT_HORIZON_DAYS == 400


def test_load_known_timezone() -> None:
    assert str(load_timezone("Europe/Moscow")) == "Europe/Moscow"


def test_load_unknown_timezone_raises() -> None:
    with pytest.raises(UnknownTimezoneError):
        load_timezone("Not/AZone")


def test_offset_transitions_raises_for_unknown_zone() -> None:
    with pytest.raises(UnknownTimezoneError):
        offset_transitions("Totally/MadeUp")


def test_stable_zone_has_no_transitions() -> None:
    # Europe/Moscow has had a fixed UTC+3 offset since 2014.
    assert offset_transitions("Europe/Moscow") == []


def test_dst_zone_has_transitions_within_horizon() -> None:
    # Europe/Berlin transitions twice a year, so any 400-day window catches one.
    assert offset_transitions("Europe/Berlin") != []


def test_offset_transitions_find_a_known_spring_forward() -> None:
    # Berlin switches to DST on 2025-03-30; scanning a 60-day window from
    # 2025-03-01 must observe the change.
    start = datetime(2025, 3, 1, tzinfo=UTC)
    transitions = offset_transitions("Europe/Berlin", start=start, horizon_days=60)
    assert transitions, "expected a DST transition"
    assert transitions[0].date() == datetime(2025, 3, 30, tzinfo=UTC).date()


def test_is_offset_stable() -> None:
    assert is_offset_stable("Asia/Yekaterinburg") is True
    assert is_offset_stable("Europe/Berlin") is False


def test_assert_supported_timezone_accepts_stable_zone() -> None:
    assert_supported_timezone("Europe/Moscow")


def test_assert_supported_timezone_rejects_dst_zone() -> None:
    with pytest.raises(UnsupportedTimezoneError):
        assert_supported_timezone("Europe/Berlin")


def test_assert_supported_timezone_rejects_unknown_zone() -> None:
    with pytest.raises(UnknownTimezoneError):
        assert_supported_timezone("Mars/Olympus")
