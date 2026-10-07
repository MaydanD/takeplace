"""Periodic maintenance orchestration (PROJECT-SPEC §42.2, §60).

Binds the retention housekeeping (:mod:`app.services.privacy`) and the read-only
invariant audit (:mod:`app.services.invariants`) into one bounded pass that the
worker runs on a timer and operators can run on demand through the CLI.

A pass never raises on a *clean* database and never mutates business history; its
only writes are anonymization and technical cleanup. The caller owns the
transaction boundary so a failure rolls back the whole pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.invariants import InvariantReport, run_invariant_audit
from app.services.privacy import RetentionReport, run_retention
from app.settings import Settings


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """One maintenance pass: what was cleaned and what the audit observed."""

    retention: RetentionReport
    audit: InvariantReport


async def run_maintenance(
    session: AsyncSession, *, now: datetime, settings: Settings, venue_id: int | None = None
) -> MaintenanceReport:
    """Run retention housekeeping followed by the invariant audit."""
    retention = await run_retention(
        session,
        now=now,
        pii_retention_days=settings.booking_pii_retention_days,
        outbox_retention_days=settings.outbox_retention_days,
        batch_size=settings.maintenance_batch_size,
        venue_id=venue_id,
    )
    stuck_before = now - timedelta(seconds=settings.maintenance_stuck_lease_seconds)
    audit = await run_invariant_audit(session, now=now, stuck_before=stuck_before)
    return MaintenanceReport(retention=retention, audit=audit)


__all__ = ["MaintenanceReport", "run_maintenance"]
