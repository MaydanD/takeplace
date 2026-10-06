"""Stage 11 API acceptance tests against real PostgreSQL (PROJECT-SPEC §20-§26, §29, §54).

Covers multi-table create/read, add/remove/replace/reseat for NEW/WAITING (plan)
and OPEN (live), the plan/live separation, the reseat deadline, capacity across
the whole assigned set, capacity-change interaction, end-time extend/shorten,
tenant isolation, archive and optimistic concurrency (BOOKING_STALE).

Time is controlled deterministically by monkeypatching the single DB-time helper
``app.services.bookings.operation_now`` (§55), never by sleeping.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from app.db.models import BookingEvent, BookingLiveTable, TableOccupancy
from sqlalchemy import select

from tests.integration.support import BOOKINGS_URL, TABLES_URL
from tests.integration.test_bookings_api import MSK, Venue
from tests.integration.test_bookings_concurrency import (
    api_client,
    engine,
    session_factory,
)

pytestmark = pytest.mark.integration
__all__ = ["api_client", "engine", "session_factory"]


def _dt(venue, hour, minute=0, day_offset=0):
    day = venue.business_date + timedelta(days=day_offset)
    return datetime.combine(day, datetime.min.time(), tzinfo=MSK).replace(hour=hour, minute=minute)


def clock_at(monkeypatch, moment):
    async def clock(_session):
        return moment

    monkeypatch.setattr("app.services.bookings.operation_now", clock)
    return moment


def create(venue, **kwargs):
    return venue.create(start=venue.at(18), end=venue.at(20), **kwargs).json()


def add_tables(venue, booking, table_ids, *, version=None, expect=200):
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={
            "expected_version": version if version is not None else booking["version"],
            "table_ids": table_ids,
        },
    )
    assert response.status_code == expect, response.text
    return response.json()


def remove_table(venue, booking, table_id, *, version=None, expect=200):
    expected = version if version is not None else booking["version"]
    response = venue.client.delete(
        f"{BOOKINGS_URL}/{booking['id']}/tables/{table_id}?expected_version={expected}",
        headers=venue.headers(),
    )
    assert response.status_code == expect, response.text
    return response.json()


def replace_tables(venue, booking, from_ids, to_ids, *, version=None, expect=200):
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/replace-table",
        headers=venue.headers(),
        json={
            "expected_version": version if version is not None else booking["version"],
            "from_table_ids": from_ids,
            "to_table_ids": to_ids,
        },
    )
    assert response.status_code == expect, response.text
    return response.json()


def change_end(venue, booking, ends_at, *, starts_at=None, version=None, expect=200):
    body = {
        "expected_version": version if version is not None else booking["version"],
        "ends_at": ends_at,
    }
    if starts_at is not None:
        body["starts_at"] = starts_at
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/change-time", headers=venue.headers(), json=body
    )
    assert response.status_code == expect, response.text
    return response.json()


def open_booking(venue, booking):
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/open",
        headers=venue.headers(),
        json={"expected_version": booking["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _occupancies(session_factory, booking_id):
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(TableOccupancy)
                    .where(TableOccupancy.booking_id == booking_id)
                    .order_by(TableOccupancy.id)
                )
            ).all()
        )


async def _live(session_factory, booking_id):
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(BookingLiveTable)
                    .where(BookingLiveTable.booking_id == booking_id)
                    .order_by(BookingLiveTable.table_id)
                )
            ).all()
        )


async def _events(session_factory, booking_id):
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(BookingEvent)
                    .where(BookingEvent.booking_id == booking_id)
                    .order_by(BookingEvent.id)
                )
            ).all()
        )


# --- multi-table create/read (§1.3, §14) ------------------------------------


async def test_create_multi_table_reads_full_set(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path, capacity=2, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:2], party_size=4)
    assert booking["table_ids"] == sorted(venue.table_ids[:2])
    assert booking["version"] == 1
    occ = await _occupancies(session_factory, booking["id"])
    assert sorted(o.table_id for o in occ) == sorted(venue.table_ids[:2])
    assert all(o.is_active for o in occ)


# --- add (§4, §20.1/§20.2) --------------------------------------------------


async def test_add_table_before_start_creates_full_interval_occupancy(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 17, 0))  # before the 18:00 start
    result = add_tables(venue, booking, venue.table_ids[1:2])
    assert result["table_ids"] == sorted(venue.table_ids[:2])
    assert result["version"] == 2
    occ = {o.table_id: o for o in await _occupancies(session_factory, booking["id"])}
    added = occ[venue.table_ids[1]]
    assert added.is_active is True
    assert added.starts_at == _dt(venue, 18)
    assert added.ends_at == _dt(venue, 20)
    events = await _events(session_factory, booking["id"])
    assert events[-1].event_type == "TABLE_ADDED"
    assert events[-1].payload["to_table_ids"] == sorted(venue.table_ids[:2])


async def test_add_table_inside_interval_starts_at_now(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    now = clock_at(monkeypatch, _dt(venue, 18, 30))
    result = add_tables(venue, booking, venue.table_ids[1:2])
    assert result["table_ids"] == sorted(venue.table_ids[:2])
    occ = {o.table_id: o for o in await _occupancies(session_factory, booking["id"])}
    assert occ[venue.table_ids[1]].starts_at == now
    assert occ[venue.table_ids[1]].ends_at == _dt(venue, 20)


async def test_add_archived_table_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    assert (
        api_client.post(
            f"{TABLES_URL}/{venue.table_ids[1]}/archive", headers=venue.headers()
        ).status_code
        == 200
    )
    clock_at(monkeypatch, _dt(venue, 17, 0))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={"expected_version": 1, "table_ids": venue.table_ids[1:2]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TABLE_NOT_BOOKABLE"


async def test_add_duplicate_or_already_assigned_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 17, 0))
    duplicate = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={"expected_version": 1, "table_ids": [venue.table_ids[1], venue.table_ids[1]]},
    )
    assert duplicate.status_code == 422
    already = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={"expected_version": 1, "table_ids": venue.table_ids[:1]},
    )
    assert already.status_code == 422


# --- remove (§5, §20.5) -----------------------------------------------------


async def test_remove_table_deactivates_only_that_occupancy(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, capacity=2, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:2], party_size=4)
    clock_at(monkeypatch, _dt(venue, 17, 0))
    # party 4 needs both tables, so first lower the party to 2.
    lowered = venue.client.patch(
        f"{BOOKINGS_URL}/{booking['id']}",
        headers=venue.headers(),
        json={"expected_version": 1, "party_size": 2},
    ).json()
    result = remove_table(venue, lowered, venue.table_ids[1])
    assert result["table_ids"] == [venue.table_ids[0]]
    occ = {o.table_id: o for o in await _occupancies(session_factory, booking["id"])}
    assert occ[venue.table_ids[1]].is_active is False
    assert occ[venue.table_ids[0]].is_active is True
    events = await _events(session_factory, booking["id"])
    assert events[-1].event_type == "TABLE_REMOVED"


async def test_remove_last_table_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 17, 0))
    response = venue.client.delete(
        f"{BOOKINGS_URL}/{booking['id']}/tables/{venue.table_ids[0]}?expected_version=1",
        headers=venue.headers(),
    )
    assert response.status_code == 422


async def test_remove_unassigned_table_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 17, 0))
    response = venue.client.delete(
        f"{BOOKINGS_URL}/{booking['id']}/tables/{venue.table_ids[1]}?expected_version=1",
        headers=venue.headers(),
    )
    assert response.status_code == 422


async def test_remove_leaving_insufficient_capacity_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, capacity=2, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:2], party_size=4)
    clock_at(monkeypatch, _dt(venue, 17, 0))
    response = venue.client.delete(
        f"{BOOKINGS_URL}/{booking['id']}/tables/{venue.table_ids[1]}?expected_version=1",
        headers=venue.headers(),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "BOOKING_RULE_VIOLATION"


# --- replace / reseat (§6, §20.6, §28) --------------------------------------


async def test_replace_table_is_atomic(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 17, 0))
    result = replace_tables(venue, booking, [venue.table_ids[0]], [venue.table_ids[1]])
    assert result["table_ids"] == [venue.table_ids[1]]
    assert result["version"] == 2
    occ = {o.table_id: o for o in await _occupancies(session_factory, booking["id"])}
    assert occ[venue.table_ids[0]].is_active is False
    assert occ[venue.table_ids[1]].is_active is True
    events = await _events(session_factory, booking["id"])
    assert events[-1].event_type == "TABLE_REPLACED"
    assert events[-1].payload["from_table_ids"] == [venue.table_ids[0]]
    assert events[-1].payload["to_table_ids"] == [venue.table_ids[1]]
    # The freed table is immediately bookable again.
    venue.create(start=venue.at(18), end=venue.at(20), table_ids=venue.table_ids[:1])


async def test_replace_conflict_rolls_back_the_old_assignment(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, tables=3)
    a = create(venue, table_ids=venue.table_ids[:1])
    create(venue, table_ids=venue.table_ids[1:2])  # holds the target table
    clock_at(monkeypatch, _dt(venue, 17, 0))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{a['id']}/replace-table",
        headers=venue.headers(),
        json={
            "expected_version": a["version"],
            "from_table_ids": venue.table_ids[:1],
            "to_table_ids": venue.table_ids[1:2],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_CONFLICT"
    view = venue.client.get(f"{BOOKINGS_URL}/{a['id']}", headers=venue.headers()).json()
    assert view["table_ids"] == [venue.table_ids[0]]
    assert view["version"] == 1
    occ = await _occupancies(session_factory, a["id"])
    assert [o.table_id for o in occ] == [venue.table_ids[0]]


async def test_reseat_full_set_is_one_atomic_operation(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, capacity=2, tables=4)
    booking = create(venue, table_ids=venue.table_ids[:2], party_size=4)
    clock_at(monkeypatch, _dt(venue, 17, 0))
    result = replace_tables(venue, booking, venue.table_ids[:2], venue.table_ids[2:4])
    assert result["table_ids"] == sorted(venue.table_ids[2:4])
    active = [o for o in await _occupancies(session_factory, booking["id"]) if o.is_active]
    assert sorted(o.table_id for o in active) == sorted(venue.table_ids[2:4])


# --- OPEN live reseat (§11, §20.2, §20.3) -----------------------------------


async def test_open_add_table_updates_live_set(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    assert opened["live_table_ids"] == [venue.table_ids[0]]
    result = add_tables(venue, opened, venue.table_ids[1:2])
    assert result["table_ids"] == sorted(venue.table_ids[:2])
    assert result["live_table_ids"] == sorted(venue.table_ids[:2])
    live = await _live(session_factory, booking["id"])
    assert sorted(row.table_id for row in live) == sorted(venue.table_ids[:2])


async def test_open_remove_table_updates_live_set(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, capacity=2, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:2], party_size=4)
    lowered = venue.client.patch(
        f"{BOOKINGS_URL}/{booking['id']}",
        headers=venue.headers(),
        json={"expected_version": 1, "party_size": 2},
    ).json()
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, lowered)
    result = remove_table(venue, opened, venue.table_ids[1])
    assert result["table_ids"] == [venue.table_ids[0]]
    assert result["live_table_ids"] == [venue.table_ids[0]]
    live = await _live(session_factory, booking["id"])
    assert [row.table_id for row in live] == [venue.table_ids[0]]


async def test_open_reseat_truncates_plan_and_moves_live(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    now = clock_at(monkeypatch, _dt(venue, 18, 40))
    result = replace_tables(venue, opened, venue.table_ids[:1], venue.table_ids[1:2])
    assert result["live_table_ids"] == [venue.table_ids[1]]
    occ = {o.table_id: o for o in await _occupancies(session_factory, booking["id"])}
    old = occ[venue.table_ids[0]]
    assert old.is_active is False and old.ends_at == now
    new = occ[venue.table_ids[1]]
    assert new.is_active is True and new.starts_at == now and new.ends_at == _dt(venue, 20)
    events = await _events(session_factory, booking["id"])
    assert events[-1].event_type == "TABLE_REPLACED"
    assert events[-1].payload["live"] is True


async def test_open_reseat_conflict_on_new_table_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    a = create(venue, table_ids=venue.table_ids[:1])
    b = create(venue, table_ids=venue.table_ids[1:2])
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, a)
    open_booking(venue, b)
    response = venue.client.post(
        f"{BOOKINGS_URL}/{a['id']}/replace-table",
        headers=venue.headers(),
        json={
            "expected_version": opened["version"],
            "from_table_ids": venue.table_ids[:1],
            "to_table_ids": venue.table_ids[1:2],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] in ("TABLE_LIVE_CONFLICT", "BOOKING_CONFLICT")


async def test_open_after_plan_end_cannot_change_tables(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    clock_at(monkeypatch, _dt(venue, 20, 0))  # exactly at plan end
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={"expected_version": opened["version"], "table_ids": venue.table_ids[1:2]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_INVALID_STATE"


async def test_previous_shift_open_cannot_change_tables(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    clock_at(monkeypatch, _dt(venue, 12, 0, day_offset=1))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={"expected_version": opened["version"], "table_ids": venue.table_ids[1:2]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_INVALID_STATE"


async def test_expired_new_cannot_change_tables(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 20, 30))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={"expected_version": booking["version"], "table_ids": venue.table_ids[1:2]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_INVALID_STATE"


# --- end-time change (§14, §26) ---------------------------------------------


async def test_new_end_only_extension(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    result = change_end(venue, booking, venue.at(21))
    assert result["version"] == 2
    assert datetime.fromisoformat(result["ends_at"]) == datetime.fromisoformat(venue.at(21))
    active = [o for o in await _occupancies(session_factory, booking["id"]) if o.is_active]
    assert len(active) == 1 and active[0].ends_at == _dt(venue, 21)
    events = await _events(session_factory, booking["id"])
    assert events[-1].event_type == "TIME_CHANGED"


async def test_new_expired_end_only_requires_full_reschedule(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 20, 30))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/change-time",
        headers=venue.headers(),
        json={"expected_version": booking["version"], "ends_at": venue.at(22)},
    )
    assert response.status_code == 422


async def test_open_extend_end_ok(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    clock_at(monkeypatch, _dt(venue, 18, 40))
    result = change_end(venue, opened, venue.at(21))
    assert datetime.fromisoformat(result["ends_at"]) == datetime.fromisoformat(venue.at(21))
    active = [o for o in await _occupancies(session_factory, booking["id"]) if o.is_active]
    assert active[0].ends_at == _dt(venue, 21)
    # The extended tail is now reserved.
    conflict = venue.create(start=venue.at(20, 30), end=venue.at(22), expect=409)
    assert conflict.json()["code"] == "BOOKING_CONFLICT"


async def test_open_extend_conflict_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    a = create(venue)
    venue.create(start=venue.at(20), end=venue.at(22), table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, a)
    clock_at(monkeypatch, _dt(venue, 18, 40))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{a['id']}/change-time",
        headers=venue.headers(),
        json={"expected_version": opened["version"], "ends_at": venue.at(21)},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_CONFLICT"
    view = venue.client.get(f"{BOOKINGS_URL}/{a['id']}", headers=venue.headers()).json()
    assert datetime.fromisoformat(view["ends_at"]) == datetime.fromisoformat(venue.at(20))


async def test_open_shorten_end_frees_tail(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    clock_at(monkeypatch, _dt(venue, 18, 40))
    result = change_end(venue, opened, venue.at(19))
    assert datetime.fromisoformat(result["ends_at"]) == datetime.fromisoformat(venue.at(19))
    occ = await _occupancies(session_factory, booking["id"])
    assert [o for o in occ if o.is_active][0].ends_at == _dt(venue, 19)
    # The freed tail is bookable again.
    venue.create(start=venue.at(19), end=venue.at(21), table_ids=venue.table_ids[:1])


async def test_open_end_change_cannot_move_start(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    clock_at(monkeypatch, _dt(venue, 18, 40))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/change-time",
        headers=venue.headers(),
        json={
            "expected_version": opened["version"],
            "starts_at": venue.at(18, 30),
            "ends_at": venue.at(21),
        },
    )
    assert response.status_code == 422


async def test_open_end_change_after_plan_end_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    clock_at(monkeypatch, _dt(venue, 20, 1))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/change-time",
        headers=venue.headers(),
        json={"expected_version": opened["version"], "ends_at": venue.at(21)},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_INVALID_STATE"


async def test_open_end_change_not_grid_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path, tables=1)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = open_booking(venue, booking)
    clock_at(monkeypatch, _dt(venue, 18, 40))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/change-time",
        headers=venue.headers(),
        json={"expected_version": opened["version"], "ends_at": venue.at(21, 3)},
    )
    assert response.status_code == 422


# --- optimistic concurrency (§33) -------------------------------------------


async def test_stale_table_mutation_leaves_no_partial_write(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue, table_ids=venue.table_ids[:1])
    clock_at(monkeypatch, _dt(venue, 17, 0))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=venue.headers(),
        json={"expected_version": 99, "table_ids": venue.table_ids[1:2]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_STALE"
    occ = await _occupancies(session_factory, booking["id"])
    assert [o.table_id for o in occ] == [venue.table_ids[0]]


# --- tenant isolation (§7.1, §54.14) ----------------------------------------


async def test_table_mutations_are_tenant_isolated(api_client, tmp_path, monkeypatch):
    a = Venue(api_client, tmp_path)
    b = Venue(api_client, tmp_path)
    foreign = create(b, table_ids=b.table_ids[:1])
    clock_at(monkeypatch, _dt(b, 17, 0))
    add = a.client.post(
        f"{BOOKINGS_URL}/{foreign['id']}/tables",
        headers=a.headers(),
        json={"expected_version": 1, "table_ids": b.table_ids[1:2]},
    )
    assert add.status_code == 404
    delete = a.client.delete(
        f"{BOOKINGS_URL}/{foreign['id']}/tables/{b.table_ids[0]}?expected_version=1",
        headers=a.headers(),
    )
    assert delete.status_code == 404
    replace = a.client.post(
        f"{BOOKINGS_URL}/{foreign['id']}/replace-table",
        headers=a.headers(),
        json={
            "expected_version": 1,
            "from_table_ids": b.table_ids[:1],
            "to_table_ids": b.table_ids[1:2],
        },
    )
    assert replace.status_code == 404


async def test_add_table_from_another_venue_is_404(api_client, tmp_path, monkeypatch):
    a = Venue(api_client, tmp_path)
    b = Venue(api_client, tmp_path)
    booking = create(a, table_ids=a.table_ids[:1])
    clock_at(monkeypatch, _dt(a, 17, 0))
    response = a.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/tables",
        headers=a.headers(),
        json={"expected_version": 1, "table_ids": b.table_ids[1:2]},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "TABLE_NOT_FOUND"


# --- capacity-change interaction (§29.4) ------------------------------------


@pytest.mark.parametrize("status", ["NEW", "WAITING"])
@pytest.mark.parametrize("end_hour", [19, 21])
async def test_end_only_after_replace_preserves_segment_start(
    api_client, tmp_path, monkeypatch, session_factory, status, end_hour
):
    venue = Venue(api_client, tmp_path, tables=2)
    booking = create(venue)
    venue.create(start=venue.at(18), end=venue.at(18, 45), table_ids=venue.table_ids[1:2])
    now = clock_at(monkeypatch, _dt(venue, 19))
    if status == "WAITING":
        response = venue.client.post(
            f"{BOOKINGS_URL}/{booking['id']}/wait",
            headers=venue.headers(),
            json={"expected_version": booking["version"]},
        )
        assert response.status_code == 200, response.text
        booking = response.json()
    booking = replace_tables(venue, booking, venue.table_ids[:1], venue.table_ids[1:2])
    end = venue.at(end_hour, 30)
    result = change_end(venue, booking, end)
    assert result["status"] == status
    rows = await _occupancies(session_factory, booking["id"])
    active = [row for row in rows if row.is_active]
    assert len(active) == 1
    assert active[0].table_id == venue.table_ids[1]
    assert active[0].starts_at == now
    assert active[0].ends_at == _dt(venue, end_hour, 30)


async def test_capacity_decrease_stranding_multitable_booking_is_blocked(api_client, tmp_path):
    """A layout-save capacity decrease below a multi-table booking's sum is blocked (§29.4)."""
    venue = Venue(api_client, tmp_path, capacity=2, tables=2)
    create(venue, table_ids=venue.table_ids[:2], party_size=4)
    hall = api_client.get(f"{TABLES_URL}", headers=venue.headers()).json()["tables"]
    hall_id = hall[0]["hall_id"]
    detail = api_client.get(f"/api/admin/v1/halls/{hall_id}", headers=venue.headers()).json()
    payload = {
        "expected_revision": detail["layout_revision"],
        "canvas_width": detail["canvas_width"],
        "canvas_height": detail["canvas_height"],
        "tables": [
            {
                "id": t["id"],
                "number": t["number"],
                "capacity": 1,
                "shape": t["shape"],
                "x": t["x"],
                "y": t["y"],
                "width": t["width"],
                "height": t["height"],
                "rotation": t["rotation"],
                "z_index": t["z_index"],
            }
            for t in detail["tables"]
        ],
        "static_elements": detail["static_elements"],
    }
    response = api_client.put(
        f"/api/admin/v1/halls/{hall_id}/layout", headers=venue.headers(), json=payload
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CAPACITY_CHANGE_BLOCKED"
