"""Stage 11 PostgreSQL table-mutation concurrency suite (PROJECT-SPEC §54.3, §54.7, §54.10).

Every test runs **real parallel transactions** against PostgreSQL: each task owns
its own ``AsyncSession``/connection and calls the service layer directly, so the
transactions genuinely overlap on the server. Outcomes are asserted to be
deterministic from the canonical locks + DB invariants, never from wall-clock
timing; synchronisation uses ``asyncio.gather`` with no sleeps.
"""

from __future__ import annotations

import asyncio

import pytest
from app.services.bookings import (
    add_booking_tables,
    change_booking_time,
    lifecycle_booking,
    replace_booking_tables,
)
from app.services.errors import (
    BookingConflictError,
    BookingInvalidStateError,
    BookingStaleError,
    TableLiveConflictError,
)

from tests.integration.test_bookings_api import Venue
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


def _fixed(moment):
    """A deterministic replacement for the single DB-time helper (§55)."""

    async def clock(_session):
        return moment

    return clock


# --- 1. two bookings add the same table concurrently ------------------------


async def test_concurrent_add_same_table_exactly_one_wins(
    api_client, tmp_path, session_factory, monkeypatch
):
    venue = Venue(api_client, tmp_path, tables=3)
    a = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    b = (
        await _create(
            session_factory,
            venue,
            _data(venue, start=_at(venue, 18), end=_at(venue, 20), tables=[venue.table_ids[1]]),
        )
    ).booking
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 17)))

    async def do_add(booking):
        async with session_factory() as session:
            return await add_booking_tables(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                table_ids=[venue.table_ids[2]],
                admin_session_id=None,
            )

    results = await asyncio.gather(do_add(a), do_add(b), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], BookingConflictError), errors
    async with session_factory() as session:
        occupying = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE venue_id=:v AND table_id=:t "
            "AND is_active AND kind='BOOKING'",
            v=venue.venue_id,
            t=venue.table_ids[2],
        )
    assert occupying == 1


# --- 2. two bookings replace onto the same free table concurrently ----------


async def test_concurrent_replace_onto_same_table(
    api_client, tmp_path, session_factory, monkeypatch
):
    venue = Venue(api_client, tmp_path, tables=3)
    a = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    b = (
        await _create(
            session_factory,
            venue,
            _data(venue, start=_at(venue, 18), end=_at(venue, 20), tables=[venue.table_ids[1]]),
        )
    ).booking
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 17)))

    async def do_replace(booking, old):
        async with session_factory() as session:
            return await replace_booking_tables(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                from_table_ids=[old],
                to_table_ids=[venue.table_ids[2]],
                admin_session_id=None,
            )

    results = await asyncio.gather(
        do_replace(a, venue.table_ids[0]), do_replace(b, venue.table_ids[1]), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], BookingConflictError), errors
    # The loser keeps its original table (no partial write).
    async with session_factory() as session:
        active = await _scalar(
            session,
            "SELECT count(*) FROM table_occupancies WHERE venue_id=:v AND table_id=:t AND is_active",
            v=venue.venue_id,
            t=venue.table_ids[2],
        )
    assert active == 1


# --- 3. replace vs a competing create --------------------------------------


async def test_replace_vs_competing_create(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 17)))
    intruder = _data(venue, start=_at(venue, 18), end=_at(venue, 20), tables=[venue.table_ids[1]])

    async def do_replace():
        async with session_factory() as session:
            return await replace_booking_tables(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                from_table_ids=[venue.table_ids[0]],
                to_table_ids=[venue.table_ids[1]],
                admin_session_id=None,
            )

    results = await asyncio.gather(
        do_replace(), _create(session_factory, venue, intruder), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], BookingConflictError), errors


# --- 4. move NEW vs competing create on the target table --------------------


async def test_move_new_vs_competing_create(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 17)))
    # A create that targets the table the move wants at [20:00, 22:00).
    intruder = _data(venue, start=_at(venue, 20), end=_at(venue, 22), tables=[venue.table_ids[0]])

    async def do_move():
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

    results = await asyncio.gather(
        do_move(), _create(session_factory, venue, intruder), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], BookingConflictError), errors


