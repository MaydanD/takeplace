"""PostgreSQL concurrency suite for the booking core (PROJECT-SPEC §54).

This is the Stage 5 acceptance gate: it runs **real parallel transactions**
against PostgreSQL (no SQLite, no sequential "concurrency") and proves that
double booking is technically impossible and that the canonical lock order does
not produce avoidable deadlocks.

Each task owns its own ``AsyncSession``/connection and calls the service layer
directly, so the transactions genuinely overlap on the server.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from app.db.models import Booking
from app.domain.layout import validate_layout_import
from app.domain.schedule import ScheduleRule
from app.services.bookings import (
    AdminBookingInput,
    cancel_booking,
    change_booking_time,
    create_admin_booking,
)
from app.services.errors import (
    BookingConflictError,
    BookingInvalidStateError,
    BookingStaleError,
    TableArchiveBlockedError,
    TableNotBookableError,
)
from app.services.halls import archive_table, import_layout
from app.services.schedule import upsert_exception
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.support import APP_URL, ORIGIN, cookie_header
from tests.integration.test_bookings_api import MSK, Venue

pytestmark = pytest.mark.integration

HMAC_KEY = "test-idempotency-key-0123456789"


@pytest.fixture(autouse=True)
def _require_test_db() -> None:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    from tests.integration.support import make_client

    with make_client() as client:
        yield client


@pytest.fixture
async def engine():
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    eng = create_async_engine(APP_URL, pool_size=10, max_overflow=10)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


# --- helpers ----------------------------------------------------------------


def _at(venue: Venue, hour: int, minute: int = 0, day_offset: int = 0) -> datetime:
    day = venue.business_date + timedelta(days=day_offset)
    return datetime.combine(day, datetime.min.time(), tzinfo=MSK).replace(hour=hour, minute=minute)


def _data(
    venue: Venue,
    *,
    start: datetime,
    end: datetime,
    tables: list[int] | None = None,
    party: int = 2,
) -> AdminBookingInput:
    return AdminBookingInput(
        starts_at=start,
        ends_at=end,
        table_ids=tables if tables is not None else [venue.table_ids[0]],
        party_size=party,
        source="PHONE",
        guest_name="Гость",
        guest_phone_raw="+79990000000",
    )


async def _create(factory, venue: Venue, data: AdminBookingInput, key: str | None = None):
    async with factory() as session:
        view, _created = await create_admin_booking(
            session,
            venue_id=venue.venue_id,
            data=data,
            idempotency_key=key or str(uuid.uuid4()),
            hmac_key=HMAC_KEY,
            admin_session_id=None,
        )
        return view


def _results(values: list) -> tuple[list, list]:
    ok = [v for v in values if not isinstance(v, BaseException)]
    errors = [v for v in values if isinstance(v, BaseException)]
    return ok, errors


async def _scalar(session: AsyncSession, sql: str, **params):
    return (await session.execute(text(sql), params)).scalar_one()


# --- 54.1 same slot ---------------------------------------------------------


async def test_same_slot_exactly_one_succeeds(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    data = _data(venue, start=_at(venue, 20), end=_at(venue, 22))
    results = await asyncio.gather(
        *[_create(session_factory, venue, data) for _ in range(4)], return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert all(isinstance(e, BookingConflictError) for e in errors), errors

    async with session_factory() as session:
        bookings = await _scalar(
            session, "SELECT count(*) FROM bookings WHERE venue_id = :v", v=venue.venue_id
        )
        occupancies = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE venue_id = :v AND is_active",
            v=venue.venue_id,
        )
        events = await _scalar(
            session,
            "SELECT count(*) FROM booking_events WHERE venue_id = :v AND event_type='BOOKING_CREATED'",
            v=venue.venue_id,
        )
    assert bookings == 1
    assert occupancies == 1
    assert events == 1


# --- 54.4 / 54.5 adjacency and overlap --------------------------------------


async def test_adjacent_intervals_both_succeed(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    a = _data(venue, start=_at(venue, 20), end=_at(venue, 22))
    b = _data(venue, start=_at(venue, 22), end=_at(venue, 23, 55))
    results = await asyncio.gather(
        _create(session_factory, venue, a),
        _create(session_factory, venue, b),
        return_exceptions=True,
    )
    ok, errors = _results(results)
    assert len(ok) == 2, errors


async def test_overlap_one_conflict(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    a = _data(venue, start=_at(venue, 20), end=_at(venue, 22))
    b = _data(venue, start=_at(venue, 21, 55), end=_at(venue, 23))
    results = await asyncio.gather(
        _create(session_factory, venue, a),
        _create(session_factory, venue, b),
        return_exceptions=True,
    )
    ok, errors = _results(results)
    assert len(ok) == 1
    assert isinstance(errors[0], BookingConflictError)


# --- 54.2 / 54.14a idempotency ----------------------------------------------


async def test_concurrent_idempotent_duplicate_creates_one(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    data = _data(venue, start=_at(venue, 20), end=_at(venue, 22))
    key = str(uuid.uuid4())
    results = await asyncio.gather(
        *[_create(session_factory, venue, data, key) for _ in range(4)], return_exceptions=True
    )
    ok, errors = _results(results)
    assert not errors, errors
    assert len({view.booking.id for view in ok}) == 1
    async with session_factory() as session:
        assert (
            await _scalar(
                session, "SELECT count(*) FROM bookings WHERE venue_id=:v", v=venue.venue_id
            )
            == 1
        )
        assert (
            await _scalar(
                session,
                "SELECT count(*) FROM table_occupancies WHERE venue_id=:v",
                v=venue.venue_id,
            )
            == 1
        )
        assert (
            await _scalar(
                session, "SELECT count(*) FROM booking_events WHERE venue_id=:v", v=venue.venue_id
            )
            == 1
        )


async def test_replay_after_schedule_change_returns_same_identity(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    """A lost response replayed after the schedule changed still returns it (§54.2)."""
    venue = Venue(api_client, tmp_path, tables=1)
    data = _data(venue, start=_at(venue, 20), end=_at(venue, 22))
    key = str(uuid.uuid4())
    first = await _create(session_factory, venue, data, key)
    # Shrink the schedule with explicit confirmation (the booking is now outside it).
    api_client.put(
        f"/api/admin/v1/schedule/exceptions/{venue.business_date.isoformat()}?confirm=true",
        json={"is_closed": False, "open_time": "16:00", "close_time": "21:00"},
        headers={**cookie_header(venue.token), "Origin": ORIGIN},
    )
    replay = await _create(session_factory, venue, data, key)
    assert replay.booking.id == first.booking.id


# --- 54.6 booking vs block --------------------------------------------------


async def test_booking_vs_block(api_client: TestClient, tmp_path: Path, session_factory) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    start, end = _at(venue, 20), _at(venue, 22)
    data = _data(venue, start=start, end=end)

    async def block():
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO table_occupancies "
                    "(venue_id, table_id, kind, starts_at, ends_at) "
                    "VALUES (:v, :t, 'BLOCK', :s, :e)"
                ),
                {"v": venue.venue_id, "t": venue.table_ids[0], "s": start, "e": end},
            )

    results = await asyncio.gather(
        _create(session_factory, venue, data), block(), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    async with session_factory() as session:
        active = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE venue_id=:v AND is_active",
            v=venue.venue_id,
        )
    assert active == 1


# --- 54.7 multi-table atomicity ---------------------------------------------


async def test_multi_table_atomicity_shared_table(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=3)
    shared = venue.table_ids[0]
    other = venue.table_ids[1]
    a = _data(venue, start=_at(venue, 20), end=_at(venue, 22), tables=[shared, other])
    b = _data(venue, start=_at(venue, 20), end=_at(venue, 22), tables=[shared])
    results = await asyncio.gather(
        _create(session_factory, venue, a),
        _create(session_factory, venue, b),
        return_exceptions=True,
    )
    ok, errors = _results(results)
    assert len(ok) == 1
    assert isinstance(errors[0], BookingConflictError)
    async with session_factory() as session:
        # No partial success: exactly the winning booking's occupancies exist.
        assert (
            await _scalar(
                session, "SELECT count(*) FROM bookings WHERE venue_id=:v", v=venue.venue_id
            )
            == 1
        )
        assert await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE venue_id=:v AND is_active",
            v=venue.venue_id,
        ) == len(ok[0].table_ids)


# --- 54.8 cross-table lock order --------------------------------------------


async def test_cross_table_lock_order_no_deadlock(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=3)
    t1, t2 = venue.table_ids[0], venue.table_ids[1]
    # Same two tables supplied in opposite order, non-overlapping intervals.
    a = _data(venue, start=_at(venue, 18), end=_at(venue, 20), tables=[t1, t2])
    b = _data(venue, start=_at(venue, 20), end=_at(venue, 22), tables=[t2, t1])
    results = await asyncio.gather(
        _create(session_factory, venue, a),
        _create(session_factory, venue, b),
        return_exceptions=True,
    )
    ok, errors = _results(results)
    assert len(ok) == 2, errors


# --- 54.9 archive vs booking ------------------------------------------------


async def test_archive_vs_booking(api_client: TestClient, tmp_path: Path, session_factory) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    data = _data(venue, start=_at(venue, 20), end=_at(venue, 22))

    async def archive():
        async with session_factory() as session, session.begin():
            return await archive_table(session, venue.venue_id, venue.table_ids[0])

    results = await asyncio.gather(
        _create(session_factory, venue, data), archive(), return_exceptions=True
    )
    ok, errors = _results(results)
    # One of the two must lose; a booking must never coexist with an archived table.
    assert len(ok) == 1, results
    async with session_factory() as session:
        archived = await _scalar(
            session, "SELECT archived_at FROM tables WHERE id=:t", t=venue.table_ids[0]
        )
        active = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE table_id=:t AND is_active",
            t=venue.table_ids[0],
        )
    if archived is not None:
        assert active == 0
    assert any(isinstance(e, (TableArchiveBlockedError, TableNotBookableError)) for e in errors)


# --- 54.10 capacity vs booking ----------------------------------------------


async def test_capacity_decrease_vs_create(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, capacity=6, tables=1)
    data = _data(venue, start=_at(venue, 20), end=_at(venue, 22), party=6)
    layout = validate_layout_import(
        {
            "halls": [
                {
                    "name": "Зал",
                    "canvas_width": 640,
                    "canvas_height": 480,
                    "tables": [
                        {
                            "number": "1",
                            "capacity": 4,
                            "shape": "rect",
                            "x": 10,
                            "y": 10,
                            "width": 80,
                            "height": 80,
                        }
                    ],
                }
            ]
        }
    )

    async def shrink():
        async with session_factory() as session, session.begin():
            return await import_layout(session, venue.venue_id, layout)

    results = await asyncio.gather(
        _create(session_factory, venue, data), shrink(), return_exceptions=True
    )
    ok, _errors = _results(results)
    # Whatever the serialisation order, no booking may violate capacity.
    async with session_factory() as session:
        rows = await session.execute(
            text(
                "SELECT o.booking_id, sum(t.capacity), b.party_size FROM table_occupancies o "
                "JOIN tables t ON t.id = o.table_id "
                "JOIN bookings b ON b.id = o.booking_id "
                "WHERE o.venue_id = :v AND o.is_active GROUP BY o.booking_id, b.party_size"
            ),
            {"v": venue.venue_id},
        )
        for _booking_id, capacity, party_size in rows.all():
            assert capacity >= party_size


# --- 54.11 schedule vs booking ----------------------------------------------


async def test_schedule_change_vs_create(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    # 17:00-20:00 fits both the old (16:00-02:00) and new (16:00-21:00) shift.
    data = _data(venue, start=_at(venue, 17), end=_at(venue, 20))

    async def change():
        async with session_factory() as session, session.begin():
            await upsert_exception(
                session,
                venue.venue_id,
                venue.business_date,
                ScheduleRule.open(time(16, 0), time(21, 0)),
                MSK,
                confirm=True,
            )

    results = await asyncio.gather(
        _create(session_factory, venue, data), change(), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 2, errors
    # The booking must still fit the committed schedule (it resolved under the lock).
    async with session_factory() as session:
        booking = (
            await session.execute(select(Booking).where(Booking.venue_id == venue.venue_id))
        ).scalar_one()
    assert booking.ends_at <= booking.shift_ends_at


# --- 54.12 cancel vs time change --------------------------------------------


async def test_cancel_vs_change_time(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking

    async def do_cancel():
        async with session_factory() as session:
            return await cancel_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=1,
                reason="NO_SHOW",
                admin_session_id=None,
            )

    async def do_change():
        async with session_factory() as session:
            return await change_booking_time(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=1,
                starts_at=_at(venue, 20),
                ends_at=_at(venue, 22),
                admin_session_id=None,
            )

    results = await asyncio.gather(do_cancel(), do_change(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (BookingStaleError, BookingInvalidStateError))
    async with session_factory() as session:
        row = (await session.execute(select(Booking).where(Booking.id == booking.id))).scalar_one()
        active = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE booking_id=:b AND is_active",
            b=booking.id,
        )
    if row.status == "CANCELED":
        assert active == 0


# --- 54.13 no inverted segments ---------------------------------------------


async def test_no_occupancy_segment_is_inverted(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    async with session_factory() as session:
        await cancel_booking(
            session,
            venue_id=venue.venue_id,
            booking_id=booking.id,
            expected_version=1,
            reason="GUEST_CANCELED",
            admin_session_id=None,
        )
    async with session_factory() as session:
        inverted = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE ends_at <= starts_at AND venue_id=:v",
            v=venue.venue_id,
        )
    assert inverted == 0


# --- 54.14 / 54.15 constraint as the final arbiter --------------------------


async def test_cross_tenant_composite_fk(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    a = Venue(api_client, tmp_path, tables=1)
    b = Venue(api_client, tmp_path, tables=1)
    booking_a = (
        await _create(session_factory, a, _data(a, start=_at(a, 20), end=_at(a, 22)))
    ).booking
    # Booking of venue A referencing a table of venue B must be rejected by the FK.
    async with session_factory() as session:
        with pytest.raises(IntegrityError) as excinfo:
            await session.execute(
                text(
                    "INSERT INTO table_occupancies "
                    "(venue_id, table_id, kind, booking_id, starts_at, ends_at) "
                    "VALUES (:v, :t, 'BOOKING', :b, :s, :e)"
                ),
                {
                    "v": a.venue_id,
                    "t": b.table_ids[0],
                    "b": booking_a.id,
                    "s": _at(a, 23),
                    "e": _at(a, 23, 55),
                },
            )
        assert "table_occupancies" in str(excinfo.value)
        await session.rollback()


async def test_exclusion_constraint_is_the_final_arbiter(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    """A direct overlapping insert bypassing the app pre-check is refused (§12, §54.15)."""
    venue = Venue(api_client, tmp_path, tables=1)
    await _create(session_factory, venue, _data(venue, start=_at(venue, 20), end=_at(venue, 22)))
    async with session_factory() as session:
        with pytest.raises(IntegrityError) as excinfo:
            await session.execute(
                text(
                    "INSERT INTO table_occupancies "
                    "(venue_id, table_id, kind, starts_at, ends_at) "
                    "VALUES (:v, :t, 'BLOCK', :s, :e)"
                ),
                {
                    "v": venue.venue_id,
                    "t": venue.table_ids[0],
                    "s": _at(venue, 21),
                    "e": _at(venue, 23),
                },
            )
        assert "occupancy_no_overlap" in str(excinfo.value)
        await session.rollback()
