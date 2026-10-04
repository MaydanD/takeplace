"""PostgreSQL advisory-lock helpers (PROJECT-SPEC §32.3).

Schedule consistency is serialised across processes with a transaction-level
advisory lock keyed by ``venue_id``. The key is a stable 64-bit signed digest of
``takeplace:{namespace}:{venue_id}``; ``venue_id`` is never cast to ``int4``, so
large ids cannot collide through truncation.

There is exactly one key builder for the whole application: booking
create/reschedule takes the *shared* schedule lock, while schedule and exception
changes take the *exclusive* one, and both must derive the same key.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Distinct advisory namespaces prevent unrelated guards from interfering.
SCHEDULE_NAMESPACE = "schedule"
LAYOUT_NAMESPACE = "layout"

_KEY_PREFIX = "takeplace"
_DIGEST_BYTES = 8  # blake2b -> a single signed 64-bit bigint


def advisory_key(namespace: str, venue_id: int) -> int:
    """Return the stable signed 64-bit advisory key for ``venue_id``.

    BLAKE2b-64 is used instead of Python's ``hash`` because ``hash`` is salted
    per process and would not be stable across API workers.
    """
    payload = f"{_KEY_PREFIX}:{namespace}:{venue_id}".encode()
    digest = hashlib.blake2b(payload, digest_size=_DIGEST_BYTES).digest()
    return int.from_bytes(digest, "big", signed=True)


async def acquire_schedule_lock(session: AsyncSession, venue_id: int) -> None:
    """Take the exclusive schedule advisory lock for this transaction.

    Called by weekly-schedule and exception mutations before validating and
    writing, so an overlapping booking create cannot slip between the overlap
    check and the commit.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": advisory_key(SCHEDULE_NAMESPACE, venue_id)},
    )


async def acquire_schedule_lock_shared(session: AsyncSession, venue_id: int) -> None:
    """Take the shared schedule advisory lock for this transaction.

    Booking create/reschedule uses the shared variant so many bookings can
    resolve a shift concurrently while a schedule change is blocked.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(:key)"),
        {"key": advisory_key(SCHEDULE_NAMESPACE, venue_id)},
    )


async def acquire_layout_lock(session: AsyncSession, venue_id: int) -> None:
    """Take the exclusive layout advisory lock for this transaction (§31)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": advisory_key(LAYOUT_NAMESPACE, venue_id)},
    )