# --- 5. extend OPEN end vs competing create ---------------------------------


async def test_extend_end_vs_competing_create(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    view = await _create(
        session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
    )
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 18, 30)))
    async with session_factory() as session:
        opened = await lifecycle_booking(
            session,
            venue_id=venue.venue_id,
            booking_id=view.booking.id,
            expected_version=view.booking.version,
            action="open",
            admin_session_id=None,
        )
    version = opened.booking.version
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 18, 40)))
    intruder = _data(
        venue, start=_at(venue, 20, 30), end=_at(venue, 22), tables=[venue.table_ids[0]]
    )

    async def do_extend():
        async with session_factory() as session:
            return await change_booking_time(
                session,
                venue_id=venue.venue_id,
                booking_id=view.booking.id,
                expected_version=version,
                starts_at=None,
                ends_at=_at(venue, 21),
                admin_session_id=None,
            )

    results = await asyncio.gather(
        do_extend(), _create(session_factory, venue, intruder), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], BookingConflictError), errors


# --- 6. two admins mutate the same booking version --------------------------


async def test_two_admins_same_version_one_stale(
    api_client, tmp_path, session_factory, monkeypatch
):
    venue = Venue(api_client, tmp_path, tables=3)
    booking = (
        await _create(
            session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
        )
    ).booking
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 17)))

    async def do_add(table_id):
        async with session_factory() as session:
            return await add_booking_tables(
                session,
                venue_id=venue.venue_id,
                booking_id=booking.id,
                expected_version=booking.version,
                table_ids=[table_id],
                admin_session_id=None,
            )

    results = await asyncio.gather(
        do_add(venue.table_ids[1]), do_add(venue.table_ids[2]), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(
        errors[0], (BookingStaleError, BookingInvalidStateError, BookingConflictError)
    )
    async with session_factory() as session:
        version = await _scalar(session, "SELECT version FROM bookings WHERE id=:b", b=booking.id)
    assert version == booking.version + 1


# --- 7. OPEN reseat vs competing OPEN on the same table ---------------------


async def test_reseat_vs_competing_open(api_client, tmp_path, session_factory, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    a_view = await _create(
        session_factory, venue, _data(venue, start=_at(venue, 18), end=_at(venue, 20))
    )
    b_view = await _create(
        session_factory,
        venue,
        _data(venue, start=_at(venue, 18), end=_at(venue, 20), tables=[venue.table_ids[1]]),
    )
    monkeypatch.setattr("app.services.bookings.operation_now", _fixed(_at(venue, 18, 30)))
    async with session_factory() as session:
        a_opened = await lifecycle_booking(
            session,
            venue_id=venue.venue_id,
            booking_id=a_view.booking.id,
            expected_version=a_view.booking.version,
            action="open",
            admin_session_id=None,
        )
    a_version = a_opened.booking.version

    async def do_reseat():
        async with session_factory() as session:
            return await replace_booking_tables(
                session,
                venue_id=venue.venue_id,
                booking_id=a_view.booking.id,
                expected_version=a_version,
                from_table_ids=[venue.table_ids[0]],
                to_table_ids=[venue.table_ids[1]],
                admin_session_id=None,
            )

    async def do_open_b():
        async with session_factory() as session:
            return await lifecycle_booking(
                session,
                venue_id=venue.venue_id,
                booking_id=b_view.booking.id,
                expected_version=b_view.booking.version,
                action="open",
                admin_session_id=None,
            )

    results = await asyncio.gather(do_reseat(), do_open_b(), return_exceptions=True)
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], (TableLiveConflictError, BookingConflictError)), errors
    async with session_factory() as session:
        live = await _scalar(
            session,
            "SELECT count(*) FROM booking_live_tables WHERE venue_id=:v AND table_id=:t",
            v=venue.venue_id,
            t=venue.table_ids[1],
        )
    assert live == 1
