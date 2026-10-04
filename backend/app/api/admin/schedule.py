"""Admin schedule endpoints (PROJECT-SPEC §5, §35).

Every handler is scoped to the venue resolved from the session cookie; there is
no ``venue_id`` parameter, so one tenant cannot read or change another's
schedule. All timezone conversion happens through the domain module against the
venue's own IANA timezone — never the server's.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.admin.dependencies import AuthContextDep, SessionDep, require_trusted_origin
from app.api.admin.schemas import (
    BusinessDayResponse,
    ScheduleDay,
    ScheduleExceptionEntry,
    ScheduleExceptionsResponse,
    ScheduleExceptionUpdate,
    WeeklyScheduleResponse,
    WeeklyScheduleUpdate,
)
from app.api.errors import not_found, schedule_invalid, schedule_overlap
from app.db.time import operation_now
from app.domain.schedule import (
    InvalidScheduleError,
    ScheduleRule,
    current_business_date,
    describe_business_day,
)
from app.domain.timezone import load_timezone
from app.services.errors import ScheduleConflictError
from app.services.schedule import (
    WEEKDAYS,
    delete_exception,
    get_weekly_rules,
    list_exceptions,
    load_schedule_table,
    replace_weekly_schedule,
    upsert_exception,
)

router = APIRouter(prefix="/schedule", tags=["admin-schedule"])


@contextmanager
def _translating_schedule_errors() -> Iterator[None]:
    """Map domain/service schedule errors to stable API error codes."""
    try:
        yield
    except InvalidScheduleError as exc:
        raise schedule_invalid(str(exc)) from exc
    except ScheduleConflictError as exc:
        raise schedule_overlap(str(exc)) from exc


def _parse_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value is not None else None


def _format_time(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _rule_from_day(day: ScheduleDay) -> ScheduleRule:
    return ScheduleRule(
        is_open=day.is_open,
        open_time=_parse_time(day.open_time),
        close_time=_parse_time(day.close_time),
    )


def _weekly_response(rules: dict[int, ScheduleRule], timezone: str) -> WeeklyScheduleResponse:
    weekdays: list[ScheduleDay] = []
    for weekday in WEEKDAYS:
        rule = rules.get(weekday, ScheduleRule.closed())
        weekdays.append(
            ScheduleDay(
                weekday=weekday,
                is_open=rule.is_open,
                open_time=_format_time(rule.open_time),
                close_time=_format_time(rule.close_time),
            )
        )
    return WeeklyScheduleResponse(timezone=timezone, weekdays=weekdays)


@router.get("", response_model=WeeklyScheduleResponse)
async def get_weekly_schedule(
    context: AuthContextDep, session: SessionDep
) -> WeeklyScheduleResponse:
    """Return this venue's weekly schedule (all seven weekdays)."""
    rules = await get_weekly_rules(session, context.venue_id)
    return _weekly_response(rules, context.venue.timezone)


@router.put(
    "",
    response_model=WeeklyScheduleResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def put_weekly_schedule(
    payload: WeeklyScheduleUpdate,
    context: AuthContextDep,
    session: SessionDep,
) -> WeeklyScheduleResponse:
    """Replace the weekly schedule, validating grid rules and adjacent overlaps.

    The request must define every weekday; a shift that crosses midnight is
    expressed as ``close_time < open_time`` (§5.2). A schedule whose adjacent
    shifts would overlap is rejected with ``SCHEDULE_OVERLAP`` (§5.4).
    """
    rules = {day.weekday: _rule_from_day(day) for day in payload.weekdays}
    if len(rules) != len(payload.weekdays):
        raise schedule_invalid("duplicate weekday in weekly schedule")
    tz = load_timezone(context.venue.timezone)
    with _translating_schedule_errors():
        await replace_weekly_schedule(session, context.venue_id, rules, tz)
    await session.commit()
    stored = await get_weekly_rules(session, context.venue_id)
    return _weekly_response(stored, context.venue.timezone)


@router.get("/exceptions", response_model=ScheduleExceptionsResponse)
async def get_exceptions(
    context: AuthContextDep, session: SessionDep
) -> ScheduleExceptionsResponse:
    """Return this venue's date-specific schedule exceptions."""
    rows = await list_exceptions(session, context.venue_id)
    return ScheduleExceptionsResponse(
        timezone=context.venue.timezone,
        exceptions=[
            ScheduleExceptionEntry(
                date=row.date,
                is_closed=row.is_closed,
                open_time=_format_time(row.open_time),
                close_time=_format_time(row.close_time),
            )
            for row in rows
        ],
    )


@router.put(
    "/exceptions/{business_date}",
    response_model=ScheduleExceptionEntry,
    dependencies=[Depends(require_trusted_origin)],
)
async def put_exception(
    business_date: date,
    payload: ScheduleExceptionUpdate,
    context: AuthContextDep,
    session: SessionDep,
) -> ScheduleExceptionEntry:
    """Create or replace the exception for one business date (§5.3)."""
    rule = ScheduleRule(
        is_open=not payload.is_closed,
        open_time=_parse_time(payload.open_time),
        close_time=_parse_time(payload.close_time),
    )
    tz = load_timezone(context.venue.timezone)
    with _translating_schedule_errors():
        row = await upsert_exception(session, context.venue_id, business_date, rule, tz)
    await session.commit()
    return ScheduleExceptionEntry(
        date=row.date,
        is_closed=row.is_closed,
        open_time=_format_time(row.open_time),
        close_time=_format_time(row.close_time),
    )


@router.delete(
    "/exceptions/{business_date}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_trusted_origin)],
)
async def remove_exception(
    business_date: date,
    context: AuthContextDep,
    session: SessionDep,
) -> Response:
    """Delete the exception for one date, reverting to the weekly rule (§5.3)."""
    tz = load_timezone(context.venue.timezone)
    with _translating_schedule_errors():
        removed = await delete_exception(session, context.venue_id, business_date, tz)
    if not removed:
        raise not_found("SCHEDULE_EXCEPTION_NOT_FOUND", "no exception exists for that date")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/business-day", response_model=BusinessDayResponse)
async def get_business_day(
    context: AuthContextDep,
    session: SessionDep,
    business_date: Annotated[date | None, Query()] = None,
) -> BusinessDayResponse:
    """Return the computed state for a business date (§5.1, §5.6).

    With no ``business_date`` the *current* business date is used — which may be
    yesterday's date while an overnight shift is still running.
    """
    table = await load_schedule_table(session, context.venue_id)
    tz = load_timezone(context.venue.timezone)
    now = await operation_now(session)
    target = business_date if business_date is not None else current_business_date(now, table, tz)
    state = describe_business_day(table, tz, now, target)
    return BusinessDayResponse(
        business_date=state.business_date,
        timezone=context.venue.timezone,
        is_open=state.is_open,
        is_open_now=state.is_open_now,
        current_business_date=state.current_business_date,
        shift_start=state.shift_start,
        shift_end=state.shift_end,
    )
