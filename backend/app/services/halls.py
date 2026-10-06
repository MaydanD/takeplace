"""Halls and tables services (PROJECT-SPEC §6.4-6.5, §29, §32.4).

Single-object mutations lock the hall/table row ``FOR UPDATE``; the bulk JSON
import additionally takes the per-venue *layout* advisory lock so it serialises
with a future layout-save (§31, §32.4). Tenant scoping is always by ``venue_id``
from the authenticated context — an object of another venue is a 404, never a
silent cross-tenant edit (§7.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.locks import acquire_layout_lock
from app.db.models import Booking, BookingLiveTable, Hall, Table, TableOccupancy, Venue
from app.db.time import operation_now
from app.domain.booking import capacity_sufficient
from app.domain.layout import (
    DEFAULT_CANVAS_HEIGHT,
    DEFAULT_CANVAS_WIDTH,
    DEFAULT_HALL_NAME,
    HallSpec,
    LayoutImport,
    LayoutSave,
    TableSpec,
    parse_static_elements,
)
from app.domain.schedule import current_business_date
from app.domain.timezone import load_timezone
from app.services.errors import (
    CapacityChangeBlockedError,
    HallArchiveBlockedError,
    HallNotFoundError,
    LayoutStaleError,
    TableArchiveBlockedError,
    TableNotFoundError,
)
from app.services.schedule import load_schedule_table


def _dec(value: float | int) -> Decimal:
    return Decimal(str(value))


async def create_hall(
    session: AsyncSession,
    venue_id: int,
    *,
    name: str,
    canvas_width: int = DEFAULT_CANVAS_WIDTH,
    canvas_height: int = DEFAULT_CANVAS_HEIGHT,
    is_bookable: bool = True,
    static_elements: list[dict[str, object]] | None = None,
) -> Hall:
    """Create a hall (canvas) for a venue."""
    now = await operation_now(session)
    hall = Hall(
        venue_id=venue_id,
        name=name,
        is_bookable=is_bookable,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        layout_revision=1,
        static_elements=static_elements or [],
        created_at=now,
        updated_at=now,
    )
    session.add(hall)
    await session.flush()
    return hall


async def create_default_hall(session: AsyncSession, venue_id: int) -> Hall:
    """Create the onboarding default hall for a new venue (§53)."""
    return await create_hall(
        session,
        venue_id,
        name=DEFAULT_HALL_NAME,
        canvas_width=DEFAULT_CANVAS_WIDTH,
        canvas_height=DEFAULT_CANVAS_HEIGHT,
    )


async def list_halls(
    session: AsyncSession, venue_id: int, *, include_archived: bool = False
) -> list[Hall]:
    """List a venue's halls ordered by id (the display order the spec implies)."""
    query = select(Hall).where(Hall.venue_id == venue_id)
    if not include_archived:
        query = query.where(Hall.archived_at.is_(None))
    result = await session.execute(query.order_by(Hall.id))
    return list(result.scalars().all())


async def get_hall(
    session: AsyncSession, venue_id: int, hall_id: int, *, for_update: bool = False
) -> Hall:
    """Load one hall scoped to the venue, or raise ``HallNotFoundError``."""
    query = select(Hall).where(Hall.id == hall_id, Hall.venue_id == venue_id)
    if for_update:
        query = query.with_for_update()
    hall = (await session.execute(query)).scalar_one_or_none()
    if hall is None:
        raise HallNotFoundError(hall_id)
    return hall


async def get_table(
    session: AsyncSession, venue_id: int, table_id: int, *, for_update: bool = False
) -> Table:
    """Load one table scoped to the venue, or raise ``TableNotFoundError``."""
    query = select(Table).where(Table.id == table_id, Table.venue_id == venue_id)
    if for_update:
        query = query.with_for_update()
    table = (await session.execute(query)).scalar_one_or_none()
    if table is None:
        raise TableNotFoundError(table_id)
    return table


async def list_tables(
    session: AsyncSession,
    venue_id: int,
    *,
    hall_id: int | None = None,
    include_archived: bool = False,
) -> list[Table]:
    """List a venue's tables (optionally one hall), ordered by hall then z-index."""
    query = select(Table).where(Table.venue_id == venue_id)
    if hall_id is not None:
        query = query.where(Table.hall_id == hall_id)
    if not include_archived:
        query = query.where(Table.archived_at.is_(None))
    result = await session.execute(query.order_by(Table.hall_id, Table.z_index, Table.id))
    return list(result.scalars().all())


