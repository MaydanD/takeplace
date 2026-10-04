"""Public, read-only availability for Stage 6 (PROJECT-SPEC §16, §17, §18).

Availability is a snapshot and does not guarantee that a slot stays free until
``POST /venues/{slug}/bookings``. The final create path re-validates everything
under the canonical booking-core transaction and the DB exclusion constraint.

This module uses only the existing ``ScheduleTable`` / shift resolution /
capacity helpers — it does not re-implement business-date or schedule resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hall, Table, TableOccupancy, Venue
from app.db.time import ceil_to_5_minutes as _ceil_to_5_minutes
from app.db.time import floor_to_5_minutes as _floor_to_5_minutes
from app.domain.booking import MIN_BOOKING_MINUTES, booking_horizon_end
from app.domain.schedule import Shift, current_business_date
from app.domain.timezone import load_timezone
from app.services.live_availability import live_busy_intervals
from app.services.schedule import load_schedule_table

_SLOT_SECONDS = 5 * 60
_MIN_SHIFT_MINUTES = timedelta(minutes=MIN_BOOKING_MINUTES)


# ---------------------------------------------------------------------------
# Public availability model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PublicAvailabilitySlot:
    """One slot option returned to the public frontend."""

    start: str
    earliest_end: str
    latest_end: str
    end_options: list[str]


@dataclass(slots=True)
class PublicAvailabilityTable:
    """A table from the public availability view."""

    id: int
    number: str
    capacity: int
    hall_id: int
    hall_name: str
    slots: list[PublicAvailabilitySlot]


@dataclass(slots=True)
class PublicAvailability:
    """Public availability for one venue / business_date / hall selection."""

    business_date: date_type
    venue_timezone: str
    shift_start: str | None
    shift_end: str | None
    is_open: bool
    tables: list[PublicAvailabilityTable]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def public_availability_key(
    venue_id: int,
    business_date: date_type | str,
    hall_id: int | None = None,
    *,
    table_id: int | None = None,
) -> str:
    """Stable, non-PII key for public availability rate limiting/caching (§40).

    Derived only from non-sensitive identifiers, never from client IP. Safe to
    store in process memory or any shared cache.
    """
    parts = ["pub-availability", str(venue_id), str(business_date), "hall", str(hall_id)]
    if table_id is not None:
        parts.append("table")
        parts.append(str(table_id))
    return ":".join(parts)


def _build_slots(
    shift: Shift,
    now: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
    party_size: int,
    table_capacity: int,
) -> list[PublicAvailabilitySlot]:
    """5-minute grid slots inside one shift, filtered by capacity and busy state.

    A slot start is emitted when:
    * it is on the 5-minute grid;
    * it is >= ``ceil_to_5_minutes(now)`` and inside ``[shift.start, shift.end)``;
    * at least ``MIN_BOOKING_MINUTES`` remain until ``shift.end``;
    * it does not fall inside any busy ``[start, end)`` interval.

    For each valid start, ``earliest_end = start + MIN_BOOKING_MINUTES`` and
    ``latest_end`` is the earliest of ``shift.end`` and the next busy interval
    start, floored to the 5-minute grid.
    """
    if party_size > table_capacity:
        return []

    busy = sorted(busy_intervals)
    first_allowed = max(shift.start, _ceil_to_5_minutes(now))
    slots: list[PublicAvailabilitySlot] = []
    cursor = first_allowed
    while cursor + _MIN_SHIFT_MINUTES <= shift.end:
        # Skip ahead past any busy interval that contains cursor.
        for busy_start, busy_end in busy:
            if busy_start <= cursor < busy_end:
                cursor = _ceil_to_5_minutes(busy_end)
                # Continue through adjacent/overlapping segments before offering
                # a start; this new cursor can still be inside the next segment.
        if cursor + _MIN_SHIFT_MINUTES > shift.end:
            break

        latest_end = shift.end
        for busy_start, _busy_end in busy:
            if busy_start > cursor and busy_start < latest_end:
                latest_end = busy_start
        latest_end = _floor_to_5_minutes(latest_end)
        earliest_end = cursor + _MIN_SHIFT_MINUTES
        if latest_end >= earliest_end:
            slots.append(
                PublicAvailabilitySlot(
                    start=cursor.isoformat(),
                    earliest_end=earliest_end.isoformat(),
                    latest_end=latest_end.isoformat(),
                    end_options=[
                        (earliest_end + timedelta(seconds=offset)).isoformat()
                        for offset in range(
                            0, int((latest_end - earliest_end).total_seconds()) + 1, _SLOT_SECONDS
                        )
                    ],
                )
            )
        cursor = cursor + timedelta(seconds=_SLOT_SECONDS)
    return slots


async def _effective_bookable_tables(
    session: AsyncSession,
    venue_id: int,
    *,
    hall_id: int | None = None,
) -> list[tuple[Table, Hall]]:
    """Active, non-archived, bookable tables for the requested hall selection."""
    query = (
        select(Table, Hall)
        .join(Hall, Hall.id == Table.hall_id)
        .where(
            Table.venue_id == venue_id,
            Table.archived_at.is_(None),
            Table.is_bookable.is_(True),
            Hall.venue_id == venue_id,
            Hall.archived_at.is_(None),
            Hall.is_bookable.is_(True),
        )
        .order_by(Table.id)
    )
    if hall_id is not None:
        query = query.where(Table.hall_id == hall_id)
    rows = (await session.execute(query)).all()
    return [(table, hall) for table, hall in rows]


async def _active_occupancy_intervals(
    session: AsyncSession,
    venue_id: int,
    table_id: int,
    *,
    shift: Shift,
) -> list[tuple[datetime, datetime]]:
    """Active BOOKING/BLOCK segments of one table clipped to the shift window.

    Dynamic online unavailability (§17) is intentionally **not** computed here:
    at Stage 6 ``booking_live_tables`` does not exist and the only overlay is
    the planned occupancies. The seam for §17 is :func:`_online_busy_intervals`,
    which returns an empty list until Stage 8+.
    """
    rows = await session.execute(
        select(TableOccupancy)
        .where(
            TableOccupancy.venue_id == venue_id,
            TableOccupancy.table_id == table_id,
            TableOccupancy.is_active.is_(True),
            TableOccupancy.starts_at < shift.end,
            TableOccupancy.ends_at > shift.start,
        )
        .order_by(TableOccupancy.starts_at)
    )
    intervals: list[tuple[datetime, datetime]] = []
    for occ in rows.scalars().all():
        start = occ.starts_at if occ.starts_at > shift.start else shift.start
        end = occ.ends_at if occ.ends_at < shift.end else shift.end
        if end > start:
            intervals.append((start, end))
    return intervals


async def _online_busy_intervals(
    session: AsyncSession,
    venue_id: int,
    table_id: int,
    *,
    now: datetime,
    shift: Shift,
) -> list[tuple[datetime, datetime]]:
    """Dynamic online unavailability for one table (§17).

    Stage 8+ populates ``booking_live_tables`` and realtime sources; at Stage 6
    the dynamic overlay is empty. The function exists as the seam for §17
    without duplicating domain logic.
    """
    return [
        (max(start, shift.start), min(end, shift.end))
        for start, end in await live_busy_intervals(session, venue_id, table_id, now)
        if start < shift.end and end > shift.start
    ]


# ---------------------------------------------------------------------------
# Service entry point
# ---------------------------------------------------------------------------


async def build_public_availability(
    session: AsyncSession,
    venue: Venue,
    *,
    business_date: date_type,
    hall_id: int | None = None,
    table_id: int | None = None,
    party_size: int = 1,
    now: datetime,
) -> PublicAvailability:
    """Build the public availability snapshot for one venue/date selection.

    Parameters align with ``GET /venues/{slug}/availability`` (§34):

    * ``business_date`` — the calendar business date the guest is looking at;
    * ``hall_id`` — optional hall filter;
    * ``table_id`` — optional single-table filter;
    * ``party_size`` — the guest party size used for capacity pre-filtering;
    * ``now`` — current server instant.
    """
    tz = load_timezone(venue.timezone)
    schedule = await load_schedule_table(session, venue.id)
    shift = schedule.shift_for(business_date, tz)
    if shift is None or business_date > booking_horizon_end(
        current_business_date(now, schedule, tz)
    ):
        return PublicAvailability(
            business_date=business_date,
            venue_timezone=venue.timezone,
            shift_start=None,
            shift_end=None,
            is_open=False,
            tables=[],
        )

    business_date_for_shift = shift.business_date
    tables = await _effective_bookable_tables(session, venue.id, hall_id=hall_id)

    if table_id is not None:
        tables = [(t, h) for t, h in tables if t.id == table_id]
        if not tables:
            return PublicAvailability(
                business_date=business_date_for_shift,
                venue_timezone=venue.timezone,
                shift_start=shift.start.isoformat(),
                shift_end=shift.end.isoformat(),
                is_open=True,
                tables=[],
            )

    tables_out: list[PublicAvailabilityTable] = []
    for table, hall in tables:
        existing = await _active_occupancy_intervals(session, venue.id, table.id, shift=shift)
        online = await _online_busy_intervals(session, venue.id, table.id, now=now, shift=shift)
        slots = _build_slots(
            shift,
            now,
            busy_intervals=existing + online,
            party_size=party_size,
            table_capacity=table.capacity,
        )
        if slots:
            tables_out.append(
                PublicAvailabilityTable(
                    id=table.id,
                    number=table.number,
                    capacity=table.capacity,
                    hall_id=hall.id,
                    hall_name=hall.name,
                    slots=slots,
                )
            )

    return PublicAvailability(
        business_date=business_date_for_shift,
        venue_timezone=venue.timezone,
        shift_start=shift.start.isoformat(),
        shift_end=shift.end.isoformat(),
        is_open=True,
        tables=tables_out,
    )
