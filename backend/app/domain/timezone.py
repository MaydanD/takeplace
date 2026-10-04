"""Venue timezone validation and DST-capability scanning (PROJECT-SPEC §53).

Production v1 supports only IANA timezones without UTC-offset transitions in
the rolling capability horizon. A single helper answers both questions:

* is the name a real IANA timezone?
* does its UTC offset stay constant across the horizon?

The same helper backs ``create-venue`` (reject unsupported zones) and the
periodic capability check that feeds ``/health/ops``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Rolling horizon mandated by the spec.
DEFAULT_HORIZON_DAYS = 400
_SCAN_STEP = timedelta(hours=1)


class UnknownTimezoneError(ValueError):
    """Raised when a timezone name is not a known IANA zone."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unknown IANA timezone: {name!r}")
        self.name = name


class UnsupportedTimezoneError(ValueError):
    """Raised when a timezone has offset transitions within the horizon."""

    def __init__(self, name: str, transition: datetime) -> None:
        super().__init__(
            f"timezone {name!r} has a UTC-offset transition at "
            f"{transition.isoformat()} within the supported horizon"
        )
        self.name = name
        self.transition = transition


def load_timezone(name: str) -> ZoneInfo:
    """Return the ``ZoneInfo`` for ``name`` or raise ``UnknownTimezoneError``."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownTimezoneError(name) from exc


def offset_transitions(
    name: str,
    *,
    start: datetime | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[datetime]:
    """Return UTC instants in the next ``horizon_days`` where the offset changes.

    Scans UTC hour by hour, as specified. An empty list means the zone is safe
    (no DST transitions) for the whole horizon.
    """
    tz = load_timezone(name)
    now = (start or datetime.now(UTC)).astimezone(UTC)
    end = now + timedelta(days=horizon_days)

    transitions: list[datetime] = []
    current = now
    previous_offset = current.astimezone(tz).utcoffset()
    while current < end:
        current += _SCAN_STEP
        offset = current.astimezone(tz).utcoffset()
        if offset != previous_offset:
            transitions.append(current)
            previous_offset = offset
    return transitions


def is_offset_stable(
    name: str,
    *,
    start: datetime | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> bool:
    """Return whether the zone keeps a constant UTC offset over the horizon."""
    return not offset_transitions(name, start=start, horizon_days=horizon_days)


def assert_supported_timezone(name: str, *, horizon_days: int = DEFAULT_HORIZON_DAYS) -> ZoneInfo:
    """Validate a timezone for a production venue.

    Raises:
        UnknownTimezoneError: the name is not an IANA zone.
        UnsupportedTimezoneError: the zone has a transition within the horizon.
    """
    tz = load_timezone(name)
    transitions = offset_transitions(name, horizon_days=horizon_days)
    if transitions:
        raise UnsupportedTimezoneError(name, transitions[0])
    return tz