async def update_hall(
    session: AsyncSession,
    venue_id: int,
    hall_id: int,
    *,
    changes: dict[str, object],
) -> Hall:
    """Apply a PATCH to a hall.

    Only the fields the client sent are applied. Changing the canvas size is a
    layout-owned edit and bumps ``layout_revision``; ``is_bookable`` is an
    operational flag and never bumps it (§31, §32.4).
    """
    hall = await get_hall(session, venue_id, hall_id, for_update=True)
    now = await operation_now(session)
    layout_changed = False

    for field in ("name", "canvas_width", "canvas_height", "is_bookable"):
        if field not in changes:
            continue
        value = changes[field]
        if field in ("canvas_width", "canvas_height") and value != getattr(hall, field):
            layout_changed = True
        setattr(hall, field, value)

    if layout_changed:
        hall.layout_revision += 1
    hall.updated_at = now
    await session.flush()
    return hall


async def archive_hall(session: AsyncSession, venue_id: int, hall_id: int) -> Hall:
    """Archive a hall, refusing while it still has non-archived tables (§29.5)."""
    hall = await get_hall(session, venue_id, hall_id, for_update=True)
    if hall.archived_at is not None:
        return hall
    active_tables = (
        await session.execute(
            select(func.count())
            .select_from(Table)
            .where(Table.hall_id == hall.id, Table.archived_at.is_(None))
        )
    ).scalar_one()
    if int(active_tables) > 0:
        raise HallArchiveBlockedError(int(active_tables))
    now = await operation_now(session)
    hall.archived_at = now
    hall.updated_at = now
    await session.flush()
    return hall


async def set_table_bookable(
    session: AsyncSession, venue_id: int, table_id: int, *, is_bookable: bool
) -> Table:
    """Operational ``is_bookable`` toggle; never changes geometry or revision (§35)."""
    table = await get_table(session, venue_id, table_id, for_update=True)
    table.is_bookable = is_bookable
    table.updated_at = await operation_now(session)
    await session.flush()
    return table


async def _guard_table_archive(
    session: AsyncSession, venue_id: int, table: Table, now: datetime
) -> None:
    """Raise ``TableArchiveBlockedError`` if the table cannot be archived (§29.3).

    The caller must already hold the table row ``FOR UPDATE`` so the
    "no bookings -> archive" window cannot be raced (§54.9).
    """
    future_bookings = (
        await session.execute(
            select(func.count())
            .select_from(TableOccupancy)
            .where(
                TableOccupancy.venue_id == venue_id,
                TableOccupancy.table_id == table.id,
                TableOccupancy.kind == "BOOKING",
                TableOccupancy.is_active.is_(True),
                TableOccupancy.ends_at > now,
            )
        )
    ).scalar_one()
    if int(future_bookings) > 0:
        raise TableArchiveBlockedError(
            f"it still has {int(future_bookings)} future active booking occupancy(ies)"
        )
    venue = await session.get(Venue, venue_id)
    if venue is not None:
        schedule = await load_schedule_table(session, venue_id)
        day = current_business_date(now, schedule, load_timezone(venue.timezone))
        if (
            await session.scalar(
                select(BookingLiveTable.id)
                .where(
                    BookingLiveTable.venue_id == venue_id,
                    BookingLiveTable.table_id == table.id,
                    BookingLiveTable.business_date == day,
                )
                .limit(1)
            )
            is not None
        ):
            raise TableArchiveBlockedError("it still has guests in the current business day")


async def archive_table(session: AsyncSession, venue_id: int, table_id: int) -> Table:
    """Archive a table, occupancy-aware (§29.3).

    Forbidden while the table has a live row of the current business day (§29.3;
    live rows are a later stage) or a future active BOOKING occupancy. Archiving
    takes the table ``FOR UPDATE``, which serialises with a booking create's
    ``FOR SHARE``: the "no bookings -> archive" window cannot be raced (§54.9).
    Active BLOCK rows are deactivated rather than deleted.
    """
    table = await get_table(session, venue_id, table_id, for_update=True)
    if table.archived_at is not None:
        return table
    now = await operation_now(session)
    await _guard_table_archive(session, venue_id, table, now)
    await session.execute(
        update(TableOccupancy)
        .where(
            TableOccupancy.venue_id == venue_id,
            TableOccupancy.table_id == table_id,
            TableOccupancy.kind == "BLOCK",
            TableOccupancy.is_active.is_(True),
        )
        .values(is_active=False, updated_at=now)
    )
    table.archived_at = now
    table.updated_at = now
    await session.flush()
    return table


