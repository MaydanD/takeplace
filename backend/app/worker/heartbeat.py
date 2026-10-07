"""Worker heartbeat (PROJECT-SPEC §47, §49).

``/health/ops`` must flag a stale worker heartbeat. Because the API and the worker
are separate processes, the heartbeat is published through PostgreSQL rather than
in-process memory: the worker writes a row to a tiny table and the API reads its
age. This keeps the two processes independent (no shared Python state) while still
letting the API observe worker liveness.

The heartbeat is best-effort: a failure to write it never stops delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: A single fixed row holds the current heartbeat (one worker fleet, v1).
_HEARTBEAT_ID = 1


@dataclass(frozen=True, slots=True)
class WorkerHeartbeat:
    """Records the last successful worker cycle.

    The actual timestamp write is done by the worker's own session
    (:func:`record_heartbeat`); this object only carries the cadence used for the
    in-process log and is the seam tests replace with a fake.
    """

    def beat(self) -> None:
        """Mark a completed cycle. The durable write happens in the loop itself."""
        # Intentionally a no-op here: durability is handled by record_heartbeat(),
        # which the worker calls with its own transaction. Keeping this method
        # allows a fake heartbeat in tests without touching the database.
        return None


async def record_heartbeat(session: AsyncSession, *, now: datetime) -> None:
    """Upsert the single worker heartbeat row (best-effort, §49)."""
    await session.execute(
        text(
            "INSERT INTO worker_heartbeat (id, last_seen_at) VALUES (:id, :now) "
            "ON CONFLICT (id) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at"
        ),
        {"id": _HEARTBEAT_ID, "now": now},
    )


async def heartbeat_age_seconds(session: AsyncSession, *, now: datetime) -> float | None:
    """Return the age of the worker heartbeat, or ``None`` if never seen (§47)."""
    value = await session.scalar(
        text("SELECT last_seen_at FROM worker_heartbeat WHERE id = :id"), {"id": _HEARTBEAT_ID}
    )
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError("worker_heartbeat.last_seen_at did not return a datetime")
    return (now - value).total_seconds()
