"""Read-only OPEN overlay required by Stage 7 WALK_IN (§17.1)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, BookingLiveTable, TableOccupancy


async def live_busy_intervals(
    session: AsyncSession, venue_id: int, table_id: int, now: datetime
) -> list[tuple[datetime, datetime]]:
    bookings = (
        await session.scalars(
            select(Booking)
            .join(
                BookingLiveTable,
                (BookingLiveTable.booking_id == Booking.id)
                & (BookingLiveTable.venue_id == Booking.venue_id),
            )
            .where(
                Booking.venue_id == venue_id,
                BookingLiveTable.table_id == table_id,
                Booking.status == "OPEN",
                Booking.shift_ends_at > now,
            )
        )
    ).all()
    intervals = []
    for booking in bookings:
        occupancies = (
            await session.scalars(
                select(TableOccupancy).where(
                    TableOccupancy.venue_id == venue_id,
                    TableOccupancy.table_id == table_id,
                    TableOccupancy.is_active.is_(True),
                    TableOccupancy.ends_at > now,
                    TableOccupancy.starts_at < booking.shift_ends_at,
                )
            )
        ).all()
        if any(o.booking_id == booking.id and o.starts_at <= now < o.ends_at for o in occupancies):
            continue
        end = min(
            (o.starts_at for o in occupancies if o.starts_at > now), default=booking.shift_ends_at
        )
        intervals.append((now, end))
    return intervals
