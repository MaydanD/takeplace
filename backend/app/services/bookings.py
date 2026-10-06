"""Booking services (PROJECT-SPEC §6.6-6.10, §12, §18, §19, §32, §33, §54).

This module is the single place that creates and mutates bookings. It follows
the canonical resource order of §32.1 exactly::

    venue read (no row lock)
    -> shared schedule advisory lock (create/reschedule)
    -> booking FOR UPDATE (existing-booking mutations)
    -> halls ASC FOR SHARE
    -> tables ASC FOR SHARE
    -> operation_now = clock_timestamp()   (after every business lock)
    -> booking counter (create only)
    -> booking
    -> occupancies
    -> event

Idempotency follows §18.2/§19: the admin ``Idempotency-Key`` is looked up
*before* any mutable validation, so a lost response can never be turned into a
``BOOKING_CONFLICT`` or ``TABLE_NOT_BOOKABLE`` by a world that changed since the
first successful commit. The unique index on ``(venue_id, admin_idempotency_key)``
is the final arbiter of a concurrent double-click; the losing transaction rolls
back, re-reads the row and returns the same booking identity.

PostgreSQL race failures are mapped deterministically (§32.5): ``23P01`` ->
``BOOKING_CONFLICT``, ``23505`` -> idempotent replay, ``40P01``/``40001``/``55P03``
-> bounded retry in a fresh transaction (``55P03`` becomes 503 when exhausted).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, false, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.locks import acquire_schedule_lock_shared
from app.db.models import (
    Booking,
    BookingEvent,
    BookingLiveTable,
    Hall,
    Table,
    TableOccupancy,
    Venue,
)
from app.db.time import SLOT_MINUTES, is_on_5_minute_grid, operation_now
from app.domain.booking import (
    ADMIN_BOOKING_SOURCES,
    BOOKING_REASONS,
    SOURCES_REQUIRING_PHONE,
    BookingRuleError,
    Segment,
    booking_horizon_end,
    capacity_sufficient,
    lifecycle_actions,
    normalize_phone,
    truncate_segment_at,
    undo_open_target,
    validate_booking_interval,
    walk_in_start,
)
from app.domain.schedule import Shift, current_business_date, shift_containing
from app.domain.timezone import load_timezone
from app.realtime.events import BOOKING_CREATED, BOOKING_UPDATED, publish
from app.security.tokens import hmac_sha256_hex
from app.services.errors import (
    BookingConflictError,
    BookingInvalidStateError,
    BookingNotFoundError,
    BookingRuleViolationError,
    BookingStaleError,
    HallNotBookableError,
    IdempotencyKeyReusedError,
    OnlineBookingDisabledError,
    ServiceUnavailableError,
    TableLiveConflictError,
    TableNotBookableError,
    TableNotFoundError,
)
from app.services.live_availability import live_busy_intervals
from app.services.schedule import load_schedule_table

MAX_ATTEMPTS = 3  # 1 try + up to 2 retries (§32.5)
_RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})
_LOCK_TIMEOUT_SQLSTATE = "55P03"


# --- small dataclasses ------------------------------------------------------


@dataclass(slots=True)
class AdminBookingInput:
    """A validated admin create request (transport-agnostic)."""

    starts_at: datetime
    ends_at: datetime
    table_ids: list[int]
    party_size: int
    source: str
    guest_name: str
    guest_phone_raw: str | None = None
    guest_comment: str | None = None
    open_immediately: bool = False


@dataclass(slots=True)
class PublicBookingInput:
    """A validated public ONLINE create request (transport-agnostic, §18, §42).

    ``source`` is fixed to ``ONLINE``: the public endpoint may not create any
    other source. Privacy consent is mandatory and captured here so the booking
    core can persist ``privacy_policy_version``/``privacy_accepted_at``.
    """

    starts_at: datetime
    ends_at: datetime
    table_id: int
    party_size: int
    guest_name: str
    guest_phone_raw: str
    guest_comment: str | None = None
    privacy_policy_version: str = "1.0.0"


@dataclass(slots=True)
class BookingView:
    """A booking plus its currently assigned plan tables."""

    booking: Booking
    table_ids: list[int]
    live_table_ids: list[int] = field(default_factory=list)
    can_investigate_network: bool = False
    available_actions: list[str] = field(default_factory=list)
    is_overdue: bool = False
    is_previous_shift: bool = False
    evaluated_at: datetime | None = None


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    for attr in ("sqlstate", "pgcode"):
        value = getattr(orig, attr, None)
        if value:
            return str(value)
    cause = getattr(orig, "__cause__", None)
    value = getattr(cause, "sqlstate", None)
    return str(value) if value else None


def canonical_admin_payload(*, venue_id: int, data: AdminBookingInput) -> str:
    """Return a deterministic digest input for the admin idempotency HMAC.

    Only the business payload is included; timestamps are ISO-8601 instants and
    the table set is sorted, so a retry of the same user action hashes identically
    regardless of field order (§19).
    """
    payload = {
        "venue_id": venue_id,
        "starts_at": data.starts_at.isoformat(),
        "ends_at": data.ends_at.isoformat(),
        "table_ids": sorted(data.table_ids),
        "party_size": data.party_size,
        "source": data.source,
        "guest_name": data.guest_name,
        "guest_phone_raw": data.guest_phone_raw,
        "guest_comment": data.guest_comment,
    }
    # Preserve the Stage 5 HMAC for ordinary creates/retries.
    if data.open_immediately:
        payload["open_immediately"] = True
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def admin_request_hmac(hmac_key: str, *, venue_id: int, data: AdminBookingInput) -> str:
    """HMAC-SHA-256 of the canonical admin payload under ``IDEMPOTENCY_HMAC_KEY``."""
    return hmac_sha256_hex(hmac_key, canonical_admin_payload(venue_id=venue_id, data=data))


def canonical_public_payload(
    *,
    venue_id: int,
    data: PublicBookingInput,
) -> str:
    """Deterministic JSON digest input for the public idempotency HMAC (§18.2, §42.1).

    Field order is fixed by ``sort_keys``; the phone is the raw validated string
    the server received, so a retry of the same user action hashes identically.
    """
    payload = {
        "venue_id": venue_id,
        "starts_at": data.starts_at.isoformat(),
        "ends_at": data.ends_at.isoformat(),
        "table_id": data.table_id,
        "party_size": data.party_size,
        "guest_name": data.guest_name,
        "guest_phone_raw": data.guest_phone_raw,
        "guest_comment": data.guest_comment,
        "privacy_policy_version": data.privacy_policy_version,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def public_request_hmac(hmac_key: str, *, venue_id: int, data: PublicBookingInput) -> str:
    """HMAC-SHA-256 of the canonical public payload under ``IDEMPOTENCY_HMAC_KEY``."""
    return hmac_sha256_hex(hmac_key, canonical_public_payload(venue_id=venue_id, data=data))


def parse_idempotency_key(raw: str) -> uuid.UUID:
    """Validate the client-supplied ``Idempotency-Key`` header is a UUID."""
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise BookingRuleViolationError("Idempotency-Key must be a UUID") from exc


# --- reads ------------------------------------------------------------------


async def get_booking(session: AsyncSession, venue_id: int, booking_id: int) -> Booking:
    """Load one booking scoped to the venue, or raise ``BookingNotFoundError``."""
    booking = (
        await session.execute(
            select(Booking).where(Booking.id == booking_id, Booking.venue_id == venue_id)
        )
    ).scalar_one_or_none()
    if booking is None:
        raise BookingNotFoundError(booking_id)
    return booking


async def active_table_ids(session: AsyncSession, venue_id: int, booking_id: int) -> list[int]:
    """Return the active plan tables of a booking, ascending (§13)."""
    rows = await session.execute(
        select(TableOccupancy.table_id)
        .where(
            TableOccupancy.venue_id == venue_id,
            TableOccupancy.booking_id == booking_id,
            TableOccupancy.kind == "BOOKING",
            TableOccupancy.is_active.is_(True),
        )
        .order_by(TableOccupancy.table_id)
    )
    return [int(table_id) for table_id in rows.scalars().all()]


async def views_of(
    session: AsyncSession, venue_id: int, bookings: list[Booking]
) -> list[BookingView]:
    """Batch resource reads for a bounded page; never N+1 queries over history."""
    if not bookings:
        return []
    ids = [b.id for b in bookings]
    plans: dict[int, set[int]] = {}
    lives: dict[int, list[int]] = {}
    for booking_id, table_id in (
        await session.execute(
            select(
                TableOccupancy.booking_id,
                TableOccupancy.table_id,
            ).where(
                TableOccupancy.venue_id == venue_id,
                TableOccupancy.booking_id.in_(ids),
                TableOccupancy.kind == "BOOKING",
                TableOccupancy.is_active.is_(True),
            )
        )
    ).all():
        if booking_id is not None:
            plans.setdefault(booking_id, set()).add(table_id)
    for booking_id, table_id in (
        await session.execute(
            select(
                BookingLiveTable.booking_id,
                BookingLiveTable.table_id,
            ).where(BookingLiveTable.venue_id == venue_id, BookingLiveTable.booking_id.in_(ids))
        )
    ).all():
        lives.setdefault(booking_id, []).append(table_id)
    now = await operation_now(session)
    histories: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    open_ids = [b.id for b in bookings if b.status == "OPEN"]
    if open_ids:
        for event in await session.scalars(
            select(BookingEvent)
            .where(BookingEvent.venue_id == venue_id, BookingEvent.booking_id.in_(open_ids))
            .order_by(BookingEvent.id)
        ):
            histories.setdefault(event.booking_id, []).append((event.event_type, event.payload))
    return [
        BookingView(
            booking=b,
            table_ids=sorted(plans.get(b.id, set())),
            live_table_ids=sorted(lives.get(b.id, [])),
            available_actions=lifecycle_actions(
                status=b.status,
                starts_at=b.starts_at,
                ends_at=b.ends_at,
                shift_starts_at=b.shift_starts_at,
                now=now,
                undo_target=undo_open_target(histories.get(b.id, [])),
            ),
            is_overdue=b.status in ("NEW", "WAITING", "OPEN") and b.ends_at < now,
            is_previous_shift=b.status in ("NEW", "WAITING", "OPEN") and b.shift_ends_at <= now,
            evaluated_at=now,
            can_investigate_network=bool(
                b.source == "ONLINE"
                and b.request_ip_hmac
                and b.request_ip_hmac_expires_at
                and b.request_ip_hmac_expires_at > now
            ),
        )
        for b in bookings
    ]


async def view_of(session: AsyncSession, booking: Booking) -> BookingView:
    return (await views_of(session, booking.venue_id, [booking]))[0]


async def list_events(session: AsyncSession, venue_id: int, booking_id: int) -> list[BookingEvent]:
    """Return the append-only history ordered by ``booking_events.id`` (§6.10)."""
    rows = await session.execute(
        select(BookingEvent)
        .where(BookingEvent.venue_id == venue_id, BookingEvent.booking_id == booking_id)
        .order_by(BookingEvent.id)
    )
    return list(rows.scalars().all())


async def find_by_admin_key(session: AsyncSession, venue_id: int, key: uuid.UUID) -> Booking | None:
    """Look up a booking by its admin idempotency key within one venue (§18.2)."""
    return (
        await session.execute(
            select(Booking).where(
                Booking.venue_id == venue_id, Booking.admin_idempotency_key == key
            )
        )
    ).scalar_one_or_none()


async def find_by_public_key(
    session: AsyncSession, venue_id: int, key: uuid.UUID
) -> Booking | None:
    """Look up a booking by its public idempotency key within one venue (§18.2)."""
    return (
        await session.execute(
            select(Booking).where(
                Booking.venue_id == venue_id, Booking.public_idempotency_key == key
            )
        )
    ).scalar_one_or_none()


# --- internal helpers -------------------------------------------------------


async def _lock_halls(session: AsyncSession, venue_id: int, hall_ids: list[int]) -> list[Hall]:
    if not hall_ids:
        return []
    rows = await session.execute(
        select(Hall)
        .where(Hall.venue_id == venue_id, Hall.id.in_(hall_ids))
        .order_by(Hall.id)
        # populate_existing: a locked row may have changed while we waited for the
        # lock, so the ORM copy must be refreshed, not kept from the identity map.
        .execution_options(populate_existing=True)
        .with_for_update(read=True)
    )
    return list(rows.scalars().all())


async def _lock_tables(
    session: AsyncSession, venue_id: int, table_ids: list[int], *, live: bool = False
) -> list[Table]:
    rows = await session.execute(
        select(Table)
        .where(Table.venue_id == venue_id, Table.id.in_(table_ids))
        .order_by(Table.id)
        .execution_options(populate_existing=True)
        .with_for_update(read=not live)
    )
    return list(rows.scalars().all())


def _check_bookable(tables: list[Table], halls: list[Hall]) -> None:
    by_id = {hall.id: hall for hall in halls}
    for table in tables:
        if table.archived_at is not None or not table.is_bookable:
            raise TableNotBookableError(table.id)
        hall = by_id.get(table.hall_id)
        if hall is None or hall.archived_at is not None or not hall.is_bookable:
            raise HallNotBookableError(table.hall_id)


async def _conflicting_booking_ids(
    session: AsyncSession,
    venue_id: int,
    table_ids: list[int],
    starts_at: datetime,
    ends_at: datetime,
    *,
    exclude_booking_id: int | None = None,
) -> list[int]:
    """Return ids of bookings whose active occupancy overlaps the interval (§12).

    Every row of ``table_occupancies`` is guarded by the ``occupancy_no_overlap``
    exclusion constraint, so a same-size query misdetects nothing here; a
    block row is reported with ``booking_id`` NULL through id ``0``.
    """
    query = select(TableOccupancy.booking_id).where(
        TableOccupancy.venue_id == venue_id,
        TableOccupancy.table_id.in_(table_ids),
        TableOccupancy.is_active.is_(True),
        TableOccupancy.starts_at < ends_at,
        TableOccupancy.ends_at > starts_at,
    )
    if exclude_booking_id is not None:
        query = query.where(
            (TableOccupancy.booking_id.is_(None))
            | (TableOccupancy.booking_id != exclude_booking_id)
        )
    rows = await session.execute(query)
    ids = {int(value) if value is not None else 0 for value in rows.scalars().all()}
    return sorted(ids)


def _assert_capacity(
    tables: list[Table], party_size: int, starts_at: datetime, ends_at: datetime
) -> None:
    """Check the §14 capacity invariant across the whole assigned interval."""
    assigned = [(table.capacity, starts_at, ends_at) for table in tables]
    if not capacity_sufficient(
        party_size=party_size,
        assigned=assigned,
        interval_start=starts_at,
        interval_end=ends_at,
    ):
        total = sum(table.capacity for table in tables)
        raise BookingRuleViolationError(
            f"insufficient capacity: party of {party_size} exceeds the {total} seats "
            "of the selected table(s)"
        )


async def _next_booking_number(session: AsyncSession, venue_id: int) -> int:
    """Atomically allocate the next per-venue booking number (§6.7).

    The canonical ``UPDATE ... RETURNING`` is expressed as a single upsert so a
    venue with no counter row (created before Stage 5) is seeded without a
    separate statement; both forms take the same row lock, which serialises all
    creates for a venue.
    """
    result = await session.execute(
        text(
            "INSERT INTO venue_booking_counters (venue_id, last_number) VALUES (:venue_id, 1) "
            "ON CONFLICT (venue_id) DO UPDATE "
            "SET last_number = venue_booking_counters.last_number + 1 "
            "RETURNING last_number"
        ),
        {"venue_id": venue_id},
    )
    return int(result.scalar_one())


def _event(
    *,
    venue_id: int,
    booking_id: int,
    event_type: str,
    actor_type: str,
    created_at: datetime,
    payload: dict[str, Any] | None = None,
    admin_session_id: int | None = None,
) -> BookingEvent:
    return BookingEvent(
        venue_id=venue_id,
        booking_id=booking_id,
        event_type=event_type,
        actor_type=actor_type,
        admin_session_id=admin_session_id,
        payload=payload or {},
        created_at=created_at,
    )


def _assert_replay(booking: Booking, request_hmac: str) -> None:
    """Validate a replayed idempotent request against the stored HMAC (§18.2)."""
    if booking.admin_request_hmac != request_hmac:
        raise IdempotencyKeyReusedError()


async def _lock_halls_tables(
    session: AsyncSession, venue_id: int, table_ids: list[int], *, live: bool = False
) -> tuple[list[Hall], list[Table]]:
    """Lock the involved halls then tables, both ascending by id (§32.1).

    A plain read first derives the hall set (the lock order forbids locking a
    table before its hall); existence is validated against the venue here.
    """
    # Derive the hall set from a column-only read (not ORM entities) so the
    # earlier, unlocked read cannot leave a stale copy in the identity map that
    # the locked read below might then fail to refresh.
    found_ids = {
        int(table_id)
        for table_id in (
            await session.execute(
                select(Table.id).where(Table.venue_id == venue_id, Table.id.in_(table_ids))
            )
        )
        .scalars()
        .all()
    }
    missing = sorted(set(table_ids) - found_ids)
    if missing:
        raise TableNotFoundError(missing[0])
    hall_ids = sorted(
        {
            int(hall_id)
            for hall_id in (
                await session.execute(
                    select(Table.hall_id).where(Table.venue_id == venue_id, Table.id.in_(table_ids))
                )
            )
            .scalars()
            .all()
        }
    )
    halls = await _lock_halls(session, venue_id, hall_ids)
    tables = await _lock_tables(session, venue_id, table_ids, live=live)
    return halls, tables


async def _lock_booking(session: AsyncSession, venue_id: int, booking_id: int) -> Booking:
    booking = (
        await session.execute(
            select(Booking)
            .where(Booking.id == booking_id, Booking.venue_id == venue_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if booking is None:
        raise BookingNotFoundError(booking_id)
    return booking


async def _lock_active_occupancies(
    session: AsyncSession, venue_id: int, booking_id: int
) -> list[TableOccupancy]:
    rows = await session.execute(
        select(TableOccupancy)
        .where(
            TableOccupancy.venue_id == venue_id,
            TableOccupancy.booking_id == booking_id,
            TableOccupancy.is_active.is_(True),
        )
        .order_by(TableOccupancy.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    return list(rows.scalars().all())


async def _live_table_ids(session: AsyncSession, venue_id: int, booking_id: int) -> list[int]:
    """Return the factual live tables of an OPEN booking, ascending (§6.9)."""
    rows = await session.execute(
        select(BookingLiveTable.table_id)
        .where(
            BookingLiveTable.venue_id == venue_id,
            BookingLiveTable.booking_id == booking_id,
        )
        .order_by(BookingLiveTable.table_id)
    )
    return [int(table_id) for table_id in rows.scalars().all()]


# --- create -----------------------------------------------------------------


async def _check_live_conflicts(
    session: AsyncSession,
    venue_id: int,
    table_ids: list[int],
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
    *,
    business_date: date_type | None = None,
    exclude_booking_id: int | None = None,
) -> None:
    if business_date is not None:
        query = select(BookingLiveTable.id).where(
            BookingLiveTable.venue_id == venue_id,
            BookingLiveTable.table_id.in_(table_ids),
            BookingLiveTable.business_date == business_date,
        )
        if exclude_booking_id is not None:
            query = query.where(BookingLiveTable.booking_id != exclude_booking_id)
        existing = await session.scalar(query.limit(1))
        if existing is not None:
            raise TableLiveConflictError()
    for table_id in table_ids:
        for start, end in await live_busy_intervals(
            session, venue_id, table_id, now, exclude_booking_id=exclude_booking_id
        ):
            if start < ends_at and starts_at < end:
                raise BookingConflictError([])


async def _create_once(
    session: AsyncSession,
    *,
    venue_id: int,
    data: AdminBookingInput,
    key: uuid.UUID,
    request_hmac: str,
    admin_session_id: int | None,
) -> Booking:
    """Run the whole create flow inside the caller's transaction (§32.2)."""
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise BookingNotFoundError(venue_id)
    if data.source not in ADMIN_BOOKING_SOURCES:
        raise BookingRuleViolationError(f"source must be one of {ADMIN_BOOKING_SOURCES}")
    if data.open_immediately and data.source != "WALK_IN":
        raise BookingRuleViolationError("open_immediately requires source WALK_IN")
    if data.guest_name is None or not data.guest_name.strip():
        raise BookingRuleViolationError("guest_name is required")
    if not data.table_ids:
        raise BookingRuleViolationError("at least one table is required")
    if len(set(data.table_ids)) != len(data.table_ids):
        raise BookingRuleViolationError("duplicate table in the request")
    if data.source in SOURCES_REQUIRING_PHONE and not data.guest_phone_raw:
        raise BookingRuleViolationError(f"phone is required for source {data.source}")

    # 1. shared schedule advisory lock (create reads schedule, §32.2).
    await acquire_schedule_lock_shared(session, venue_id)

    # 2. resolve the shift/snapshot using the canonical Stage 3 resolver.
    venue_tz = load_timezone(venue.timezone)
    schedule = await load_schedule_table(session, venue_id)
    # 3. halls ASC FOR SHARE, then tables ASC FOR SHARE (§32.1).
    halls, tables = await _lock_halls_tables(
        session, venue_id, data.table_ids, live=data.open_immediately
    )

    # 4. operation_now, after every business lock.
    now = await operation_now(session)
    containing = shift_containing(
        schedule, now if data.open_immediately else data.starts_at, venue_tz
    )
    if containing is None:
        raise BookingRuleViolationError("no shift contains the requested start time")
    business_date, shift = containing

    # 5. re-validate time/shift/bookability/capacity/conflicts on locked state.
    try:
        if data.open_immediately:
            data = replace(
                data, starts_at=walk_in_start(now=now, ends_at=data.ends_at, shift=shift)
            )
        else:
            validate_booking_interval(
                starts_at=data.starts_at, ends_at=data.ends_at, shift=shift, now=now
            )
    except BookingRuleError as exc:
        raise BookingRuleViolationError(exc.reason) from exc
    if business_date > booking_horizon_end(current_business_date(now, schedule, venue_tz)):
        raise BookingRuleViolationError("business date is beyond the booking horizon")
    _check_bookable(tables, halls)
    _assert_capacity(tables, data.party_size, data.starts_at, data.ends_at)
    conflicts = await _conflicting_booking_ids(
        session,
        venue_id,
        data.table_ids,
        now if data.open_immediately else data.starts_at,
        data.ends_at,
    )
    if conflicts:
        raise BookingConflictError(conflicts)
    await _check_live_conflicts(
        session,
        venue_id,
        data.table_ids,
        data.starts_at,
        data.ends_at,
        now,
        business_date=business_date if data.open_immediately else None,
    )

    # 6. booking counter, booking, occupancies, event.
    number = await _next_booking_number(session, venue_id)
    booking = Booking(
        venue_id=venue_id,
        number=number,
        business_date=business_date,
        shift_starts_at=shift.start,
        shift_ends_at=shift.end,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        guest_name=data.guest_name.strip(),
        guest_phone_raw=data.guest_phone_raw,
        guest_phone_normalized=normalize_phone(data.guest_phone_raw)
        if data.guest_phone_raw
        else None,
        party_size=data.party_size,
        guest_comment=data.guest_comment,
        public_idempotency_key=None,
        public_request_hmac=None,
        admin_idempotency_key=key,
        admin_request_hmac=request_hmac,
        request_ip_hmac=None,
        request_ip_hmac_expires_at=None,
        source=data.source,
        status="OPEN" if data.open_immediately else "NEW",
        waiting_at=None,
        opened_at=now if data.open_immediately else None,
        closed_at=None,
        canceled_at=None,
        cancellation_reason=None,
        cancellation_note=None,
        privacy_policy_version=None,
        privacy_accepted_at=None,
        anonymized_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(booking)
    await session.flush()
    for table in tables:
        session.add(
            TableOccupancy(
                venue_id=venue_id,
                table_id=table.id,
                kind="BOOKING",
                booking_id=booking.id,
                starts_at=data.starts_at,
                ends_at=data.ends_at,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    session.add(
        _event(
            venue_id=venue_id,
            booking_id=booking.id,
            event_type="BOOKING_CREATED",
            actor_type="ADMIN",
            admin_session_id=admin_session_id,
            created_at=now,
            payload={
                "source": data.source,
                "party_size": data.party_size,
                "business_date": business_date.isoformat(),
                "starts_at": data.starts_at.isoformat(),
                "ends_at": data.ends_at.isoformat(),
                "table_ids": sorted(table.id for table in tables),
            },
        )
    )
    await session.flush()
    if data.open_immediately:
        for table in tables:
            session.add(
                BookingLiveTable(
                    venue_id=venue_id,
                    booking_id=booking.id,
                    business_date=business_date,
                    table_id=table.id,
                    live_since=now,
                )
            )
        session.add(
            _event(
                venue_id=venue_id,
                booking_id=booking.id,
                event_type="BOOKING_OPENED",
                actor_type="ADMIN",
                admin_session_id=admin_session_id,
                created_at=now,
                payload={"table_ids": sorted(data.table_ids)},
            )
        )
        await session.flush()
    # Transactional realtime signal: delivered only if this transaction commits.
    await publish(session, venue_id=venue_id, event_type=BOOKING_CREATED, ids=[booking.id])
    return booking


async def _replay(
    session: AsyncSession, venue_id: int, booking: Booking, request_hmac: str
) -> BookingView:
    _assert_replay(booking, request_hmac)
    return await view_of(session, booking)


async def create_admin_booking(
    session: AsyncSession,
    *,
    venue_id: int,
    data: AdminBookingInput,
    idempotency_key: str,
    hmac_key: str,
    admin_session_id: int | None,
) -> tuple[BookingView, bool]:
    """Create a manual booking idempotently (§19, §32.2).

    Returns ``(view, created)``: ``created=False`` marks an idempotent replay
    (HTTP 200); a fresh booking is 201. The idempotency lookup happens before any
    mutable validation, so a replay survives a changed schedule/kill switch.
    """
    key = parse_idempotency_key(idempotency_key)
    request_hmac = admin_request_hmac(hmac_key, venue_id=venue_id, data=data)

    # Idempotency lookup *before* mutable validations (§18.2/§19).
    async with session.begin():
        existing = await find_by_admin_key(session, venue_id, key)
    if existing is not None:
        return await _replay(session, venue_id, existing, request_hmac), False

    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.begin():
                booking = await _create_once(
                    session,
                    venue_id=venue_id,
                    data=data,
                    key=key,
                    request_hmac=request_hmac,
                    admin_session_id=admin_session_id,
                )
            return await view_of(session, booking), True
        except (BookingConflictError, TableLiveConflictError):
            # A serialized WALK_IN retry sees the winner before INSERT (§19).
            async with session.begin():
                existing = await find_by_admin_key(session, venue_id, key)
            if existing is not None:
                return await _replay(session, venue_id, existing, request_hmac), False
            raise
        except IntegrityError as exc:
            state = _sqlstate(exc)
            if state == "23505":  # idempotency-key unique race -> replay lookup
                async with session.begin():
                    existing = await find_by_admin_key(session, venue_id, key)
                if existing is not None:
                    return await _replay(session, venue_id, existing, request_hmac), False
                raise
            if state == "23P01":  # exclusion constraint -> BOOKING_CONFLICT
                async with session.begin():
                    existing = await find_by_admin_key(session, venue_id, key)
                if existing is not None:
                    return await _replay(session, venue_id, existing, request_hmac), False
                raise BookingConflictError([]) from exc
            raise
        except DBAPIError as exc:
            state = _sqlstate(exc)
            if state in _RETRYABLE_SQLSTATES and attempt + 1 < MAX_ATTEMPTS:
                continue
            if state == _LOCK_TIMEOUT_SQLSTATE:
                raise ServiceUnavailableError() from exc
            raise
    raise ServiceUnavailableError()  # pragma: no cover - the loop always returns or raises


# --- public create (ONLINE) -------------------------------------------------


def _assert_public_replay(booking: Booking, request_hmac: str) -> None:
    """Validate a replayed public idempotent request against the stored HMAC (§18.2)."""
    if booking.public_request_hmac != request_hmac:
        raise IdempotencyKeyReusedError()


async def _create_public_once(
    session: AsyncSession,
    *,
    venue_id: int,
    data: PublicBookingInput,
    key: uuid.UUID,
    request_hmac: str,
    request_ip_hmac: str,
    ip_hmac_ttl_days: int,
) -> Booking:
    """Run the public ONLINE create flow inside the caller's transaction (§32.2).

    Shares the canonical lock order, schedule resolution, capacity/conflict
    validation and occupancy/event helpers with the admin path. The public
    differences are: ``source='ONLINE'``, public idempotency columns, the
    ``request_ip_hmac`` fingerprint, privacy consent, a final plain
    ``online_booking_enabled`` gate after the locks (§32.2) and a ``PUBLIC``
    event actor.
    """
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise BookingNotFoundError(venue_id)
    if not venue.is_active:
        raise BookingNotFoundError(venue_id)
    if data.guest_name is None or not data.guest_name.strip():
        raise BookingRuleViolationError("guest_name is required")
    if not data.guest_phone_raw or not data.guest_phone_raw.strip():
        raise BookingRuleViolationError("phone is required for ONLINE booking")

    # 1. shared schedule advisory lock (create reads schedule, §32.2).
    await acquire_schedule_lock_shared(session, venue_id)

    # 2. resolve the shift/snapshot using the canonical Stage 3 resolver.
    venue_tz = load_timezone(venue.timezone)
    schedule = await load_schedule_table(session, venue_id)
    containing = shift_containing(schedule, data.starts_at, venue_tz)
    if containing is None:
        raise BookingRuleViolationError("no shift contains the requested start time")
    business_date, shift = containing

    # 3. halls ASC FOR SHARE, then tables ASC FOR SHARE (§32.1).
    halls, tables = await _lock_halls_tables(session, venue_id, [data.table_id])

    # 4. operation_now, after every business lock.
    now = await operation_now(session)

    # 5. Final plain SELECT kill-switch gate after the locks (§32.2, §54.11a).
    #    Public create does not FOR SHARE the venues row, so disabling online
    #    booking stays responsive even under abuse.
    # Read columns explicitly: session.get() can return the pre-lock identity-map
    # object and miss a kill switch committed while this request waited.
    fresh = (
        await session.execute(
            select(Venue.is_active, Venue.online_booking_enabled).where(Venue.id == venue_id)
        )
    ).one_or_none()
    if fresh is None or not fresh.is_active or not fresh.online_booking_enabled:
        raise OnlineBookingDisabledError()

    # 6. re-validate time/shift/bookability/capacity/conflicts on locked state.
    try:
        validate_booking_interval(
            starts_at=data.starts_at, ends_at=data.ends_at, shift=shift, now=now
        )
    except BookingRuleError as exc:
        raise BookingRuleViolationError(exc.reason) from exc
    if business_date > booking_horizon_end(current_business_date(now, schedule, venue_tz)):
        raise BookingRuleViolationError("business date is beyond the booking horizon")
    _check_bookable(tables, halls)
    _assert_capacity(tables, data.party_size, data.starts_at, data.ends_at)
    conflicts = await _conflicting_booking_ids(
        session, venue_id, [data.table_id], data.starts_at, data.ends_at
    )
    if conflicts:
        raise BookingConflictError(conflicts)

    await _check_live_conflicts(
        session, venue_id, [data.table_id], data.starts_at, data.ends_at, now
    )

    # 7. booking counter, booking, occupancies, event.
    number = await _next_booking_number(session, venue_id)
    ip_hmac_expires = now + timedelta(days=ip_hmac_ttl_days)
    booking = Booking(
        venue_id=venue_id,
        number=number,
        business_date=business_date,
        shift_starts_at=shift.start,
        shift_ends_at=shift.end,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        guest_name=data.guest_name.strip(),
        guest_phone_raw=data.guest_phone_raw,
        guest_phone_normalized=normalize_phone(data.guest_phone_raw),
        party_size=data.party_size,
        guest_comment=data.guest_comment,
        public_idempotency_key=key,
        public_request_hmac=request_hmac,
        admin_idempotency_key=None,
        admin_request_hmac=None,
        request_ip_hmac=request_ip_hmac,
        request_ip_hmac_expires_at=ip_hmac_expires,
        source="ONLINE",
        status="NEW",
        waiting_at=None,
        opened_at=None,
        closed_at=None,
        canceled_at=None,
        cancellation_reason=None,
        cancellation_note=None,
        privacy_policy_version=data.privacy_policy_version,
        privacy_accepted_at=now,
        anonymized_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(booking)
    await session.flush()
    for table in tables:
        session.add(
            TableOccupancy(
                venue_id=venue_id,
                table_id=table.id,
                kind="BOOKING",
                booking_id=booking.id,
                starts_at=data.starts_at,
                ends_at=data.ends_at,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    session.add(
        _event(
            venue_id=venue_id,
            booking_id=booking.id,
            event_type="BOOKING_CREATED",
            actor_type="PUBLIC",
            admin_session_id=None,
            created_at=now,
            payload={
                "source": "ONLINE",
                "party_size": data.party_size,
                "business_date": business_date.isoformat(),
                "starts_at": data.starts_at.isoformat(),
                "ends_at": data.ends_at.isoformat(),
                "table_ids": [table.id for table in tables],
            },
        )
    )
    await session.flush()
    await publish(session, venue_id=venue_id, event_type=BOOKING_CREATED, ids=[booking.id])
    return booking


async def _replay_public(
    session: AsyncSession, venue_id: int, booking: Booking, request_hmac: str
) -> BookingView:
    _assert_public_replay(booking, request_hmac)
    return await view_of(session, booking)


async def create_public_booking(
    session: AsyncSession,
    *,
    venue_id: int,
    data: PublicBookingInput,
    idempotency_key: str,
    hmac_key: str,
    request_ip_hmac: str,
    ip_hmac_ttl_days: int,
) -> tuple[BookingView, bool]:
    """Create a public ONLINE booking idempotently (§18, §18.2, §32.2).

    Returns ``(view, created)``: ``created=False`` marks an idempotent replay
    (HTTP 200); a fresh booking is 201. The idempotency lookup happens before
    any mutable validation, so a replay survives a changed schedule/kill switch.
    """
    key = parse_idempotency_key(idempotency_key)
    request_hmac = public_request_hmac(hmac_key, venue_id=venue_id, data=data)

    # Idempotency lookup *before* mutable validations (§18.2/§18.2 step 3–6).
    async with session.begin():
        existing = await find_by_public_key(session, venue_id, key)
    if existing is not None:
        return await _replay_public(session, venue_id, existing, request_hmac), False

    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.begin():
                booking = await _create_public_once(
                    session,
                    venue_id=venue_id,
                    data=data,
                    key=key,
                    request_hmac=request_hmac,
                    request_ip_hmac=request_ip_hmac,
                    ip_hmac_ttl_days=ip_hmac_ttl_days,
                )
            return await view_of(session, booking), True
        except IntegrityError as exc:
            state = _sqlstate(exc)
            if state == "23505":  # public idempotency-key unique race -> replay
                async with session.begin():
                    existing = await find_by_public_key(session, venue_id, key)
                if existing is not None:
                    return await _replay_public(session, venue_id, existing, request_hmac), False
                raise
            if state == "23P01":  # exclusion constraint -> BOOKING_CONFLICT
                async with session.begin():
                    existing = await find_by_public_key(session, venue_id, key)
                if existing is not None:
                    return await _replay_public(session, venue_id, existing, request_hmac), False
                raise BookingConflictError([]) from exc
            raise
        except DBAPIError as exc:
            state = _sqlstate(exc)
            if state in _RETRYABLE_SQLSTATES and attempt + 1 < MAX_ATTEMPTS:
                continue
            if state == _LOCK_TIMEOUT_SQLSTATE:
                raise ServiceUnavailableError() from exc
            raise
    raise ServiceUnavailableError()  # pragma: no cover - the loop always returns or raises


# --- cancel -----------------------------------------------------------------


async def _cancel_once(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    reason: str,
    note: str | None,
    admin_session_id: int | None,
) -> Booking:
    if reason not in BOOKING_REASONS:
        raise BookingRuleViolationError(f"unknown cancellation reason {reason!r}")
    await session.get(Venue, venue_id)
    booking = await _lock_booking(session, venue_id, booking_id)
    if booking.version != expected_version:
        raise BookingStaleError(booking_id)
    if booking.status not in ("NEW", "WAITING"):
        raise BookingInvalidStateError(f"a booking in status {booking.status} cannot be canceled")
    # WAITING's dynamic overlay is also released by cancellation.
    await _lock_halls_tables(
        session, venue_id, await active_table_ids(session, venue_id, booking_id), live=True
    )
    now = await operation_now(session)
    for occupancy in await _lock_active_occupancies(session, venue_id, booking_id):
        segment = truncate_segment_at(
            Segment(occupancy.starts_at, occupancy.ends_at, occupancy.is_active), now
        )
        occupancy.starts_at = segment.starts_at
        occupancy.ends_at = segment.ends_at
        occupancy.is_active = False
        occupancy.updated_at = now
    booking.status = "CANCELED"
    booking.canceled_at = now
    booking.cancellation_reason = reason
    booking.cancellation_note = note
    booking.version += 1
    booking.updated_at = now
    session.add(
        _event(
            venue_id=venue_id,
            booking_id=booking.id,
            event_type="BOOKING_CANCELED",
            actor_type="ADMIN",
            admin_session_id=admin_session_id,
            created_at=now,
            payload={"reason": reason},
        )
    )
    await session.flush()
    await publish(session, venue_id=venue_id, event_type=BOOKING_UPDATED, ids=[booking.id])
    return booking


async def cancel_booking(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    reason: str,
    note: str | None = None,
    admin_session_id: int | None,
) -> BookingView:
    """Cancel an unopened booking and deactivate its occupancies (§9, §11, §54.12).

    A canceled booking must never keep an active BOOKING occupancy (§13).
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.begin():
                booking = await _cancel_once(
                    session,
                    venue_id=venue_id,
                    booking_id=booking_id,
                    expected_version=expected_version,
                    reason=reason,
                    note=note,
                    admin_session_id=admin_session_id,
                )
            return await view_of(session, booking)
        except DBAPIError as exc:
            state = _sqlstate(exc)
            if state in _RETRYABLE_SQLSTATES and attempt + 1 < MAX_ATTEMPTS:
                continue
            if state == _LOCK_TIMEOUT_SQLSTATE:
                raise ServiceUnavailableError() from exc
            raise
    raise ServiceUnavailableError()  # pragma: no cover - the loop always returns or raises


# --- change time ------------------------------------------------------------


async def lifecycle_booking(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    action: str,
    admin_session_id: int | None,
) -> BookingView:
    """Stage 8 commands share the existing booking locks, clock and event core."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.begin():
                await session.get(Venue, venue_id)
                booking = await _lock_booking(session, venue_id, booking_id)
                if booking.version != expected_version:
                    raise BookingStaleError(booking_id)
                allowed_states = {
                    "wait": ("NEW",),
                    "open": ("NEW", "WAITING"),
                    "undo-open": ("OPEN",),
                    "close": ("OPEN",),
                }
                if booking.status not in allowed_states.get(action, ()):
                    raise BookingInvalidStateError(f"cannot {action} a {booking.status} booking")
                # Lock every possible segment table before the clock: waiting for a lock
                # can cross a segment boundary. Select the actual set only afterwards.
                plan_ids = await active_table_ids(session, venue_id, booking_id)
                live_ids = list(
                    await session.scalars(
                        select(BookingLiveTable.table_id).where(
                            BookingLiveTable.venue_id == venue_id,
                            BookingLiveTable.booking_id == booking_id,
                        )
                    )
                )
                _, tables = await _lock_halls_tables(
                    session, venue_id, sorted(set(plan_ids + live_ids)), live=True
                )
                now = await operation_now(session)
                target = None
                if action == "undo-open":
                    history = await list_events(session, venue_id, booking_id)
                    target = undo_open_target([(e.event_type, e.payload) for e in history])
                if action not in lifecycle_actions(
                    status=booking.status,
                    starts_at=booking.starts_at,
                    ends_at=booking.ends_at,
                    shift_starts_at=booking.shift_starts_at,
                    now=now,
                    undo_target=target,
                ):
                    raise BookingRuleViolationError("lifecycle time or undo history restriction")
                occupancies = await _lock_active_occupancies(session, venue_id, booking_id)
                payload: dict[str, Any] = {}
                if action == "wait":
                    booking.status = "WAITING"
                    booking.waiting_at = now
                    event_type = "WAITING_SET"
                elif action == "open":
                    at = max(now, booking.starts_at)
                    assigned_ids = sorted(
                        {o.table_id for o in occupancies if o.starts_at <= at < o.ends_at}
                    )
                    capacities = {t.id: t.capacity for t in tables}
                    if (
                        not assigned_ids
                        or not capacity_sufficient(
                            party_size=booking.party_size,
                            assigned=[
                                (capacities[o.table_id], o.starts_at, o.ends_at)
                                for o in occupancies
                            ],
                            interval_start=at,
                            interval_end=booking.ends_at,
                        )
                        or sum(capacities[i] for i in assigned_ids) < booking.party_size
                    ):
                        raise BookingRuleViolationError("insufficient assigned/live capacity")
                    # Operational disable only prohibits NEW reservations (§29.1/2).
                    # Opening an existing plan is allowed; archive cannot retain future plan.
                    if any(t.archived_at is not None for t in tables if t.id in assigned_ids):
                        raise TableNotBookableError(assigned_ids[0])
                    live_conflict = await session.scalar(
                        select(BookingLiveTable.id)
                        .where(
                            BookingLiveTable.venue_id == venue_id,
                            BookingLiveTable.business_date == booking.business_date,
                            BookingLiveTable.table_id.in_(assigned_ids),
                        )
                        .limit(1)
                    )
                    if live_conflict is not None:
                        raise TableLiveConflictError()
                    # Also validate present occupancy for normal OPEN; the early gap is
                    # not protected by the plan's exclusion constraint.
                    conflicts = await _conflicting_booking_ids(
                        session,
                        venue_id,
                        assigned_ids,
                        now,
                        booking.starts_at
                        if now < booking.starts_at
                        else now + timedelta(microseconds=1),
                        exclude_booking_id=booking_id,
                    )
                    if conflicts:
                        raise BookingConflictError(conflicts)
                    payload = {"previous_status": booking.status, "table_ids": assigned_ids}
                    for table_id in assigned_ids:
                        session.add(
                            BookingLiveTable(
                                venue_id=venue_id,
                                booking_id=booking_id,
                                business_date=booking.business_date,
                                table_id=table_id,
                                live_since=now,
                            )
                        )
                    booking.status = "OPEN"
                    booking.opened_at = now
                    event_type = "BOOKING_OPENED"
                else:
                    await session.execute(
                        delete(BookingLiveTable).where(
                            BookingLiveTable.venue_id == venue_id,
                            BookingLiveTable.booking_id == booking_id,
                        )
                    )
                    if action == "undo-open":
                        if target is None:  # Defensive: eligibility above already checked it.
                            raise BookingRuleViolationError("no reversible OPEN event")
                        booking.status = target
                        booking.opened_at = None
                        payload = {"restored_status": target}
                        event_type = "OPEN_UNDONE"
                    else:
                        for occupancy in occupancies:
                            segment = truncate_segment_at(
                                Segment(occupancy.starts_at, occupancy.ends_at), now
                            )
                            occupancy.ends_at = segment.ends_at
                            occupancy.is_active = segment.is_active
                            occupancy.updated_at = now
                        booking.status = "CLOSED"
                        booking.closed_at = now
                        event_type = "BOOKING_CLOSED"
                booking.version += 1
                booking.updated_at = now
                session.add(
                    _event(
                        venue_id=venue_id,
                        booking_id=booking_id,
                        event_type=event_type,
                        actor_type="ADMIN",
                        admin_session_id=admin_session_id,
                        created_at=now,
                        payload=payload,
                    )
                )
                await session.flush()
                await publish(
                    session,
                    venue_id=venue_id,
                    event_type=BOOKING_UPDATED,
                    ids=[booking_id],
                )
            return await view_of(session, booking)
        except DBAPIError as exc:
            state = _sqlstate(exc)
            if state in _RETRYABLE_SQLSTATES and attempt + 1 < MAX_ATTEMPTS:
                continue
            if state == "23P01":
                raise BookingConflictError([]) from exc
            if state == "23505" and "booking_live_tables" in str(exc.orig):
                raise TableLiveConflictError() from exc
            if state == _LOCK_TIMEOUT_SQLSTATE:
                raise ServiceUnavailableError() from exc
            raise
    raise ServiceUnavailableError()


async def _change_open_end_once(
    session: AsyncSession,
    *,
    venue: Venue,
    booking_id: int,
    expected_version: int,
    starts_at: datetime | None,
    ends_at: datetime,
    admin_session_id: int | None,
) -> Booking:
    """Change only the ``ends_at`` of an OPEN booking before its plan end (§26.2).

    ``starts_at`` never moves for OPEN. An extension grows only the tail
    effective segments (the past is never re-derived); a shortening frees the
    tail through the canonical ``truncate_segment_at``. Live rows are untouched.
    """
    venue_id = venue.id
    booking = await _lock_booking(session, venue_id, booking_id)
    if booking.version != expected_version:
        raise BookingStaleError(booking_id)
    if booking.status != "OPEN":
        raise BookingInvalidStateError(
            f"a booking in status {booking.status} cannot change its end time"
        )
    if starts_at is not None and starts_at != booking.starts_at:
        raise BookingRuleViolationError("an OPEN booking cannot move its start time")
    plan_ids = await active_table_ids(session, venue_id, booking_id)
    live_ids = await _live_table_ids(session, venue_id, booking_id)
    table_ids = sorted(set(plan_ids) | set(live_ids))
    if not table_ids:
        raise BookingInvalidStateError("the open booking has no assigned tables")
    # A live mutation is serialised with create/early-OPEN through FOR UPDATE (§32.3).
    _, tables = await _lock_halls_tables(session, venue_id, table_ids, live=True)
    by_id = {table.id: table for table in tables}
    now = await operation_now(session)
    old_end = booking.ends_at
    if now >= old_end:
        raise BookingInvalidStateError("a booking past its plan end cannot change its end time")
    if not is_on_5_minute_grid(ends_at):
        raise BookingRuleViolationError(f"ends_at must be on the {SLOT_MINUTES}-minute grid")
    if ends_at <= max(now, booking.starts_at):
        raise BookingRuleViolationError("the new end must be after the current booking start")
    if ends_at > booking.shift_ends_at:
        raise BookingRuleViolationError("the booking interval must stay inside one shift")
    occupancies = await _lock_active_occupancies(session, venue_id, booking_id)
    if ends_at > old_end:
        await _check_live_conflicts(
            session,
            venue_id,
            live_ids,
            old_end,
            ends_at,
            now,
            business_date=booking.business_date,
            exclude_booking_id=booking_id,
        )
        conflicts = await _conflicting_booking_ids(
            session, venue_id, live_ids, old_end, ends_at, exclude_booking_id=booking_id
        )
        if conflicts:
            raise BookingConflictError(conflicts)
        live_capacity = sum(by_id[table_id].capacity for table_id in live_ids)
        if live_capacity < booking.party_size:
            raise BookingRuleViolationError("insufficient live capacity for the party size")
        tail_by_table = {
            occupancy.table_id: occupancy
            for occupancy in occupancies
            if occupancy.ends_at == old_end and occupancy.is_active
        }
        for table_id in live_ids:
            occupancy = tail_by_table.get(table_id)
            if occupancy is not None:
                occupancy.ends_at = ends_at
                occupancy.updated_at = now
            else:
                session.add(
                    TableOccupancy(
                        venue_id=venue_id,
                        table_id=table_id,
                        kind="BOOKING",
                        booking_id=booking_id,
                        starts_at=old_end,
                        ends_at=ends_at,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
    else:
        for occupancy in occupancies:
            segment = truncate_segment_at(
                Segment(occupancy.starts_at, occupancy.ends_at, occupancy.is_active), ends_at
            )
            occupancy.starts_at = segment.starts_at
            occupancy.ends_at = segment.ends_at
            occupancy.is_active = segment.is_active
            occupancy.updated_at = now
    booking.ends_at = ends_at
    booking.version += 1
    booking.updated_at = now
    session.add(
        _event(
            venue_id=venue_id,
            booking_id=booking_id,
            event_type="TIME_CHANGED",
            actor_type="ADMIN",
            admin_session_id=admin_session_id,
            created_at=now,
            payload={
                "starts_at": booking.starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
            },
        )
    )
    await session.flush()
    await publish(session, venue_id=venue_id, event_type=BOOKING_UPDATED, ids=[booking_id])
    return booking


async def _change_time_once(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    starts_at: datetime | None,
    ends_at: datetime,
    admin_session_id: int | None,
) -> Booking:
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise BookingNotFoundError(venue_id)
    # Decide the lock path from a plain read; the authoritative status is
    # re-checked under the booking lock inside each branch. An OPEN booking can
    # only change its end, which cannot move the business date, so it does not
    # take the schedule lock (§32.3).
    peek = (
        await session.execute(
            select(Booking.status).where(Booking.id == booking_id, Booking.venue_id == venue_id)
        )
    ).scalar_one_or_none()
    if peek is None:
        raise BookingNotFoundError(booking_id)
    if peek == "OPEN":
        return await _change_open_end_once(
            session,
            venue=venue,
            booking_id=booking_id,
            expected_version=expected_version,
            starts_at=starts_at,
            ends_at=ends_at,
            admin_session_id=admin_session_id,
        )
    # A reschedule can change the business date, so it holds the shared schedule
    # lock before touching the booking (§32.3).
    await acquire_schedule_lock_shared(session, venue_id)
    booking = await _lock_booking(session, venue_id, booking_id)
    if booking.version != expected_version:
        raise BookingStaleError(booking_id)
    if booking.status not in ("NEW", "WAITING"):
        raise BookingInvalidStateError(
            f"a booking in status {booking.status} cannot be rescheduled"
        )
    if starts_at is None:
        starts_at = booking.starts_at
    venue_tz = load_timezone(venue.timezone)
    schedule = await load_schedule_table(session, venue_id)
    end_only = starts_at == booking.starts_at
    containing = (
        (
            booking.business_date,
            Shift(booking.business_date, booking.shift_starts_at, booking.shift_ends_at),
        )
        if end_only
        else shift_containing(schedule, starts_at, venue_tz)
    )
    if containing is None:
        raise BookingRuleViolationError("no shift contains the new start time")
    business_date, shift = containing
    table_ids = await active_table_ids(session, venue_id, booking_id)
    if not table_ids:
        raise BookingInvalidStateError("the booking has no assigned tables")
    halls, tables = await _lock_halls_tables(session, venue_id, table_ids)
    now = await operation_now(session)
    try:
        if end_only and (now >= booking.ends_at or ends_at <= now):
            raise BookingRuleError("an expired interval requires a full reschedule")
        validate_booking_interval(
            starts_at=starts_at, ends_at=ends_at, shift=shift, now=None if end_only else now
        )
    except BookingRuleError as exc:
        raise BookingRuleViolationError(exc.reason) from exc
    if business_date > booking_horizon_end(current_business_date(now, schedule, venue_tz)):
        raise BookingRuleViolationError("business date is beyond the booking horizon")
    _check_bookable(tables, halls)
    check_start = booking.ends_at if end_only else starts_at
    _assert_capacity(tables, booking.party_size, starts_at, ends_at)
    # End-only edits reserve only the extended tail; shortening reserves nothing.
    # In particular, never backfill a replacement table into the booking's past.
    if check_start < ends_at:
        await _check_live_conflicts(
            session, venue_id, table_ids, check_start, ends_at, now, exclude_booking_id=booking_id
        )
        conflicts = await _conflicting_booking_ids(
            session, venue_id, table_ids, check_start, ends_at, exclude_booking_id=booking_id
        )
        if conflicts:
            raise BookingConflictError(conflicts)
    for occupancy in await _lock_active_occupancies(session, venue_id, booking_id):
        if end_only:
            if ends_at > booking.ends_at:
                if occupancy.ends_at == booking.ends_at:
                    occupancy.ends_at = ends_at
            else:
                segment = truncate_segment_at(
                    Segment(occupancy.starts_at, occupancy.ends_at, occupancy.is_active), ends_at
                )
                occupancy.starts_at = segment.starts_at
                occupancy.ends_at = segment.ends_at
                occupancy.is_active = segment.is_active
            occupancy.updated_at = now
            continue
        segment = truncate_segment_at(
            Segment(occupancy.starts_at, occupancy.ends_at, occupancy.is_active), now
        )
        occupancy.starts_at = segment.starts_at
        occupancy.ends_at = segment.ends_at
        occupancy.is_active = False
        occupancy.updated_at = now
    for table in tables if not end_only else []:
        session.add(
            TableOccupancy(
                venue_id=venue_id,
                table_id=table.id,
                kind="BOOKING",
                booking_id=booking.id,
                starts_at=starts_at,
                ends_at=ends_at,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    changed_date = business_date != booking.business_date
    booking.business_date = business_date
    booking.shift_starts_at = shift.start
    booking.shift_ends_at = shift.end
    booking.starts_at = starts_at
    booking.ends_at = ends_at
    if booking.status == "WAITING" and starts_at > now:
        booking.status = "NEW"
        booking.waiting_at = None
    booking.version += 1
    booking.updated_at = now
    session.add(
        _event(
            venue_id=venue_id,
            booking_id=booking.id,
            event_type="BOOKING_RESCHEDULED" if changed_date else "TIME_CHANGED",
            actor_type="ADMIN",
            admin_session_id=admin_session_id,
            created_at=now,
            payload={
                "business_date": business_date.isoformat(),
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
            },
        )
    )
    await session.flush()
    await publish(session, venue_id=venue_id, event_type=BOOKING_UPDATED, ids=[booking.id])
    return booking


async def change_booking_time(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    starts_at: datetime | None,
    ends_at: datetime,
    admin_session_id: int | None,
) -> BookingView:
    """Move a NEW/WAITING booking, or change an OPEN booking's end (§5.5, §26)."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.begin():
                booking = await _change_time_once(
                    session,
                    venue_id=venue_id,
                    booking_id=booking_id,
                    expected_version=expected_version,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    admin_session_id=admin_session_id,
                )
            return await view_of(session, booking)
        except DBAPIError as exc:
            state = _sqlstate(exc)
            if state in _RETRYABLE_SQLSTATES and attempt + 1 < MAX_ATTEMPTS:
                continue
            if state == _LOCK_TIMEOUT_SQLSTATE:
                raise ServiceUnavailableError() from exc
            raise
    raise ServiceUnavailableError()  # pragma: no cover - the loop always returns or raises


# --- table assignment (add/remove/replace/reseat) ---------------------------


async def _mutate_tables_once(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    add_ids: list[int],
    remove_ids: list[int],
    admin_session_id: int | None,
) -> Booking:
    """Add and/or remove tables atomically, for NEW/WAITING (plan) or OPEN (live).

    Ordering (§32.3): booking ``FOR UPDATE`` then the union of the old and new
    tables ascending (``FOR SHARE`` for a plan mutation, ``FOR UPDATE`` when the
    live set changes). A plan mutation rewrites ``table_occupancies``; an OPEN
    mutation additionally rewrites ``booking_live_tables`` so plan and live fact
    never diverge. A remove inside the interval ends the effective segment
    through the canonical ``truncate_segment_at`` (§20.2/§20.5).
    """
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise BookingNotFoundError(venue_id)
    if len(set(add_ids)) != len(add_ids) or len(set(remove_ids)) != len(remove_ids):
        raise BookingRuleViolationError("duplicate table in the request")
    add_ids = sorted(add_ids)
    remove_ids = sorted(remove_ids)
    overlap = set(add_ids) & set(remove_ids)
    if overlap:
        raise BookingRuleViolationError("a table cannot be added and removed at once")
    booking = await _lock_booking(session, venue_id, booking_id)
    if booking.version != expected_version:
        raise BookingStaleError(booking_id)
    if booking.status not in ("NEW", "WAITING", "OPEN"):
        raise BookingInvalidStateError(
            f"a booking in status {booking.status} cannot change its tables"
        )
    is_open = booking.status == "OPEN"
    current_ids = set(await active_table_ids(session, venue_id, booking_id))
    if is_open:
        current_ids |= set(await _live_table_ids(session, venue_id, booking_id))
    for table_id in remove_ids:
        if table_id not in current_ids:
            raise BookingRuleViolationError(f"table {table_id} is not assigned to this booking")
    for table_id in add_ids:
        if table_id in current_ids:
            raise BookingRuleViolationError(f"table {table_id} is already assigned to this booking")
    new_ids = sorted((current_ids - set(remove_ids)) | set(add_ids))
    if not new_ids:
        raise BookingRuleViolationError("a booking must keep at least one table")
    # Lock the full dependency set (old and new) before the clock and validation.
    lock_ids = sorted(current_ids | set(new_ids))
    halls, tables = await _lock_halls_tables(session, venue_id, lock_ids, live=is_open)
    by_id = {table.id: table for table in tables}
    now = await operation_now(session)
    if now >= booking.shift_ends_at:
        raise BookingInvalidStateError(
            "a previous-shift booking permits only close or guest-text edits"
        )
    if now >= booking.ends_at:
        raise BookingInvalidStateError("a booking past its plan end cannot change its tables")
    # Only newly added tables introduce a bookability question; kept ones were valid.
    if add_ids:
        _check_bookable([by_id[table_id] for table_id in add_ids], halls)
    new_start = max(now, booking.starts_at)
    occupancies = await _lock_active_occupancies(session, venue_id, booking_id)
    if add_ids:
        await _check_live_conflicts(
            session,
            venue_id,
            add_ids,
            new_start,
            booking.ends_at,
            now,
            business_date=booking.business_date if is_open else None,
            exclude_booking_id=booking_id,
        )
        conflicts = await _conflicting_booking_ids(
            session,
            venue_id,
            add_ids,
            new_start,
            booking.ends_at,
            exclude_booking_id=booking_id,
        )
        if conflicts:
            raise BookingConflictError(conflicts)
    # Capacity invariant over the resulting plan (§14); removed tables are
    # truncated at ``now`` so they no longer cover the future sub-intervals.
    removed_set = set(remove_ids)
    assigned = [
        (by_id[o.table_id].capacity, o.starts_at, o.ends_at)
        for o in occupancies
        if o.table_id in by_id and o.table_id not in removed_set
    ]
    assigned.extend((by_id[table_id].capacity, new_start, booking.ends_at) for table_id in add_ids)
    if not capacity_sufficient(
        party_size=booking.party_size,
        assigned=assigned,
        interval_start=new_start,
        interval_end=booking.ends_at,
    ):
        total = sum(by_id[table_id].capacity for table_id in new_ids)
        raise BookingRuleViolationError(
            f"insufficient capacity: party of {booking.party_size} exceeds the {total} seats "
            "of the selected table(s)"
        )
    if is_open:
        live_capacity = sum(by_id[table_id].capacity for table_id in new_ids)
        if live_capacity < booking.party_size:
            raise BookingRuleViolationError("insufficient live capacity for the party size")
    # Apply the plan delta.
    for occupancy in occupancies:
        if occupancy.table_id in removed_set:
            segment = truncate_segment_at(
                Segment(occupancy.starts_at, occupancy.ends_at, occupancy.is_active), now
            )
            occupancy.starts_at = segment.starts_at
            occupancy.ends_at = segment.ends_at
            occupancy.is_active = False
            occupancy.updated_at = now
    for table_id in add_ids:
        session.add(
            TableOccupancy(
                venue_id=venue_id,
                table_id=table_id,
                kind="BOOKING",
                booking_id=booking_id,
                starts_at=new_start,
                ends_at=booking.ends_at,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    # Apply the live delta for OPEN, keeping the live set in lockstep with the plan.
    if is_open:
        if remove_ids:
            await session.execute(
                delete(BookingLiveTable).where(
                    BookingLiveTable.venue_id == venue_id,
                    BookingLiveTable.booking_id == booking_id,
                    BookingLiveTable.table_id.in_(remove_ids),
                )
            )
        for table_id in add_ids:
            session.add(
                BookingLiveTable(
                    venue_id=venue_id,
                    booking_id=booking_id,
                    business_date=booking.business_date,
                    table_id=table_id,
                    live_since=now,
                )
            )
    if add_ids and remove_ids:
        event_type = "TABLE_REPLACED"
    elif add_ids:
        event_type = "TABLE_ADDED"
    else:
        event_type = "TABLE_REMOVED"
    booking.version += 1
    booking.updated_at = now
    session.add(
        _event(
            venue_id=venue_id,
            booking_id=booking_id,
            event_type=event_type,
            actor_type="ADMIN",
            admin_session_id=admin_session_id,
            created_at=now,
            payload={
                "from_table_ids": sorted(current_ids),
                "to_table_ids": new_ids,
                "table_ids": new_ids,
                "live": is_open,
            },
        )
    )
    await session.flush()
    await publish(session, venue_id=venue_id, event_type=BOOKING_UPDATED, ids=[booking_id])
    return booking


async def mutate_booking_tables(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    add_ids: list[int],
    remove_ids: list[int],
    admin_session_id: int | None,
) -> BookingView:
    """Add/remove tables atomically, mapping constraint races to error codes (§32.5).

    A caller supplies one of the three shapes: pure add, pure remove, or a
    replace/reseat (both lists). Every shape is one transaction, so a conflict on
    one table rolls the whole operation back and leaves the old assignment.
    """
    if not add_ids and not remove_ids:
        raise BookingRuleViolationError("no table change requested")
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.begin():
                booking = await _mutate_tables_once(
                    session,
                    venue_id=venue_id,
                    booking_id=booking_id,
                    expected_version=expected_version,
                    add_ids=add_ids,
                    remove_ids=remove_ids,
                    admin_session_id=admin_session_id,
                )
            return await view_of(session, booking)
        except DBAPIError as exc:
            state = _sqlstate(exc)
            if state in _RETRYABLE_SQLSTATES and attempt + 1 < MAX_ATTEMPTS:
                continue
            if state == "23P01":
                raise BookingConflictError([]) from exc
            if state == "23505" and "booking_live_tables" in str(exc.orig):
                raise TableLiveConflictError() from exc
            if state == _LOCK_TIMEOUT_SQLSTATE:
                raise ServiceUnavailableError() from exc
            raise
    raise ServiceUnavailableError()  # pragma: no cover - the loop always returns or raises


async def add_booking_tables(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    table_ids: list[int],
    admin_session_id: int | None,
) -> BookingView:
    """Add one or more tables to a booking (§20.1/§20.2)."""
    return await mutate_booking_tables(
        session,
        venue_id=venue_id,
        booking_id=booking_id,
        expected_version=expected_version,
        add_ids=list(table_ids),
        remove_ids=[],
        admin_session_id=admin_session_id,
    )


async def remove_booking_table(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    table_id: int,
    admin_session_id: int | None,
) -> BookingView:
    """Remove one table from a booking, keeping at least one seat (§20.5)."""
    return await mutate_booking_tables(
        session,
        venue_id=venue_id,
        booking_id=booking_id,
        expected_version=expected_version,
        add_ids=[],
        remove_ids=[table_id],
        admin_session_id=admin_session_id,
    )


async def replace_booking_tables(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    from_table_ids: list[int],
    to_table_ids: list[int],
    admin_session_id: int | None,
) -> BookingView:
    """Atomically replace/reseat a set of tables, `remove old + add new` (§20.6)."""
    return await mutate_booking_tables(
        session,
        venue_id=venue_id,
        booking_id=booking_id,
        expected_version=expected_version,
        add_ids=list(to_table_ids),
        remove_ids=list(from_table_ids),
        admin_session_id=admin_session_id,
    )


# --- list / history ---------------------------------------------------------


async def edit_booking_guest(
    session: AsyncSession,
    *,
    venue_id: int,
    booking_id: int,
    expected_version: int,
    changes: dict[str, Any],
    admin_session_id: int | None,
) -> BookingView:
    """Whitelist-only edit, serialized by booking and capacity resource locks (§27/33)."""
    allowed = {"guest_name", "guest_phone_raw", "guest_comment", "party_size"}
    if not changes or changes.keys() - allowed:
        raise BookingRuleViolationError("only guest fields may be edited")
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.begin():
                await session.get(Venue, venue_id)
                booking = await _lock_booking(session, venue_id, booking_id)
                if booking.version != expected_version:
                    raise BookingStaleError(booking_id)
                if booking.status not in ("NEW", "WAITING", "OPEN"):
                    raise BookingInvalidStateError("terminal bookings cannot be edited")
                name = changes.get("guest_name", booking.guest_name)
                phone = changes.get("guest_phone_raw", booking.guest_phone_raw)
                party = changes.get("party_size", booking.party_size)
                if not name or not name.strip() or party is None or party < 1:
                    raise BookingRuleViolationError("name and positive party_size are required")
                if booking.source in SOURCES_REQUIRING_PHONE and (not phone or not phone.strip()):
                    raise BookingRuleViolationError("phone is required for this source")
                if "party_size" in changes:
                    occupancies = list(
                        (
                            await session.scalars(
                                select(TableOccupancy).where(
                                    TableOccupancy.venue_id == venue_id,
                                    TableOccupancy.booking_id == booking_id,
                                    TableOccupancy.is_active.is_(True),
                                )
                            )
                        ).all()
                    )
                    live_ids = list(
                        (
                            await session.scalars(
                                select(BookingLiveTable.table_id).where(
                                    BookingLiveTable.venue_id == venue_id,
                                    BookingLiveTable.booking_id == booking_id,
                                )
                            )
                        ).all()
                    )
                    ids = sorted({o.table_id for o in occupancies} | set(live_ids))
                    _, tables = await _lock_halls_tables(session, venue_id, ids)
                now = await operation_now(session)
                if "party_size" in changes:
                    if (
                        booking.status == "OPEN"
                        and now >= booking.shift_ends_at
                        and party != booking.party_size
                    ):
                        raise BookingRuleViolationError(
                            "previous-shift OPEN permits only guest text edits"
                        )
                    capacities = {t.id: t.capacity for t in tables}
                    if (
                        now < booking.ends_at
                        and not capacity_sufficient(
                            party_size=party,
                            assigned=[
                                (capacities[o.table_id], o.starts_at, o.ends_at)
                                for o in occupancies
                            ],
                            interval_start=max(now, booking.starts_at),
                            interval_end=booking.ends_at,
                        )
                    ) or (
                        booking.status == "OPEN" and sum(capacities[i] for i in live_ids) < party
                    ):
                        raise BookingRuleViolationError("insufficient capacity for party_size")
                changes = {k: v for k, v in changes.items() if getattr(booking, k) != v}
                if "guest_name" in changes:
                    changes["guest_name"] = name.strip()
                for key, value in changes.items():
                    setattr(booking, key, value)
                if "guest_phone_raw" in changes:
                    booking.guest_phone_normalized = normalize_phone(phone) if phone else None
                booking.version += 1
                booking.updated_at = now
                session.add(
                    _event(
                        venue_id=venue_id,
                        booking_id=booking_id,
                        event_type="BOOKING_EDITED",
                        actor_type="ADMIN",
                        admin_session_id=admin_session_id,
                        created_at=now,
                        payload={"changed_fields": sorted(changes)},
                    )
                )
                await session.flush()
                await publish(
                    session,
                    venue_id=venue_id,
                    event_type=BOOKING_UPDATED,
                    ids=[booking_id],
                )
            return await view_of(session, booking)
        except DBAPIError as exc:
            state = _sqlstate(exc)
            if state in _RETRYABLE_SQLSTATES and attempt + 1 < MAX_ATTEMPTS:
                continue
            if state == _LOCK_TIMEOUT_SQLSTATE:
                raise ServiceUnavailableError() from exc
            raise
    raise ServiceUnavailableError()


async def list_bookings(
    session: AsyncSession,
    venue_id: int,
    *,
    business_date: date_type | None = None,
    status: str | None = None,
    source: str | None = None,
    table_id: int | None = None,
    phone: str | None = None,
    number: int | None = None,
    same_network_as: int | None = None,
    unresolved: bool = False,
    cursor: int | None = None,
    limit: int = 50,
) -> tuple[list[BookingView], int | None]:
    """Cursor/limit list with the §35 filters, newest first by ``id``."""
    query = select(Booking).where(Booking.venue_id == venue_id)
    if business_date is not None:
        query = query.where(Booking.business_date == business_date)
    if status is not None:
        query = query.where(Booking.status == status)
    if source is not None:
        query = query.where(Booking.source == source)
    if phone is not None:
        normalized = normalize_phone(phone)
        query = query.where(Booking.guest_phone_normalized == normalized if normalized else false())
    if number is not None:
        query = query.where(Booking.number == number)
    if same_network_as is not None:
        reference = await get_booking(session, venue_id, same_network_as)
        now = await operation_now(session)
        if (
            reference.source != "ONLINE"
            or not reference.request_ip_hmac
            or reference.request_ip_hmac_expires_at is None
            or reference.request_ip_hmac_expires_at <= now
        ):
            return [], None
        query = query.where(
            Booking.source == "ONLINE",
            Booking.request_ip_hmac == reference.request_ip_hmac,
            Booking.request_ip_hmac_expires_at > now,
        )
    if table_id is not None:
        query = query.where(
            Booking.id.in_(
                select(TableOccupancy.booking_id).where(
                    TableOccupancy.venue_id == venue_id,
                    TableOccupancy.table_id == table_id,
                    TableOccupancy.kind == "BOOKING",
                    TableOccupancy.is_active.is_(True),
                )
            )
        )
    if unresolved:
        now = await operation_now(session)
        query = query.where(Booking.status.in_(("NEW", "WAITING", "OPEN")), Booking.ends_at < now)
    if cursor is not None:
        query = query.where(Booking.id < cursor)
    query = query.order_by(Booking.id.desc()).limit(limit + 1)
    rows = list((await session.execute(query)).scalars().all())
    next_cursor: int | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = rows[-1].id
    return await views_of(session, venue_id, rows), next_cursor