async def save_layout(
    session: AsyncSession, venue_id: int, hall_id: int, payload: LayoutSave
) -> Hall:
    """Apply a full-state editor layout-save for one hall (PROJECT-SPEC §31).

    Ordering (§32.4): venue-scoped exclusive layout advisory lock (serialises
    cross-hall capacity write skew), then ``hall FOR UPDATE``, then all live
    tables of the hall ``FOR UPDATE`` ordered by id, then ``operation_now``,
    then validation, then writes.

    Identity is by ``id``: ``id=None`` creates a table, a known ``id`` updates
    it in place, and a live table missing from the payload is archived in place
    (occupancy/history rows keep their FK, §29.3). Operational ``is_bookable``
    is never touched: it is not part of the editor state.

    A no-op payload (identical canvas, tables and static elements) does not
    bump ``layout_revision``. A stale ``expected_revision`` raises
    ``LayoutStaleError`` (``409 LAYOUT_STALE``) without writing anything.
    """
    await acquire_layout_lock(session, venue_id)
    hall = await get_hall(session, venue_id, hall_id, for_update=True)
    if hall.archived_at is not None:
        raise HallNotFoundError(hall_id)
    if hall.layout_revision != payload.expected_revision:
        raise LayoutStaleError(hall_id, payload.expected_revision, hall.layout_revision)

    existing = {
        table.id: table
        for table in (
            await session.execute(
                select(Table)
                .where(
                    Table.hall_id == hall.id,
                    Table.venue_id == venue_id,
                    Table.archived_at.is_(None),
                )
                .order_by(Table.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    }
    for spec in payload.tables:
        if spec.id is not None and spec.id not in existing:
            raise TableNotFoundError(spec.id)

    now = await operation_now(session)
    canvas_changed = (
        hall.canvas_width != payload.canvas_width or hall.canvas_height != payload.canvas_height
    )
    static_elements = parse_static_elements(
        [element.model_dump(mode="json") for element in payload.static_elements]
    )
    static_changed = hall.static_elements != static_elements

    wanted_ids = {spec.id for spec in payload.tables if spec.id is not None}
    to_archive = [table for table_id, table in existing.items() if table_id not in wanted_ids]

    for table in to_archive:
        await _guard_table_archive(session, venue_id, table, now)

    tables_changed = False
    for spec in payload.tables:
        if spec.id is None:
            session.add(
                Table(
                    venue_id=venue_id,
                    hall_id=hall.id,
                    number=spec.number,
                    capacity=spec.capacity,
                    shape=spec.shape,
                    x=_dec(spec.x),
                    y=_dec(spec.y),
                    width=_dec(spec.width),
                    height=_dec(spec.height),
                    rotation=_dec(spec.rotation),
                    z_index=spec.z_index,
                    is_bookable=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            tables_changed = True
        else:
            table = existing[spec.id]
            values = {
                "number": spec.number,
                "capacity": spec.capacity,
                "shape": spec.shape,
                "x": _dec(spec.x),
                "y": _dec(spec.y),
                "width": _dec(spec.width),
                "height": _dec(spec.height),
                "rotation": _dec(spec.rotation),
                "z_index": spec.z_index,
            }
            if any(getattr(table, field) != value for field, value in values.items()):
                for field, value in values.items():
                    setattr(table, field, value)
                table.updated_at = now
                tables_changed = True

    for table in to_archive:
        await session.execute(
            update(TableOccupancy)
            .where(
                TableOccupancy.venue_id == venue_id,
                TableOccupancy.table_id == table.id,
                TableOccupancy.kind == "BLOCK",
                TableOccupancy.is_active.is_(True),
            )
            .values(is_active=False, updated_at=now)
        )
        table.archived_at = now
        table.updated_at = now
        tables_changed = True

    await session.flush()
    if tables_changed:
        # §29.4: the capacities just written (archived tables count as 0) must
        # not strand an existing future booking. The table row locks above
        # serialise this with a concurrent booking create.
        affected = await _capacity_breakers(session, venue_id, now)
        if affected:
            raise CapacityChangeBlockedError(affected)

    if canvas_changed:
        hall.canvas_width = payload.canvas_width
        hall.canvas_height = payload.canvas_height
    if static_changed:
        hall.static_elements = static_elements

    if canvas_changed or static_changed or tables_changed:
        hall.layout_revision += 1
    hall.updated_at = now
    await session.flush()
    return hall


async def _capacity_breakers(
    session: AsyncSession, venue_id: int, now: datetime
) -> list[dict[str, object]]:
    """Return future NEW/WAITING bookings a capacity change would strand (§29.4).

    A booking is stranded when the seats of its currently assigned tables (using
    the capacities just written in this transaction, an archived table counting
    as 0) fall below its party size. The caller holds the table row locks from
    its UPDATE, so a concurrent booking create is serialised behind this check.
    """
    rows = await session.execute(
        select(Booking.id, TableOccupancy.table_id)
        .join(Booking, Booking.id == TableOccupancy.booking_id)
        .where(
            TableOccupancy.venue_id == venue_id,
            TableOccupancy.kind == "BOOKING",
            TableOccupancy.is_active.is_(True),
            TableOccupancy.ends_at > now,
            Booking.status.in_(("NEW", "WAITING", "OPEN")),
        )
    )
    by_booking: dict[int, set[int]] = {}
    for booking_id, table_id in rows.all():
        by_booking.setdefault(int(booking_id), set()).add(int(table_id))
    live_by_booking: dict[int, set[int]] = {}
    live_rows = (
        await session.execute(
            select(BookingLiveTable.booking_id, BookingLiveTable.table_id).where(
                BookingLiveTable.venue_id == venue_id
            )
        )
    ).all()
    for booking_id, table_id in live_rows:
        live_by_booking.setdefault(booking_id, set()).add(table_id)
        by_booking.setdefault(booking_id, set())
    if not by_booking:
        return []

    capacities = {
        int(table_id): (int(capacity) if archived_at is None else 0)
        for table_id, capacity, archived_at in (
            await session.execute(
                select(Table.id, Table.capacity, Table.archived_at).where(
                    Table.venue_id == venue_id
                )
            )
        ).all()
    }
    bookings = {
        booking.id: booking
        for booking in (
            await session.execute(
                select(Booking).where(
                    Booking.id.in_(list(by_booking)), Booking.venue_id == venue_id
                )
            )
        )
        .scalars()
        .all()
    }
    affected: list[dict[str, object]] = []
    for booking_id, _table_ids in sorted(by_booking.items()):
        booking = bookings.get(booking_id)
        if booking is None:
            continue
        segments = (
            await session.scalars(
                select(TableOccupancy).where(
                    TableOccupancy.venue_id == venue_id,
                    TableOccupancy.booking_id == booking_id,
                    TableOccupancy.is_active.is_(True),
                )
            )
        ).all()
        plan_ok = now >= booking.ends_at or capacity_sufficient(
            party_size=booking.party_size,
            assigned=[(capacities.get(o.table_id, 0), o.starts_at, o.ends_at) for o in segments],
            interval_start=max(now, booking.starts_at),
            interval_end=booking.ends_at,
        )
        live_ok = (
            booking.status != "OPEN"
            or sum(capacities.get(i, 0) for i in live_by_booking.get(booking_id, set()))
            >= booking.party_size
        )
        if not plan_ok or not live_ok:
            affected.append(
                {
                    "id": booking.id,
                    "number": booking.number,
                    "business_date": booking.business_date.isoformat(),
                    "starts_at": booking.starts_at.isoformat(),
                    "ends_at": booking.ends_at.isoformat(),
                }
            )
    return affected


@dataclass(slots=True)
class ImportResult:
    """Summary of a JSON/CLI layout import."""

    halls_created: int = 0
    halls_updated: int = 0
    tables_created: int = 0
    tables_updated: int = 0
    # Internal: whether any existing table capacity changed, which triggers the
    # §29.4 future-booking capacity guard. Never serialised.
    capacity_changed: bool = False

    def to_dict(self) -> dict[str, int]:
        return {
            "halls_created": self.halls_created,
            "halls_updated": self.halls_updated,
            "tables_created": self.tables_created,
            "tables_updated": self.tables_updated,
        }


_TABLE_GEOMETRY_FIELDS = ("number", "capacity", "shape", "x", "y", "width", "height", "rotation")


def _table_values(spec: TableSpec) -> dict[str, object]:
    return {
        "number": spec.number,
        "capacity": spec.capacity,
        "shape": spec.shape,
        "x": _dec(spec.x),
        "y": _dec(spec.y),
        "width": _dec(spec.width),
        "height": _dec(spec.height),
        "rotation": _dec(spec.rotation),
        "z_index": spec.z_index,
        "is_bookable": spec.is_bookable,
    }


def _table_differs(table: Table, values: dict[str, object]) -> bool:
    return any(getattr(table, field) != values[field] for field in _TABLE_GEOMETRY_FIELDS) or (
        table.z_index != values["z_index"] or table.is_bookable != values["is_bookable"]
    )


async def _apply_table(
    session: AsyncSession,
    *,
    venue_id: int,
    hall_id: int,
    spec: TableSpec,
    existing: Table | None,
    now: datetime,
    result: ImportResult,
) -> bool:
    values = _table_values(spec)
    if existing is None:
        session.add(
            Table(venue_id=venue_id, hall_id=hall_id, created_at=now, updated_at=now, **values)
        )
        result.tables_created += 1
        return True
    if not _table_differs(existing, values):
        return False
    if existing.capacity != values["capacity"]:
        result.capacity_changed = True
    for field, value in values.items():
        setattr(existing, field, value)
    existing.updated_at = now
    result.tables_updated += 1
    return True


async def _import_hall(
    session: AsyncSession,
    *,
    venue_id: int,
    spec: HallSpec,
    existing: Hall | None,
    now: datetime,
    result: ImportResult,
) -> None:
    static_elements = spec.static_elements_json()
    if existing is None:
        hall = await create_hall(
            session,
            venue_id,
            name=spec.name,
            canvas_width=spec.canvas_width,
            canvas_height=spec.canvas_height,
            is_bookable=spec.is_bookable,
            static_elements=static_elements,
        )
        result.halls_created += 1
        hall_changed = True
    else:
        hall = existing
        hall_changed = False
        if hall.canvas_width != spec.canvas_width or hall.canvas_height != spec.canvas_height:
            hall.canvas_width = spec.canvas_width
            hall.canvas_height = spec.canvas_height
            hall_changed = True
        if hall.is_bookable != spec.is_bookable:
            hall.is_bookable = spec.is_bookable
            hall_changed = True
        if hall.static_elements != static_elements:
            hall.static_elements = static_elements
            hall_changed = True

    existing_tables = {
        table.number: table
        for table in (
            await session.execute(
                select(Table).where(
                    Table.hall_id == hall.id,
                    Table.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    for table_spec in spec.tables:
        changed = await _apply_table(
            session,
            venue_id=venue_id,
            hall_id=hall.id,
            spec=table_spec,
            existing=existing_tables.get(table_spec.number),
            now=now,
            result=result,
        )
        hall_changed = hall_changed or changed

    if existing is not None:
        if hall_changed:
            hall.layout_revision += 1
            result.halls_updated += 1
        hall.updated_at = now
    await session.flush()


async def import_layout(session: AsyncSession, venue_id: int, layout: LayoutImport) -> ImportResult:
    """Upsert a validated layout for one venue.

    Semantic contract: halls are matched by ``name`` and tables by ``number``
    within their hall (both among non-archived rows). Nothing is deleted, and a
    repeat of the same file is idempotent — it re-applies the same values. The
    payload must already have passed ``validate_layout_import``; the caller owns
    the transaction, so a later failure rolls the whole import back.
    """
    await acquire_layout_lock(session, venue_id)
    now = await operation_now(session)
    result = ImportResult()

    existing_halls = {
        hall.name: hall
        for hall in (
            await session.execute(
                select(Hall).where(Hall.venue_id == venue_id, Hall.archived_at.is_(None))
            )
        )
        .scalars()
        .all()
    }
    for hall_spec in layout.halls:
        await _import_hall(
            session,
            venue_id=venue_id,
            spec=hall_spec,
            existing=existing_halls.get(hall_spec.name),
            now=now,
            result=result,
        )
    if result.capacity_changed:
        # §29.4: a capacity decrease must not break an existing future booking.
        # The capacity UPDATEs already hold the table row locks, so a booking
        # create either committed before this check or waits for our commit.
        affected = await _capacity_breakers(session, venue_id, now)
        if affected:
            raise CapacityChangeBlockedError(affected)
    return result
