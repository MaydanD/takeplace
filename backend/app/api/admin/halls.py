"""Admin halls and tables endpoints (PROJECT-SPEC §6.4-6.5, §29, §35).

Everything is scoped to the venue resolved from the session cookie; there is no
``venue_id`` parameter, so one tenant can never read or mutate another's halls,
tables or layout. The canvas is read-only here — the visual editor and its
layout-save arrive in Stage 10.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.dependencies import AuthContextDep, SessionDep, require_trusted_origin
from app.api.admin.schemas import (
    HallCreate,
    HallDetail,
    HallsResponse,
    HallSummary,
    HallUpdate,
    LayoutSaveRequest,
    TablesResponse,
    TableSummary,
    TableUpdate,
)
from app.api.errors import (
    capacity_change_blocked,
    hall_archive_blocked,
    layout_invalid,
    layout_stale,
    not_found,
    table_archive_blocked,
    table_number_taken,
)
from app.db.models import Hall, Table
from app.domain.layout import LayoutValidationError, validate_layout_save
from app.realtime.events import RESYNC, publish
from app.services.errors import (
    CapacityChangeBlockedError,
    HallArchiveBlockedError,
    HallNotFoundError,
    LayoutStaleError,
    TableArchiveBlockedError,
    TableNotFoundError,
    TableNumberTakenError,
)
from app.services.halls import (
    archive_hall,
    archive_table,
    create_hall,
    get_hall,
    list_halls,
    list_tables,
    save_layout,
    set_table_bookable,
    update_hall,
)

router = APIRouter(tags=["admin-halls"])


@contextmanager
def _translating_errors() -> Iterator[None]:
    try:
        yield
    except LayoutValidationError as exc:
        raise layout_invalid(exc.message) from exc
    except HallNotFoundError as exc:
        raise not_found("HALL_NOT_FOUND", str(exc)) from exc
    except TableNotFoundError as exc:
        raise not_found("TABLE_NOT_FOUND", str(exc)) from exc
    except HallArchiveBlockedError as exc:
        raise hall_archive_blocked(str(exc)) from exc
    except TableArchiveBlockedError as exc:
        raise table_archive_blocked(str(exc)) from exc
    except TableNumberTakenError as exc:
        raise table_number_taken(str(exc)) from exc
    except CapacityChangeBlockedError as exc:
        raise capacity_change_blocked(str(exc), exc.affected) from exc
    except LayoutStaleError as exc:
        raise layout_stale(
            str(exc), expected_revision=exc.expected, layout_revision=exc.actual
        ) from exc


async def _table_counts(session: AsyncSession, venue_id: int) -> dict[int, int]:
    """Count non-archived tables per hall."""
    rows = await session.execute(
        select(Table.hall_id, func.count())
        .where(Table.venue_id == venue_id, Table.archived_at.is_(None))
        .group_by(Table.hall_id)
    )
    return {hall_id: int(count) for hall_id, count in rows.all()}


def _summaries(halls: list[Hall], counts: dict[int, int]) -> HallsResponse:
    return HallsResponse(
        halls=[HallSummary.from_model(hall, table_count=counts.get(hall.id, 0)) for hall in halls]
    )


@router.get("/halls", response_model=HallsResponse)
async def get_halls(
    context: AuthContextDep,
    session: SessionDep,
    include_archived: Annotated[bool, Query()] = False,
) -> HallsResponse:
    """List this venue's halls (archived halls are hidden unless requested)."""
    halls = await list_halls(session, context.venue_id, include_archived=include_archived)
    return _summaries(halls, await _table_counts(session, context.venue_id))


@router.post(
    "/halls",
    response_model=HallSummary,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_trusted_origin)],
)
async def post_hall(
    payload: HallCreate, context: AuthContextDep, session: SessionDep
) -> HallSummary:
    """Create a hall (canvas) for this venue."""
    with _translating_errors():
        hall = await create_hall(
            session,
            context.venue_id,
            name=payload.name,
            canvas_width=payload.canvas_width,
            canvas_height=payload.canvas_height,
            is_bookable=payload.is_bookable,
        )
    await publish(session, venue_id=context.venue_id, event_type=RESYNC)
    await session.commit()
    return HallSummary.from_model(hall, table_count=0)


