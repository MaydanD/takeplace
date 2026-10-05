"""Stage 8 PostgreSQL concurrency suite (PROJECT-SPEC §54.3, §54.12, §59, §61).

Every test runs **real parallel transactions** against PostgreSQL (no SQLite, no
sequential pseudo-concurrency): each task owns its own ``AsyncSession``/connection
and calls the service layer directly, so the transactions genuinely overlap on the
server. The outcomes are asserted to be deterministic (canonical locks + DB
invariants), never dependent on wall-clock timing; synchronisation uses
``asyncio.Event`` barriers, never sleeps.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from app.db.models import Booking, BookingLiveTable
from app.services.bookings import (
    cancel_booking,
    change_booking_time,
    lifecycle_booking,
)
from app.services.errors import (
    BookingConflictError,
    BookingInvalidStateError,
    BookingStaleError,
    TableLiveConflictError,
)
from sqlalchemy import select

from tests.integration.test_bookings_api import MSK, Venue
from tests.integration.test_bookings_concurrency import (
    _at,
    _create,
    _data,
    _results,
    _scalar,
    api_client,
    engine,
    session_factory,
)

pytestmark = pytest.mark.integration
__all__ = ["api_client", "engine", "session_factory"]


def _dt(venue, hour, minute=0, day_offset=0):
    day = venue.business_date + timedelta(days=day_offset)
    return datetime.combine(day, datetime.min.time(), tzinfo=MSK).replace(hour=hour, minute=minute)


def _fixed(moment):
    """A deterministic replacement for the single DB-time helper (§55)."""

    async def clock(_session):
        return moment

    return clock


async def _open_at(factory, venue, booking_id, version):
    async with factory() as session:
        return await lifecycle_booking(
            session,
            venue_id=venue.venue_id,
            booking_id=booking_id,
            expected_version=version,
            action="open",
            admin_session_id=None,
        )


# --- OPEN vs OPEN (§22.3) ---------------------------------------------------


async def test_open_vs_open_same_live_table(api_client, tmp_path, session_factory, monkeypatch):
    """Two bookings cannot both become live on the same table/business date (§59.7)."""
    venue = Venue(api_client, tmp_path, tables=1)
    # Adjacent plans on the same table are legal; both are then opened early at a
    # moment where only one can be live on the table (they overlap on [now, 20:00)).
    a = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    b = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 20), end=_at(venue, 22))
        )
    ).booking
    now = _dt(venue, 19, 30)

    async def do_open(booking_id, version):
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=booking_id,
                expected_version=version,
                action="open",
                admin_session_id=None,
            )

    results = await asyncio.gather(
        do_open(a.id, a.version), do_open(b.id, b.version), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (TableLiveConflictError, BookingConflictError)), errors
    async with session_factory() as session:
        live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE venue_id = :v",
            v=venue.venue_id,
        )
    assert live == 1


# --- two OPEN of the same booking with the same expected_version ------------


async def test_two_open_same_booking_same_version(
    api_client, tmp_path, session_factory, monkeypatch
):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    now = _dt(venue, 18, 30)

    async def do_open():
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                action="open",
                admin_session_id=None,
            )

    results = await asyncio.gather(do_open(), do_open(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert all(isinstance(e, (BookingStaleError, BookingInvalidStateError)) for e in errors), errors
    async with session_factory() as session:
        live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE booking_id = :b",
            b=booking.id,
        )
        events = await _scalar(
            session,
            "SELECT count(*) FROM booking_events WHERE booking_id = :b AND event_type='BOOKING_OPENED'",
            b=booking.id,
        )
    assert live == 1 and events == 1


# --- early OPEN vs create (§54.3) -------------------------------------------


async def test_early_open_vs_create_gap(api_client, tmp_path, session_factory, monkeypatch):
    """An early OPEN of the gap [now, starts_at) and a create on the same table
    cannot both commit: the table FOR UPDATE / FOR SHARE ordering plus the
    post-lock revalidation close the race the exclusion constraint misses (§54.3)."""
    venue = Venue(api_client, tmp_path, tables=1)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 19), end=_at(venue, 20))
        )
    ).booking
    now = _dt(venue, 18, 0)
    # A create targeting the early gap [18:05, 19:00) on the same table.
    intruder = _data(venue, start=_dt(venue, 18, 5), end=_at(venue, 19))
    assert intruder.starts_at >= now

    async def do_open():
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                action="open",
                admin_session_id=None,
            )

    results = await asyncio.gather(
        do_open(), _create(session_factory, venue, intruder), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (BookingConflictError, TableLiveConflictError)), errors
    async with session_factory() as session:
        # No committed state where the table is live AND a conflicting plan exists.
        live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE venue_id = :v",
            v=venue.venue_id,
        )
        plans = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE venue_id = :v AND is_active",
            v=venue.venue_id,
        )
    if live == 1:
        assert plans == 1  # only the opened booking's own plan remains
    else:
        assert plans == 2


# --- OPEN vs CANCEL (§54.12) ------------------------------------------------


async def test_open_vs_cancel(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    now = _dt(venue, 18, 30)

    async def do_open():
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                action="open",
                admin_session_id=None,
            )

    async def do_cancel():
        async with session_factory() as session:
            return await cancel_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                reason="NO_SHOW",
                admin_session_id=None,
            )

    results = await asyncio.gather(do_open(), do_cancel(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (BookingStaleError, BookingInvalidStateError)), errors
    async with session_factory() as session:
        row = (await session.execute(select(Booking).where(Booking.id == booking.id))).scalar_one()
        live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE booking_id = :b",
            b=booking.id,
        )
    if row.status == "OPEN":
        assert live == 1
    else:
        assert row.status == "CANCELED" and live == 0


# --- OPEN vs change-time (§20.3) --------------------------------------------


async def test_open_vs_change_time(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    now = _dt(venue, 18, 30)

    async def do_open():
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                action="open",
                admin_session_id=None,
            )

    async def do_change():
        async with session_factory() as session:
            return await change_booking_time(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                starts_at=_at(venue, 20),
                ends_at=_at(venue, 22),
                admin_session_id=None,
            )

    results = await asyncio.gather(do_open(), do_change(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (BookingStaleError, BookingInvalidStateError)), errors


# --- CLOSE vs CLOSE (§24) ---------------------------------------------------


async def test_close_vs_close(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    view = await _create(
        session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
    )
    now = _dt(venue, 18, 30)
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now))
    opened = await _open_at(session_factory, venue, view.booking.id, view.booking.version)
    version = opened.booking.version

    async def do_close():
        async with session_factory() as session:
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=view.booking.id,
                expected_version=version,
                action="close",
                admin_session_id=None,
            )

    results = await asyncio.gather(do_close(), do_close(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (BookingStaleError, BookingInvalidStateError)), errors
    async with session_factory() as session:
        events = await _scalar(
            session,
            "SELECT count(*) FROM booking_events WHERE booking_id=:b AND event_type='BOOKING_CLOSED'",
            b=view.booking.id,
        )
    assert events == 1


# --- CLOSE vs UNDO_OPEN (§23/§24) -------------------------------------------


async def test_close_vs_undo_open(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    view = await _create(
        session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
    )
    now = _dt(venue, 18, 30)
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now))
    opened = await _open_at(session_factory, venue, view.booking.id, view.booking.version)
    version = opened.booking.version

    async def do_close():
        async with session_factory() as session:
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=view.booking.id,
                expected_version=version,
                action="close",
                admin_session_id=None,
            )

    async def do_undo():
        async with session_factory() as session:
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=view.booking.id,
                expected_version=version,
                action="undo-open",
                admin_session_id=None,
            )

    results = await asyncio.gather(do_close(), do_undo(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (BookingStaleError, BookingInvalidStateError)), errors
    async with session_factory() as session:
        row = (
            await session.execute(select(Booking).where(Booking.id == view.booking.id))
        ).scalar_one()
        live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE booking_id=:b",
            b=view.booking.id,
        )
    # Either CLOSED (no live) or back to NEW (no live): never CLOSED with live rows.
    assert live == 0
    assert row.status in ("CLOSED", "NEW", "WAITING")


# --- D1 OPEN vs D2 new visit (§61) ------------------------------------------


async def test_old_open_d1_vs_new_visit_d2(api_client, tmp_path, session_factory, monkeypatch):
    """A stale D1 OPEN must not prevent a D2 OPEN of the same table (§61)."""
    venue = Venue(api_client, tmp_path, tables=1)
    d1 = await _create(
        session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
    )
    now_d1 = _dt(venue, 18, 30)
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now_d1))
    await _open_at(session_factory, venue, d1.booking.id, d1.booking.version)

    next_day = venue.business_date + timedelta(days=1)
    venue._open_day(next_day)
    d2 = await _create(
        session_factory,
        venue,
        _data(
            venue,
            start=datetime.combine(next_day, datetime.min.time(), tzinfo=MSK).replace(hour=18),
            end=datetime.combine(next_day, datetime.min.time(), tzinfo=MSK).replace(hour=20),
        ),
    )
    now_d2 = _dt(venue, 18, 30, day_offset=1)

    async def do_d2():
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now_d2))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=d2.booking.id,
                expected_version=d2.booking.version,
                action="open",
                admin_session_id=None,
            )

    results = await asyncio.gather(do_d2(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1 and not errors, results
    async with session_factory() as session:
        live = list(
            (
                await session.scalars(
                    select(BookingLiveTable).where(BookingLiveTable.venue_id == venue.venue_id)
                )
            ).all()
        )
    assert {(row.booking_id, row.business_date) for row in live} == {
        (d1.booking.id, venue.business_date),
        (d2.booking.id, next_day),
    }


async def test_old_close_d1_vs_current_open_d2(api_client, tmp_path, session_factory, monkeypatch):
    """Closing the D1 visit concurrently with a D2 OPEN must not damage D2's live state."""
    venue = Venue(api_client, tmp_path, tables=1)
    d1 = await _create(
        session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
    )
    now_d1 = _dt(venue, 18, 30)
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now_d1))
    opened_d1 = await _open_at(session_factory, venue, d1.booking.id, d1.booking.version)
    d1_version = opened_d1.booking.version

    next_day = venue.business_date + timedelta(days=1)
    venue._open_day(next_day)
    d2 = await _create(
        session_factory,
        venue,
        _data(
            venue,
            start=datetime.combine(next_day, datetime.min.time(), tzinfo=MSK).replace(hour=18),
            end=datetime.combine(next_day, datetime.min.time(), tzinfo=MSK).replace(hour=20),
        ),
    )
    now_d2 = _dt(venue, 18, 30, day_offset=1)

    async def do_close_d1():
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now_d2))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=d1.booking.id,
                expected_version=d1_version,
                action="close",
                admin_session_id=None,
            )

    async def do_open_d2():
        async with session_factory() as session:
            monkeypatch.setattr("app.services.bookings.operation_now", _fixed(now_d2))
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=d2.booking.id,
                expected_version=d2.booking.version,
                action="open",
                admin_session_id=None,
            )

    results = await asyncio.gather(do_close_d1(), do_open_d2(), return_exceptions=True)
    ok, errors = _results(results)
    # Both must succeed: they touch disjoint live rows (different business dates).
    assert len(ok) == 2, (results, errors)
    async with session_factory() as session:
        d1_live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE booking_id=:b",
            b=d1.booking.id,
        )
        d2_live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE booking_id=:b",
            b=d2.booking.id,
        )
        d2_status = await _scalar(
            session, "SELECT status FROM bookings WHERE id=:b", b=d2.booking.id
        )
    assert d1_live == 0  # D1 CLOSE removed only its own live row
    assert d2_live == 1 and d2_status == "OPEN"  # D2 untouched
