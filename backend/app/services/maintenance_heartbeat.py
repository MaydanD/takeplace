"""Durable maintenance heartbeat (PROJECT-SPEC §42.2, §47, §60).

Retention/anonymization must run even when VK delivery is disabled, so the
periodic maintenance pass is decoupled from the VK worker loop (audit FIX-02).
Because the operator CLI and the background worker are separate processes, the
outcome of each pass is published through PostgreSQL — a single fixed row — so
``/health/ops`` can flag a deployment whose retention has silently stopped.

``last_error`` stores only a sanitized exception class name (for example
``OperationalError``); a raw message is never persisted, so no guest data can
leak into this table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: A single fixed row holds the current maintenance heartbeat (one fleet, v1).
_HEARTBEAT_ID = 1


async def mark_maintenance_success(session: AsyncSession, *, now: datetime) -> None:
    """Record a fully committed maintenance pass and clear any prior error."""
    await session.execute(
        text(
            "INSERT INTO maintenance_heartbeat "
            "(id, last_success_at, last_error, last_error_at) "
            "VALUES (:id, :now, NULL, NULL) "
            "ON CONFLICT (id) DO UPDATE SET "
            "last_success_at = EXCLUDED.last_success_at, "
            "last_error = NULL, last_error_at = NULL"
        ),
        {"id": _HEARTBEAT_ID, "now": now},
    )


async def mark_maintenance_failure(session: AsyncSession, *, now: datetime, error: str) -> None:
    """Record a failed pass. ``error`` must be a sanitized class name only."""
    await session.execute(
        text(
            "INSERT INTO maintenance_heartbeat "
            "(id, last_success_at, last_error, last_error_at) "
            "VALUES (:id, NULL, :error, :now) "
            "ON CONFLICT (id) DO UPDATE SET "
            "last_error = EXCLUDED.last_error, last_error_at = EXCLUDED.last_error_at"
        ),
        {"id": _HEARTBEAT_ID, "now": now, "error": error},
    )


async def maintenance_success_age_seconds(session: AsyncSession, *, now: datetime) -> float | None:
    """Age of the last successful pass, or ``None`` if one has never committed."""
    value = await session.scalar(
        text("SELECT last_success_at FROM maintenance_heartbeat WHERE id = :id"),
        {"id": _HEARTBEAT_ID},
    )
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError("maintenance_heartbeat.last_success_at did not return a datetime")
    return (now - value).total_seconds()


async def maintenance_last_error(session: AsyncSession) -> str | None:
    """The sanitized error class name of the most recent failed pass, if any."""
    value = await session.scalar(
        text("SELECT last_error FROM maintenance_heartbeat WHERE id = :id"),
        {"id": _HEARTBEAT_ID},
    )
    return value if isinstance(value, str) else None


__all__ = [
    "maintenance_last_error",
    "maintenance_success_age_seconds",
    "mark_maintenance_failure",
    "mark_maintenance_success",
]
