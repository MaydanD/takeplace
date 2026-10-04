"""Rolling timezone capability check (PROJECT-SPEC §47, §53).

A single helper scans every active venue's timezone over the rolling horizon.
The result feeds ``/health/ops`` so an upcoming UTC-offset transition is alerted
long before it enters the booking horizon. The scan runs at startup and then
periodically (at least daily), not on every health request.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Venue
from app.domain.timezone import UnknownTimezoneError, offset_transitions

logger = structlog.get_logger("takeplace.timezone")

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass(slots=True)
class TimezoneCapability:
    """Snapshot of the last capability scan."""

    checked: bool = False
    unsupported: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error is not None:
            return "unsupported"
        return "unsupported" if self.unsupported else "ok"


class TimezoneCapabilityMonitor:
    """Scans the timezones of active venues on a rolling horizon."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        horizon_days: int,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._horizon_days = horizon_days
        self._interval = interval_seconds
        self._snapshot = TimezoneCapability()

    def snapshot(self) -> TimezoneCapability:
        return self._snapshot

    async def run_once(self) -> TimezoneCapability:
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(Venue.timezone).where(Venue.is_active.is_(True)).distinct()
                )
                zones = [row[0] for row in result.all()]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("timezone_capability_db_error", error=str(exc))
            self._snapshot = TimezoneCapability(checked=True, error=str(exc))
            return self._snapshot

        unsupported: list[str] = []
        for zone in zones:
            try:
                if offset_transitions(zone, horizon_days=self._horizon_days):
                    unsupported.append(zone)
            except UnknownTimezoneError:
                unsupported.append(zone)

        self._snapshot = TimezoneCapability(checked=True, unsupported=unsupported)
        if unsupported:
            logger.warning("timezone_capability_unsupported", zones=unsupported)
        return self._snapshot

    async def run_forever(self) -> None:
        """Run once immediately, then every ``interval`` seconds until cancelled."""
        while True:
            await self.run_once()
            await asyncio.sleep(self._interval)


_monitor: TimezoneCapabilityMonitor | None = None


def init_monitor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    horizon_days: int,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> TimezoneCapabilityMonitor:
    global _monitor
    if _monitor is None:
        _monitor = TimezoneCapabilityMonitor(
            session_factory, horizon_days=horizon_days, interval_seconds=interval_seconds
        )
    return _monitor


def get_monitor() -> TimezoneCapabilityMonitor | None:
    return _monitor


def reset_monitor() -> None:
    global _monitor
    _monitor = None
