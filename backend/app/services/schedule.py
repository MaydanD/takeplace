"""Schedule services: weekly CRUD and date exceptions (PROJECT-SPEC §5, §32.3).

Every mutation takes the exclusive per-venue *schedule* advisory lock before it
validates and writes, so it cannot interleave with a booking create that holds
the shared schedule lock (§32.3). The overlap validation itself lives in the
domain module and is called here, never reimplemented inline.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as date_type
from datetime import tzinfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.locks import acquire_schedule_lock
from app.db.models import ScheduleException, WeeklySchedule
from app.db.time import operation_now
from app.domain.schedule import (
    InvalidScheduleError,
    ScheduleRule,
    ScheduleTable,
    ShiftConflict,
    find_adjacent_overlaps,
    find_weekly_overlaps,
    validate_rule,
    validate_weekday,
)
from app.services.errors import ScheduleConflictError

WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)


def _rule_from_row(row: WeeklySchedule) -> ScheduleRule:
    return ScheduleRule(is_open=row.is_open, open_time=row.open_time, close_time=row.close_time)


def _exception_rule(row: ScheduleException) -> ScheduleRule:
    # is_closed == not is_open; times are stored identically.
    return ScheduleRule(
        is_open=not row.is_closed, open_time=row.open_time, close_time=row.close_time
    )


def _conflict_message(conflicts: list[ShiftConflict]) -> str:
    first = conflicts[0]
    return (
        "adjacent shifts overlap: "
        f"{first.earlier.business_date.isoformat()} "
        f"[{first.earlier.start.isoformat()} - {first.earlier.end.isoformat()}] "
        f"vs {first.later.business_date.isoformat()} "
        f"[{first.later.start.isoformat()} - {first.later.end.isoformat()}]"
    )


async def get_weekly_rules(session: AsyncSession, venue_id: int) -> dict[int, ScheduleRule]:
    """Return the stored weekly rules keyed by weekday (missing = closed)."""
    result = await session.execute(
        select(WeeklySchedule).where(WeeklySchedule.venue_id == venue_id)
    )
    return {row.weekday: _rule_from_row(row) for row in result.scalars().all()}


async def load_schedule_table(session: AsyncSession, venue_id: int) -> ScheduleTable:
    """Load the full effective schedule (weekly rows + exceptions)."""
    weekly = await get_weekly_rules(session, venue_id)
    result = await session.execute(
        select(ScheduleException).where(ScheduleException.venue_id == venue_id)
    )
    exceptions = {row.date: _exception_rule(row) for row in result.scalars().all()}
    return ScheduleTable(weekly=weekly, exceptions=exceptions)


async def list_exceptions(session: AsyncSession, venue_id: int) -> list[ScheduleException]:
    """Return a venue's exceptions ordered by date."""
    result = await session.execute(
        select(ScheduleException)
        .where(ScheduleException.venue_id == venue_id)
        .order_by(ScheduleException.date)
    )
    return list(result.scalars().all())


async def seed_default_schedule(session: AsyncSession, venue_id: int) -> None:
    """Seed seven *closed* weekly rows for a freshly created venue (§53).

    A new venue starts with every day closed; the operator opens the days it
    actually works. Missing rows would also resolve to closed, so this only
    makes the base schedule explicit and editable from the first request.
    """
    now = await operation_now(session)
    for weekday in WEEKDAYS:
        session.add(
            WeeklySchedule(
                venue_id=venue_id,
                weekday=weekday,
                is_open=False,
                open_time=None,
                close_time=None,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()


async def replace_weekly_schedule(
    session: AsyncSession,
    venue_id: int,
    rules: Mapping[int, ScheduleRule],
    tz: tzinfo,
) -> dict[int, ScheduleRule]:
    """Replace the whole weekly schedule after validating the grid and overlaps.

    The submitted set must cover all seven weekdays so the schedule is never
    left partially defined. Adjacent weekday shifts must not overlap (§5.4).
    """
    normalized: dict[int, ScheduleRule] = {}
    for weekday, rule in rules.items():
        validate_weekday(weekday)
        validate_rule(rule)
        normalized[weekday] = rule
    missing = sorted(set(WEEKDAYS) - set(normalized))
    if missing:
        raise InvalidScheduleError(f"weekly schedule must define every weekday; missing {missing}")

    conflicts = find_weekly_overlaps(normalized, tz)
    if conflicts:
        raise ScheduleConflictError(_conflict_message(conflicts))

    await acquire_schedule_lock(session, venue_id)
    now = await operation_now(session)

    existing = {
        row.weekday: row
        for row in (
            await session.execute(select(WeeklySchedule).where(WeeklySchedule.venue_id == venue_id))
        )
        .scalars()
        .all()
    }
    for weekday in WEEKDAYS:
        rule = normalized[weekday]
        row = existing.get(weekday)
        if row is None:
            session.add(
                WeeklySchedule(
                    venue_id=venue_id,
                    weekday=weekday,
                    is_open=rule.is_open,
                    open_time=rule.open_time,
                    close_time=rule.close_time,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.is_open = rule.is_open
            row.open_time = rule.open_time
            row.close_time = rule.close_time
            row.updated_at = now
    await session.flush()
    return normalized


async def upsert_exception(
    session: AsyncSession,
    venue_id: int,
    business_date: date_type,
    rule: ScheduleRule,
    tz: tzinfo,
) -> ScheduleException:
    """Create or replace the exception for one business date (§5.3, §5.4).

    An exception fully overrides the weekly rule and is validated against the
    previous and the next business date using the effective schedule.
    """
    validate_rule(rule)
    await acquire_schedule_lock(session, venue_id)

    table = await load_schedule_table(session, venue_id)
    candidate = table.with_exception(business_date, rule)
    conflicts = find_adjacent_overlaps(candidate, business_date, tz)
    if conflicts:
        raise ScheduleConflictError(_conflict_message(conflicts))

    now = await operation_now(session)
    result = await session.execute(
        select(ScheduleException).where(
            ScheduleException.venue_id == venue_id, ScheduleException.date == business_date
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ScheduleException(
            venue_id=venue_id,
            date=business_date,
            is_closed=not rule.is_open,
            open_time=rule.open_time,
            close_time=rule.close_time,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.is_closed = not rule.is_open
        row.open_time = rule.open_time
        row.close_time = rule.close_time
        row.updated_at = now
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - defensive; validated above
        raise ScheduleConflictError("failed to store schedule exception") from exc
    return row


async def delete_exception(
    session: AsyncSession, venue_id: int, business_date: date_type, tz: tzinfo
) -> bool:
    """Delete the exception for one date, reverting to the weekly rule (§5.3).

    The revert is validated too: removing an exception can re-expose an overlap
    between the weekly rule and an adjacent exception. Returns whether a row was
    removed.
    """
    await acquire_schedule_lock(session, venue_id)
    table = await load_schedule_table(session, venue_id)
    if business_date not in table.exceptions:
        return False

    candidate = table.without_exception(business_date)
    conflicts = find_adjacent_overlaps(candidate, business_date, tz)
    if conflicts:
        raise ScheduleConflictError(_conflict_message(conflicts))

    await session.execute(
        delete(ScheduleException).where(
            ScheduleException.venue_id == venue_id, ScheduleException.date == business_date
        )
    )
    return True
