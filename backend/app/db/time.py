"""Server-side operation time.

PROJECT-SPEC §4.2: client time is never the source of truth, and
``transaction_timestamp()`` must not be used as business mutation time because
it is fixed at transaction start and can go stale while a request waits for a
row lock.

Instead, a mutation acquires all business-critical locks first and then reads
``clock_timestamp()`` exactly once. That value is ``operation_now`` for the
whole mutation. Stage 1 provides the single helper every later service must use,
so the rule cannot drift into per-endpoint reimplementations.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Rounding grid for scheduled times (PROJECT-SPEC §2).
SLOT_MINUTES = 5
_SLOT_SECONDS = SLOT_MINUTES * 60


async def operation_now(session: AsyncSession) -> datetime:
    """Return ``clock_timestamp()`` from PostgreSQL.

    Call this *after* all business-critical locks for a mutation have been
    acquired, and use the returned value for the rest of that mutation.
    """
    result = await session.execute(text("SELECT clock_timestamp()"))
    value: object = result.scalar_one()
    if not isinstance(value, datetime):
        raise TypeError("clock_timestamp() did not return a datetime")
    return value


def floor_to_5_minutes(value: datetime) -> datetime:
    """Floor a timestamp to the 5-minute scheduling grid."""
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % _SLOT_SECONDS), tz=value.tzinfo)


def ceil_to_5_minutes(value: datetime) -> datetime:
    """Ceil a timestamp to the 5-minute scheduling grid.

    ``20:05:00`` stays at ``20:05``; ``20:05:01`` becomes ``20:10``.
    """
    floored = floor_to_5_minutes(value)
    if floored == value:
        return floored
    return datetime.fromtimestamp(int(floored.timestamp()) + _SLOT_SECONDS, tz=value.tzinfo)


def is_on_5_minute_grid(value: datetime) -> bool:
    """Return whether ``value`` sits exactly on the 5-minute grid (0 seconds)."""
    epoch = int(value.timestamp())
    return epoch % _SLOT_SECONDS == 0 and value.microsecond == 0
