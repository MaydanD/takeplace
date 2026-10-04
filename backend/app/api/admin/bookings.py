"""Admin booking endpoints (PROJECT-SPEC §9, §11, §18.2, §19, §32, §35).

This is the booking-core subset of the admin API for Stage 5: create (manual,
idempotent), list, single read, append-only history, cancel and change-time.
The lifecycle commands (wait/open/undo-open/close), table add/remove/replace and
the public booking flow are later stages and are deliberately absent.

Everything is scoped to the venue resolved from the session cookie; no endpoint
accepts a ``venue_id`` (§7.1).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.dependencies import (
    AuthContextDep,
    SessionDep,
    SettingsDep,
    require_trusted_origin,
)
from app.api.admin.schemas import (
    BookingCancel,
    BookingChangeTime,
    BookingCreate,
    BookingEventSummary,
    BookingGuestEdit,
    BookingHistoryResponse,
    BookingListResponse,
    BookingSummary,
)
from app.api.errors import (
    ApiError,
    booking_conflict,
    booking_invalid_state,
    booking_rule_violation,
    booking_stale,
    hall_not_bookable,
    idempotency_key_reused,
    not_found,
    service_unavailable,
    table_not_bookable,
)
from app.services.bookings import (
    AdminBookingInput,
    BookingView,
    cancel_booking,
    change_booking_time,
    create_admin_booking,
    edit_booking_guest,
    get_booking,
    list_bookings,
    list_events,
    view_of,
)
from app.services.errors import (
    BookingConflictError,
    BookingInvalidStateError,
    BookingNotFoundError,
    BookingRuleViolationError,
    BookingStaleError,
    HallNotBookableError,
    IdempotencyKeyReusedError,
    ServiceUnavailableError,
    TableLiveConflictError,
    TableNotBookableError,
    TableNotFoundError,
)

router = APIRouter(prefix="/bookings", tags=["admin-bookings"])


@contextmanager
def _translating_errors() -> Iterator[None]:
    """Map booking service errors to stable API error codes (§36)."""
    try:
        yield
    except BookingNotFoundError as exc:
        raise not_found("BOOKING_NOT_FOUND", str(exc)) from exc
    except TableNotFoundError as exc:
        raise not_found("TABLE_NOT_FOUND", str(exc)) from exc
    except BookingRuleViolationError as exc:
        raise booking_rule_violation(str(exc)) from exc
    except BookingConflictError as exc:
        raise booking_conflict(str(exc), exc.conflicting_booking_ids) from exc
    except TableLiveConflictError as exc:
        raise ApiError(409, "TABLE_LIVE_CONFLICT", str(exc)) from exc
    except BookingStaleError as exc:
        raise booking_stale(str(exc)) from exc
    except BookingInvalidStateError as exc:
        raise booking_invalid_state(str(exc)) from exc
    except TableNotBookableError as exc:
        raise table_not_bookable(str(exc)) from exc
    except HallNotBookableError as exc:
        raise hall_not_bookable(str(exc)) from exc
    except IdempotencyKeyReusedError as exc:
        raise idempotency_key_reused(str(exc)) from exc
    except ServiceUnavailableError as exc:
        raise service_unavailable(str(exc)) from exc


def _input(payload: BookingCreate) -> AdminBookingInput:
    return AdminBookingInput(
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        table_ids=list(payload.table_ids),
        party_size=payload.party_size,
        source=payload.source,
        guest_name=payload.guest_name,
        guest_phone_raw=payload.guest_phone_raw,
        guest_comment=payload.guest_comment,
        open_immediately=payload.open_immediately,
    )


@router.post(
    "",
    response_model=BookingSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def post_booking(
    payload: BookingCreate,
    response: Response,
    context: AuthContextDep,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> BookingSummary:
    """Create a manual booking idempotently (§19, §32.2).

    A fresh booking is ``201``; a replay of the same key+payload is ``200`` with
    the same booking identity and no new side effects (§36).
    """
    with _translating_errors():
        view, created = await create_admin_booking(
            session,
            venue_id=context.venue_id,
            data=_input(payload),
            idempotency_key=idempotency_key,
            hmac_key=settings.idempotency_hmac_key,
            admin_session_id=context.session.id,
        )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return BookingSummary.from_view(view)


@router.get("", response_model=BookingListResponse)
async def get_bookings(
    context: AuthContextDep,
    session: SessionDep,
    business_date: Annotated[date | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    source: Annotated[str | None, Query()] = None,
    table_id: Annotated[int | None, Query()] = None,
    phone: Annotated[str | None, Query()] = None,
    guest_phone_normalized: Annotated[str | None, Query(max_length=50)] = None,
    number: Annotated[int | None, Query(ge=1)] = None,
    same_network_as: Annotated[int | None, Query(ge=1)] = None,
    unresolved: bool = False,
    cursor: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BookingListResponse:
    """Cursor/limit list with the §35 filters (newest first)."""
    with _translating_errors():
        views, next_cursor = await list_bookings(
            session,
            context.venue_id,
            business_date=business_date,
            status=status_filter,
            source=source,
            table_id=table_id,
            phone=guest_phone_normalized if guest_phone_normalized is not None else phone,
            number=number,
            same_network_as=same_network_as,
            unresolved=unresolved,
            cursor=cursor,
            limit=limit,
        )
    return BookingListResponse(
        items=[BookingSummary.from_view(view) for view in views], next_cursor=next_cursor
    )


@router.get("/unresolved", response_model=BookingListResponse)
async def get_unresolved(
    context: AuthContextDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[int | None, Query()] = None,
) -> BookingListResponse:
    """NEW/WAITING bookings whose planned interval has already ended (§17.3)."""
    views, next_cursor = await list_bookings(
        session, context.venue_id, unresolved=True, cursor=cursor, limit=limit
    )
    return BookingListResponse(
        items=[BookingSummary.from_view(view) for view in views], next_cursor=next_cursor
    )


async def _view(session: AsyncSession, venue_id: int, booking_id: int) -> BookingView:
    return await view_of(session, await get_booking(session, venue_id, booking_id))


@router.patch(
    "/{booking_id}", response_model=BookingSummary, dependencies=[Depends(require_trusted_origin)]
)
async def patch_booking(
    booking_id: int, payload: BookingGuestEdit, context: AuthContextDep, session: SessionDep
) -> BookingSummary:
    with _translating_errors():
        view = await edit_booking_guest(
            session,
            venue_id=context.venue_id,
            booking_id=booking_id,
            expected_version=payload.expected_version,
            changes=payload.model_dump(exclude_unset=True, exclude={"expected_version"}),
            admin_session_id=context.session.id,
        )
    return BookingSummary.from_view(view)


@router.get("/{booking_id}", response_model=BookingSummary)
async def get_booking_detail(
    booking_id: int, context: AuthContextDep, session: SessionDep
) -> BookingSummary:
    """Return one booking of this venue, or 404 for any other tenant (§7.1)."""
    with _translating_errors():
        view = await _view(session, context.venue_id, booking_id)
    return BookingSummary.from_view(view)


@router.get("/{booking_id}/history", response_model=BookingHistoryResponse)
async def get_booking_history(
    booking_id: int, context: AuthContextDep, session: SessionDep
) -> BookingHistoryResponse:
    """Return the append-only history ordered by ``booking_events.id`` (§6.10)."""
    with _translating_errors():
        await get_booking(session, context.venue_id, booking_id)
        events = await list_events(session, context.venue_id, booking_id)
    return BookingHistoryResponse(events=[BookingEventSummary.from_model(e) for e in events])


@router.post(
    "/{booking_id}/cancel",
    response_model=BookingSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def post_cancel_booking(
    booking_id: int,
    payload: BookingCancel,
    context: AuthContextDep,
    session: SessionDep,
) -> BookingSummary:
    """Cancel an unopened booking; deactivates its occupancies (§9, §13)."""
    with _translating_errors():
        view = await cancel_booking(
            session,
            venue_id=context.venue_id,
            booking_id=booking_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            note=payload.note,
            admin_session_id=context.session.id,
        )
    return BookingSummary.from_view(view)


@router.post(
    "/{booking_id}/change-time",
    response_model=BookingSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def post_change_time(
    booking_id: int,
    payload: BookingChangeTime,
    context: AuthContextDep,
    session: SessionDep,
) -> BookingSummary:
    """Reschedule a NEW/WAITING booking and rewrite its shift snapshot (§5.5)."""
    with _translating_errors():
        view = await change_booking_time(
            session,
            venue_id=context.venue_id,
            booking_id=booking_id,
            expected_version=payload.expected_version,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            admin_session_id=context.session.id,
        )
    return BookingSummary.from_view(view)