@router.get("/halls/{hall_id}", response_model=HallDetail)
async def get_hall_detail(hall_id: int, context: AuthContextDep, session: SessionDep) -> HallDetail:
    """Return one hall with its tables (including archived) and static elements."""
    with _translating_errors():
        hall = await get_hall(session, context.venue_id, hall_id)
    tables = await list_tables(session, context.venue_id, hall_id=hall.id, include_archived=True)
    return HallDetail.from_model(hall, tables)


@router.patch(
    "/halls/{hall_id}",
    response_model=HallSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def patch_hall(
    hall_id: int,
    payload: HallUpdate,
    context: AuthContextDep,
    session: SessionDep,
) -> HallSummary:
    """Update a hall's name, canvas size or operational bookability.

    A canvas-size change is a layout-owned edit and bumps ``layout_revision``;
    ``is_bookable`` never does (§31).
    """
    changes = payload.model_dump(exclude_unset=True)
    with _translating_errors():
        hall = await update_hall(session, context.venue_id, hall_id, changes=changes)
    await publish(session, venue_id=context.venue_id, event_type=RESYNC)
    await session.commit()
    counts = await _table_counts(session, context.venue_id)
    return HallSummary.from_model(hall, table_count=counts.get(hall.id, 0))


@router.post(
    "/halls/{hall_id}/archive",
    response_model=HallSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def post_archive_hall(
    hall_id: int, context: AuthContextDep, session: SessionDep
) -> HallSummary:
    """Archive a hall; blocked while it still has non-archived tables (§29.5)."""
    with _translating_errors():
        hall = await archive_hall(session, context.venue_id, hall_id)
    await publish(session, venue_id=context.venue_id, event_type=RESYNC)
    await session.commit()
    return HallSummary.from_model(hall, table_count=0)


@router.put(
    "/halls/{hall_id}/layout",
    response_model=HallDetail,
    dependencies=[Depends(require_trusted_origin)],
)
async def put_hall_layout(
    hall_id: int,
    payload: LayoutSaveRequest,
    context: AuthContextDep,
    session: SessionDep,
) -> HallDetail:
    """Apply a full-state editor layout-save for one hall (PROJECT-SPEC §31).

    The payload carries ``expected_revision`` plus the complete editor-owned
    state (canvas size, tables, static elements). A stale revision returns
    ``409 LAYOUT_STALE`` without writing anything; the commit also emits a
    ``resync`` realtime signal, so a second admin device refetches instead of
    silently overwriting the winner.
    """
    raw = payload.model_dump(mode="json")
    with _translating_errors():
        validated = validate_layout_save(raw)
        hall = await save_layout(session, context.venue_id, hall_id, validated)
    await publish(session, venue_id=context.venue_id, event_type=RESYNC)
    await session.commit()
    with _translating_errors():
        hall = await get_hall(session, context.venue_id, hall_id)
        tables = await list_tables(session, context.venue_id, hall_id=hall_id)
    return HallDetail.from_model(hall, tables)


@router.get("/tables", response_model=TablesResponse)
async def get_tables(
    context: AuthContextDep,
    session: SessionDep,
    hall_id: Annotated[int | None, Query()] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> TablesResponse:
    """Flat list of this venue's tables for the list view, with hall names."""
    tables = await list_tables(
        session, context.venue_id, hall_id=hall_id, include_archived=include_archived
    )
    names = {
        hall.id: hall.name
        for hall in await list_halls(session, context.venue_id, include_archived=True)
    }
    return TablesResponse(
        tables=[TableSummary.from_model(t, hall_name=names.get(t.hall_id)) for t in tables]
    )


@router.patch(
    "/tables/{table_id}",
    response_model=TableSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def patch_table(
    table_id: int,
    payload: TableUpdate,
    context: AuthContextDep,
    session: SessionDep,
) -> TableSummary:
    """Operational ``is_bookable`` toggle; geometry is owned by layout-save (§35)."""
    with _translating_errors():
        table = await set_table_bookable(
            session, context.venue_id, table_id, is_bookable=payload.is_bookable
        )
    await publish(session, venue_id=context.venue_id, event_type=RESYNC)
    await session.commit()
    return TableSummary.from_model(table)


@router.post(
    "/tables/{table_id}/archive",
    response_model=TableSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def post_archive_table(
    table_id: int, context: AuthContextDep, session: SessionDep
) -> TableSummary:
    """Archive a table; the live/future-occupancy guard lands with Stage 5 (§29.3)."""
    with _translating_errors():
        table = await archive_table(session, context.venue_id, table_id)
    await publish(session, venue_id=context.venue_id, event_type=RESYNC)
    await session.commit()
    return TableSummary.from_model(table)
