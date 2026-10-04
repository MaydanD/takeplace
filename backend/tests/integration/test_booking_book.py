"""Stage 7 API and real PostgreSQL concurrency acceptance gates."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta

import pytest
from app.db.models import Booking, BookingEvent, BookingLiveTable, Table, TableOccupancy
from app.db.time import ceil_to_5_minutes
from app.domain.layout import validate_layout_import
from app.services.bookings import (
    AdminBookingInput,
    PublicBookingInput,
    change_booking_time,
    create_public_booking,
    edit_booking_guest,
)
from app.services.errors import (
    BookingConflictError,
    BookingRuleViolationError,
    BookingStaleError,
    CapacityChangeBlockedError,
    TableArchiveBlockedError,
    TableLiveConflictError,
)
from app.services.halls import archive_table, import_layout
from app.services.live_availability import live_busy_intervals
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.integration.support import BOOKINGS_URL, SCHEDULE_EXCEPTIONS_URL, SETTINGS_URL
from tests.integration.test_bookings_api import MSK, Venue, _layout
from tests.integration.test_bookings_concurrency import (
    HMAC_KEY,
    _at,
    _create,
    _data,
    api_client,
    engine,
    session_factory,
)

pytestmark = pytest.mark.integration
__all__ = ["api_client", "engine", "session_factory"]


def create(venue, **kwargs):
    return venue.create(start=venue.at(18), end=venue.at(20), **kwargs).json()


def patch(venue, booking, **changes):
    return venue.client.patch(
        f"{BOOKINGS_URL}/{booking['id']}",
        headers=venue.headers(),
        json={"expected_version": booking["version"], **changes},
    )


def walk_payload(venue):
    assert (
        venue.client.patch(
            SETTINGS_URL, headers=venue.headers(), json={"online_booking_enabled": True}
        ).status_code
        == 200
    )
    now = datetime.now(MSK)
    shift_start = ceil_to_5_minutes(now) - timedelta(hours=1)
    shift_end = shift_start + timedelta(hours=4)
    response = venue.client.put(
        f"{SCHEDULE_EXCEPTIONS_URL}/{shift_start.date()}",
        headers=venue.headers(),
        json={
            "is_closed": False,
            "open_time": shift_start.strftime("%H:%M"),
            "close_time": shift_end.strftime("%H:%M"),
        },
    )
    assert response.status_code == 200, response.text
    start = ceil_to_5_minutes(now)
    end = start + timedelta(hours=1)
    return {
        "source": "WALK_IN",
        "open_immediately": True,
        "guest_name": "Walk guest",
        "party_size": 2,
        "table_ids": venue.table_ids[:1],
        "starts_at": venue.at(18),
        "ends_at": end.isoformat(),
    }


@pytest.mark.parametrize(
    "source,phone", [("PHONE", "+79990000000"), ("VK", None), ("OTHER", "+79990000000")]
)
def test_manual_sources_and_history(api_client, tmp_path, source, phone):
    venue = Venue(api_client, tmp_path)
    booking = create(venue, source=source, phone=phone)
    assert booking["status"] == "NEW" and booking["version"] == 1
    assert booking["guest_phone_raw"] == phone
    history = api_client.get(
        f"{BOOKINGS_URL}/{booking['id']}/history", headers=venue.headers()
    ).json()
    assert [e["event_type"] for e in history["events"]] == ["BOOKING_CREATED"]
    assert "Гость" not in json.dumps(history) and "+7999" not in json.dumps(history)


def test_filters_pagination_phone_number_and_tenant(api_client, tmp_path):
    venue = Venue(api_client, tmp_path)
    other = Venue(api_client, tmp_path)
    foreign = create(other)
    first = create(venue, source="VK", phone=None)
    second = create(venue, table_ids=venue.table_ids[1:2])
    base = {"business_date": venue.business_date.isoformat(), "status": "NEW", "limit": 1}
    page = api_client.get(BOOKINGS_URL, headers=venue.headers(), params=base).json()
    assert [b["id"] for b in page["items"]] == [second["id"]]
    page2 = api_client.get(
        BOOKINGS_URL, headers=venue.headers(), params={**base, "cursor": page["next_cursor"]}
    ).json()
    assert [b["id"] for b in page2["items"]] == [first["id"]] and page2["next_cursor"] is None
    for params, expected in [
        ({"source": "VK"}, [first["id"]]),
        ({"guest_phone_normalized": "+79990000000"}, [second["id"]]),
        ({"phone": "+7 (999) 000-00-00"}, [second["id"]]),
        ({"phone": "%_"}, []),
        ({"number": first["number"]}, [first["id"]]),
        ({"table_id": venue.table_ids[1]}, [second["id"]]),
        ({"table_id": other.table_ids[0]}, []),
    ]:
        rows = api_client.get(
            BOOKINGS_URL, headers=venue.headers(), params={**base, **params}
        ).json()
        assert [b["id"] for b in rows["items"]] == expected
    assert (
        api_client.get(
            BOOKINGS_URL, headers=venue.headers(), params={"same_network_as": foreign["id"]}
        ).status_code
        == 404
    )


def test_guest_whitelist_stale_events_capacity_terminal(api_client, tmp_path):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    assert patch(venue, booking, status="OPEN").status_code == 422
    assert patch(venue, booking, party_size=5).status_code == 422
    assert patch(venue, booking, guest_phone_raw=None).status_code == 422
    assert patch(venue, booking, guest_name=" ").status_code == 422
    assert patch(venue, booking, party_size=None).status_code == 422
    result = patch(
        venue,
        booking,
        guest_name="New private name",
        guest_phone_raw="+79991112233",
        guest_comment="private comment",
        party_size=4,
    )
    assert result.status_code == 200, result.text
    assert result.json()["version"] == 2
    assert patch(venue, booking, guest_name="stale").json()["code"] == "BOOKING_STALE"
    history = api_client.get(
        f"{BOOKINGS_URL}/{booking['id']}/history", headers=venue.headers()
    ).json()["events"]
    assert [e["event_type"] for e in history] == ["BOOKING_CREATED", "BOOKING_EDITED"]
    assert history[-1]["payload"] == {
        "changed_fields": ["guest_comment", "guest_name", "guest_phone_raw", "party_size"]
    }
    assert all(
        s not in json.dumps(history)
        for s in ["New private name", "+79991112233", "private comment"]
    )
    canceled = api_client.post(
        f"{BOOKINGS_URL}/{booking['id']}/cancel",
        headers=venue.headers(),
        json={"expected_version": 2, "reason": "GUEST_CANCELED"},
    )
    assert canceled.json()["version"] == 3
    assert patch(venue, canceled.json(), guest_name="no").json()["code"] == "BOOKING_INVALID_STATE"


def test_cross_tenant_every_stage7_route(api_client, tmp_path):
    a, b = Venue(api_client, tmp_path), Venue(api_client, tmp_path)
    foreign = create(b)
    url = f"{BOOKINGS_URL}/{foreign['id']}"
    for suffix in ["", "/history"]:
        assert api_client.get(url + suffix, headers=a.headers()).status_code == 404
    assert patch(a, foreign, guest_name="no").status_code == 404
    assert (
        api_client.post(
            url + "/cancel", headers=a.headers(), json={"expected_version": 1, "reason": "OTHER"}
        ).status_code
        == 404
    )
    assert (
        api_client.post(
            url + "/change-time",
            headers=a.headers(),
            json={"expected_version": 1, "starts_at": a.at(20), "ends_at": a.at(22)},
        ).status_code
        == 404
    )
    payload = walk_payload(a)
    payload["table_ids"] = b.table_ids[:1]
    assert (
        api_client.post(
            BOOKINGS_URL, headers=a.headers(**{"Idempotency-Key": str(uuid.uuid4())}), json=payload
        ).status_code
        == 404
    )


async def test_walk_in_atomic_replay_edit_live_and_constraint(
    api_client, tmp_path, session_factory
):
    venue = Venue(api_client, tmp_path)
    payload = walk_payload(venue)
    headers = venue.headers(**{"Idempotency-Key": str(uuid.uuid4())})
    response = api_client.post(BOOKINGS_URL, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    booking = response.json()
    assert booking["status"] == "OPEN" and booking["version"] == 1
    assert booking["guest_phone_raw"] is None
    assert datetime.fromisoformat(booking["starts_at"]) == ceil_to_5_minutes(
        datetime.fromisoformat(booking["opened_at"])
    )
    assert booking["live_table_ids"] == venue.table_ids[:1]
    assert api_client.post(BOOKINGS_URL, headers=headers, json=payload).status_code == 200
    assert (
        api_client.post(
            BOOKINGS_URL, headers=headers, json={**payload, "open_immediately": False}
        ).json()["code"]
        == "IDEMPOTENCY_KEY_REUSED"
    )
    assert patch(venue, booking, party_size=5).status_code == 422
    assert patch(venue, booking, party_size=4).json()["version"] == 2
    async with session_factory() as session:
        history = (
            await session.scalars(
                select(BookingEvent)
                .where(BookingEvent.booking_id == booking["id"])
                .order_by(BookingEvent.id)
            )
        ).all()
        assert [e.event_type for e in history] == [
            "BOOKING_CREATED",
            "BOOKING_OPENED",
            "BOOKING_EDITED",
        ]
        live = (
            await session.scalars(
                select(BookingLiveTable).where(BookingLiveTable.booking_id == booking["id"])
            )
        ).one()
        session.add(
            BookingLiveTable(
                venue_id=venue.venue_id,
                booking_id=booking["id"],
                business_date=live.business_date,
                table_id=live.table_id,
                live_since=live.live_since,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_two_admin_edits_real_postgres(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    ready = asyncio.Event()

    async def edit(name):
        async with session_factory() as session:
            await ready.wait()
            return await edit_booking_guest(
                session,
                venue_id=venue.venue_id,
                booking_id=booking["id"],
                expected_version=1,
                changes={"guest_name": name},
                admin_session_id=None,
            )

    tasks = [asyncio.create_task(edit(name)) for name in ["one", "two"]]
    ready.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(isinstance(r, BookingStaleError) for r in results) == 1, results
    winner = next(r for r in results if not isinstance(r, BaseException))
    assert winner.booking.version == 2
    async with session_factory() as session:
        events = (
            await session.scalars(
                select(BookingEvent).where(BookingEvent.booking_id == booking["id"])
            )
        ).all()
        assert len(events) == 2


@pytest.mark.parametrize("public", [False, True])
async def test_walk_in_vs_create_real_postgres(api_client, tmp_path, session_factory, public):
    venue = Venue(api_client, tmp_path)
    payload = walk_payload(venue)
    end, start = datetime.fromisoformat(payload["ends_at"]), ceil_to_5_minutes(datetime.now(MSK))
    walk = AdminBookingInput(
        starts_at=start,
        ends_at=end,
        table_ids=venue.table_ids[:1],
        party_size=2,
        source="WALK_IN",
        guest_name="walk",
        open_immediately=True,
    )
    regular = _data(venue, start=start, end=end)

    async def other():
        if not public:
            return await _create(session_factory, venue, regular)
        async with session_factory() as session:
            return (
                await create_public_booking(
                    session,
                    venue_id=venue.venue_id,
                    data=PublicBookingInput(
                        starts_at=start,
                        ends_at=end,
                        table_id=venue.table_ids[0],
                        party_size=2,
                        guest_name="online",
                        guest_phone_raw="+79990000000",
                    ),
                    idempotency_key=str(uuid.uuid4()),
                    hmac_key=HMAC_KEY,
                    request_ip_hmac="test-fingerprint",
                    ip_hmac_ttl_days=1,
                )
            )[0]

    results = await asyncio.gather(
        _create(session_factory, venue, walk), other(), return_exceptions=True
    )
    assert sum(not isinstance(r, BaseException) for r in results) == 1, results
    assert (
        sum(isinstance(r, (BookingConflictError, TableLiveConflictError)) for r in results) == 1
    ), results
    async with session_factory() as session:
        bookings = (
            await session.scalars(select(Booking).where(Booking.venue_id == venue.venue_id))
        ).all()
        assert len(bookings) == 1
        live = (
            await session.scalars(
                select(BookingLiveTable).where(BookingLiveTable.venue_id == venue.venue_id)
            )
        ).all()
        assert len(live) == (1 if bookings[0].status == "OPEN" else 0)


async def test_walk_in_concurrent_idempotency(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    payload = walk_payload(venue)
    data = AdminBookingInput(
        starts_at=datetime.fromisoformat(payload["starts_at"]),
        ends_at=datetime.fromisoformat(payload["ends_at"]),
        table_ids=venue.table_ids[:1],
        party_size=2,
        source="WALK_IN",
        guest_name="walk",
        open_immediately=True,
    )
    key = str(uuid.uuid4())
    results = await asyncio.gather(*[_create(session_factory, venue, data, key) for _ in range(3)])
    assert len({r.booking.id for r in results}) == 1
    async with session_factory() as session:
        events = (
            await session.scalars(
                select(BookingEvent).where(BookingEvent.venue_id == venue.venue_id)
            )
        ).all()
        assert len(events) == 2


async def test_fingerprint_retention_tenant_and_no_hmac_exposure(
    api_client, tmp_path, session_factory
):
    venue, other = Venue(api_client, tmp_path), Venue(api_client, tmp_path)
    for v in [venue, other]:
        assert (
            v.client.patch(
                SETTINGS_URL, headers=v.headers(), json={"online_booking_enabled": True}
            ).status_code
            == 200
        )

    async def online(v, table):
        async with session_factory() as session:
            view, _ = await create_public_booking(
                session,
                venue_id=v.venue_id,
                data=PublicBookingInput(
                    starts_at=_at(v, 18),
                    ends_at=_at(v, 20),
                    table_id=table,
                    party_size=2,
                    guest_name="online",
                    guest_phone_raw="+79990000000",
                ),
                idempotency_key=str(uuid.uuid4()),
                hmac_key=HMAC_KEY,
                request_ip_hmac="same-secret-hmac",
                ip_hmac_ttl_days=1,
            )
            return view.booking.id

    first, second = await online(venue, venue.table_ids[0]), await online(venue, venue.table_ids[1])
    await online(other, other.table_ids[0])
    response = api_client.get(
        BOOKINGS_URL, headers=venue.headers(), params={"same_network_as": first, "limit": 1}
    )
    assert "same-secret-hmac" not in response.text
    assert response.json()["items"][0]["id"] == second
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE bookings SET request_ip_hmac_expires_at = clock_timestamp() - interval '1 second' WHERE id = :id"
            ),
            {"id": second},
        )
    result = api_client.get(
        BOOKINGS_URL, headers=venue.headers(), params={"same_network_as": first}
    ).json()
    assert [b["id"] for b in result["items"]] == [first]
    assert (
        api_client.get(
            BOOKINGS_URL, headers=venue.headers(), params={"same_network_as": second}
        ).json()["items"]
        == []
    )


@pytest.mark.parametrize(
    "near_close,minutes,expected", [(True, 25, 201), (True, 20, 422), (False, 30, 422)]
)
async def test_walk_in_near_close_atomic_rollback(
    api_client, tmp_path, session_factory, monkeypatch, near_close, minutes, expected
):
    venue = Venue(api_client, tmp_path)
    now = datetime.fromisoformat(venue.at(1, 34, day_offset=1) if near_close else venue.at(18, 1))

    async def clock(_session):
        return now

    monkeypatch.setattr("app.services.bookings.operation_now", clock)
    start = ceil_to_5_minutes(now)
    payload = {
        "source": "WALK_IN",
        "open_immediately": True,
        "guest_name": "walk",
        "party_size": 2,
        "table_ids": venue.table_ids[:1],
        "starts_at": venue.at(18),
        "ends_at": (start + timedelta(minutes=minutes)).isoformat(),
    }
    response = api_client.post(
        BOOKINGS_URL, headers=venue.headers(**{"Idempotency-Key": str(uuid.uuid4())}), json=payload
    )
    assert response.status_code == expected, response.text
    async with session_factory() as session:
        rows = (
            await session.scalars(select(Booking).where(Booking.venue_id == venue.venue_id))
        ).all()
        assert len(rows) == (1 if expected == 201 else 0)
        if rows:
            assert rows[0].ends_at == rows[0].shift_ends_at
            assert rows[0].business_date == venue.business_date
        else:
            assert (
                await session.scalars(
                    select(BookingEvent).where(BookingEvent.venue_id == venue.venue_id)
                )
            ).all() == []
            assert (
                await session.scalars(
                    select(BookingLiveTable).where(BookingLiveTable.venue_id == venue.venue_id)
                )
            ).all() == []


async def test_walk_in_checks_early_gap_and_block_atomicity(
    api_client, tmp_path, session_factory, monkeypatch
):
    venue = Venue(api_client, tmp_path)
    now = _at(venue, 18) + timedelta(seconds=30)

    async def clock(_session):
        return now

    monkeypatch.setattr("app.services.bookings.operation_now", clock)
    async with session_factory() as session, session.begin():
        session.add(
            TableOccupancy(
                venue_id=venue.venue_id,
                table_id=venue.table_ids[0],
                kind="BLOCK",
                starts_at=now - timedelta(minutes=5),
                ends_at=ceil_to_5_minutes(now),
                is_active=True,
            )
        )
    data = AdminBookingInput(
        starts_at=_at(venue, 19),
        ends_at=_at(venue, 20),
        table_ids=venue.table_ids[:2],
        source="WALK_IN",
        open_immediately=True,
        party_size=2,
        guest_name="walk",
    )
    with pytest.raises(BookingConflictError):
        await _create(session_factory, venue, data)
    async with session_factory() as session:
        assert (
            await session.scalars(select(Booking).where(Booking.venue_id == venue.venue_id))
        ).all() == []
        assert (
            await session.scalars(
                select(BookingLiveTable).where(BookingLiveTable.venue_id == venue.venue_id)
            )
        ).all() == []


async def test_capacity_edit_vs_layout_real_postgres(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)

    async def edit():
        async with session_factory() as session:
            return await edit_booking_guest(
                session,
                venue_id=venue.venue_id,
                booking_id=booking["id"],
                expected_version=1,
                changes={"party_size": 4},
                admin_session_id=None,
            )

    async def shrink():
        async with session_factory() as session, session.begin():
            return await import_layout(
                session, venue.venue_id, validate_layout_import(_layout(capacity=2, tables=1))
            )

    results = await asyncio.gather(edit(), shrink(), return_exceptions=True)
    assert sum(not isinstance(r, BaseException) for r in results) == 1, results
    assert any(
        isinstance(r, (BookingRuleViolationError, CapacityChangeBlockedError)) for r in results
    ), results
    async with session_factory() as session:
        persisted = await session.get(Booking, booking["id"])
        table = await session.get(Table, venue.table_ids[0])
        assert persisted.party_size <= table.capacity


async def test_overdue_walk_in_overlay_capacity_archive_and_unresolved(
    api_client, tmp_path, session_factory, monkeypatch
):
    venue = Venue(api_client, tmp_path, tables=1)
    payload = walk_payload(venue)
    response = api_client.post(
        BOOKINGS_URL, headers=venue.headers(**{"Idempotency-Key": str(uuid.uuid4())}), json=payload
    )
    assert response.status_code == 201
    booking = response.json()
    now = datetime.fromisoformat(booking["ends_at"]) + timedelta(seconds=1)

    async def clock(_session):
        return now

    monkeypatch.setattr("app.services.bookings.operation_now", clock)
    monkeypatch.setattr("app.services.halls.operation_now", clock)
    assert patch(venue, booking, party_size=4).status_code == 200
    async with session_factory() as session:
        busy = await live_busy_intervals(session, venue.venue_id, venue.table_ids[0], now)
        assert busy == [(now, datetime.fromisoformat(booking["shift_ends_at"]))]
    data = _data(
        venue, start=ceil_to_5_minutes(now), end=ceil_to_5_minutes(now) + timedelta(hours=1)
    )
    with pytest.raises(BookingConflictError):
        await _create(session_factory, venue, data)
    async with session_factory() as session, session.begin():
        with pytest.raises(TableArchiveBlockedError):
            await archive_table(session, venue.venue_id, venue.table_ids[0])
    async with session_factory() as session, session.begin():
        with pytest.raises(CapacityChangeBlockedError):
            await import_layout(
                session, venue.venue_id, validate_layout_import(_layout(capacity=1, tables=1))
            )
        await session.rollback()
    rows = api_client.get(f"{BOOKINGS_URL}/unresolved", headers=venue.headers()).json()["items"]
    assert [b["id"] for b in rows] == [booking["id"]]
    async with session_factory() as session:
        assert (
            await live_busy_intervals(
                session,
                venue.venue_id,
                venue.table_ids[0],
                datetime.fromisoformat(booking["shift_ends_at"]),
            )
            == []
        )


async def test_end_only_in_progress_uses_snapshot(
    api_client, tmp_path, session_factory, monkeypatch
):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)

    async def clock(_session):
        return _at(venue, 19)

    monkeypatch.setattr("app.services.bookings.operation_now", clock)
    async with session_factory() as session:
        view = await change_booking_time(
            session,
            venue_id=venue.venue_id,
            booking_id=booking["id"],
            expected_version=1,
            starts_at=_at(venue, 18),
            ends_at=_at(venue, 21),
            admin_session_id=None,
        )
        assert view.booking.version == 2
        assert view.booking.starts_at == _at(venue, 18)
