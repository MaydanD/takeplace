"""Stage 8 lifecycle API acceptance tests against real PostgreSQL (PROJECT-SPEC §21-§27, §61).

Covers permitted/forbidden transitions, version increments, lifecycle timestamps,
append-only events, live-row mutation, early OPEN, UNDO_OPEN targets, CLOSE
truncation, overdue OPEN/WAITING, unresolved previous shifts, current-vs-previous
business date, tenant isolation and the Stage 7 WAITING change-time regression.

Time is controlled deterministically by monkeypatching the single DB-time helper
``app.services.bookings.operation_now`` (§55), never by sleeping.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from app.db.models import BookingEvent, BookingLiveTable, TableOccupancy
from sqlalchemy import select, text

from tests.integration.support import BOOKINGS_URL
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


def _same_instant(a: str, b: datetime) -> bool:
    """Compare an API ISO timestamp (UTC) with a MSK datetime by instant."""
    return datetime.fromisoformat(a) == b


def create(venue, **kwargs):
    return venue.create(start=venue.at(18), end=venue.at(20), **kwargs).json()


def lifecycle(venue, booking, action, *, version=None, expect=200):
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/{action}",
        headers=venue.headers(),
        json={"expected_version": version if version is not None else booking["version"]},
    )
    assert response.status_code == expect, response.text
    return response.json()


def patch(venue, booking, **changes):
    return venue.client.patch(
        f"{BOOKINGS_URL}/{booking['id']}",
        headers=venue.headers(),
        json={"expected_version": booking["version"], **changes},
    )


def clock_at(monkeypatch, moment):
    async def clock(_session):
        return moment

    monkeypatch.setattr("app.services.bookings.operation_now", clock)
    return moment


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


async def _live(session_factory, booking_id):
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(BookingLiveTable).where(BookingLiveTable.booking_id == booking_id)
                )
            ).all()
        )


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


# --- WAITING (§21) ----------------------------------------------------------


async def test_new_to_waiting_sets_timestamp_and_event(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    now = clock_at(monkeypatch, _dt(venue, 18, 30))
    result = lifecycle(venue, booking, "wait")
    assert result["status"] == "WAITING"
    assert result["version"] == 2
    assert result["waiting_at"] is not None
    assert "open" in result["available_actions"] and "cancel" in result["available_actions"]
    events = await _events(session_factory, booking["id"])
    assert [e.event_type for e in events] == ["BOOKING_CREATED", "WAITING_SET"]
    assert events[-1].created_at == now


async def test_waiting_before_start_is_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 17, 0))
    lifecycle(venue, booking, "wait", expect=422)


async def test_waiting_after_end_is_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 20, 1))
    lifecycle(venue, booking, "wait", expect=422)


async def test_waiting_from_waiting_is_invalid_state(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    waiting = lifecycle(venue, booking, "wait")
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/wait",
        headers=venue.headers(),
        json={"expected_version": waiting["version"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_INVALID_STATE"


# --- OPEN (§22) -------------------------------------------------------------


async def test_new_to_open_creates_live_rows_and_event(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    now = clock_at(monkeypatch, _dt(venue, 18, 30))
    result = lifecycle(venue, booking, "open")
    assert result["status"] == "OPEN"
    assert result["version"] == 2
    assert result["live_table_ids"] == venue.table_ids[:1]
    assert result["opened_at"] is not None
    assert result["waiting_at"] is None
    assert "close" in result["available_actions"]
    assert "undo-open" in result["available_actions"]

    events = await _events(session_factory, booking["id"])
    assert [e.event_type for e in events] == ["BOOKING_CREATED", "BOOKING_OPENED"]
    assert events[-1].payload["previous_status"] == "NEW"
    assert events[-1].created_at == now
    live = await _live(session_factory, booking["id"])
    assert [row.table_id for row in live] == venue.table_ids[:1]
    assert all(row.live_since == now for row in live)


async def test_waiting_to_open(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    waiting = lifecycle(venue, booking, "wait")
    result = lifecycle(venue, waiting, "open")
    assert result["status"] == "OPEN"
    assert result["version"] == 3
    assert result["waiting_at"] is not None  # §10: OPEN may keep waiting_at


async def test_open_before_shift_start_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 15, 30))
    lifecycle(venue, booking, "open", expect=422)


async def test_open_after_plan_end_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 20, 1))
    lifecycle(venue, booking, "open", expect=422)


async def test_early_open_before_plan_start_allowed(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    now = clock_at(monkeypatch, _dt(venue, 17, 30))  # shift open, plan not started
    result = lifecycle(venue, booking, "open")
    assert result["status"] == "OPEN"
    assert _same_instant(result["starts_at"], _dt(venue, 18))  # plan start unchanged (§22.2)
    live = await _live(session_factory, booking["id"])
    assert all(row.live_since == now for row in live)


async def test_open_repeat_is_invalid_or_stale(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    lifecycle(venue, opened, "open", expect=409)  # invalid state at current version
    lifecycle(venue, opened, "open", version=booking["version"], expect=409)  # stale


async def test_open_stale_version(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/open",
        headers=venue.headers(),
        json={"expected_version": 999},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_STALE"


async def test_open_live_conflict_rejected(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path, tables=2)
    first = create(venue, table_ids=venue.table_ids[:1])
    # The second booking sits on table[1] at an adjacent (non-overlapping) interval
    # so moving its plan to table[0] does not break the plan exclusion constraint.
    second = venue.create(
        start=venue.at(20), end=venue.at(22), table_ids=venue.table_ids[1:2]
    ).json()
    clock_at(monkeypatch, _dt(venue, 18, 30))
    lifecycle(venue, first, "open")
    # Reassign the second booking's plan to the table that is already live today.
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE table_occupancies SET table_id = :t WHERE booking_id = :b"),
            {"t": venue.table_ids[0], "b": second["id"]},
        )
    clock_at(monkeypatch, _dt(venue, 20, 30))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{second['id']}/open",
        headers=venue.headers(),
        json={"expected_version": second["version"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "TABLE_LIVE_CONFLICT"


# --- UNDO_OPEN (§23) --------------------------------------------------------


async def test_undo_open_restores_new(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    result = lifecycle(venue, opened, "undo-open")
    assert result["status"] == "NEW"
    assert result["version"] == 3
    assert result["opened_at"] is None
    assert result["live_table_ids"] == []

    events = await _events(session_factory, booking["id"])
    assert [e.event_type for e in events] == [
        "BOOKING_CREATED",
        "BOOKING_OPENED",
        "OPEN_UNDONE",
    ]
    assert events[-1].payload == {"restored_status": "NEW"}
    assert await _live(session_factory, booking["id"]) == []


async def test_undo_open_restores_waiting(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    waiting = lifecycle(venue, booking, "wait")
    opened = lifecycle(venue, waiting, "open")
    result = lifecycle(venue, opened, "undo-open")
    assert result["status"] == "WAITING"
    assert result["waiting_at"] is not None
    assert result["opened_at"] is None


async def test_undo_open_after_plan_end_rejected(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    clock_at(monkeypatch, _dt(venue, 20, 1))
    lifecycle(venue, opened, "undo-open", expect=422)


async def test_undo_open_blocked_by_party_size_edit(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    edited = patch(venue, opened, party_size=4).json()
    assert "undo-open" not in edited["available_actions"]
    lifecycle(venue, edited, "undo-open", expect=422)


async def test_undo_open_preserved_by_text_edit(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    edited = patch(venue, opened, guest_name="Тихо").json()
    assert "undo-open" in edited["available_actions"]
    assert lifecycle(venue, edited, "undo-open")["status"] == "NEW"


async def test_undo_open_from_non_open_invalid(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    lifecycle(venue, booking, "undo-open", expect=409)


async def test_undo_open_repeat_invalid(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    undone = lifecycle(venue, opened, "undo-open")
    lifecycle(venue, undone, "undo-open", expect=409)  # already NEW


# --- CLOSE (§24) ------------------------------------------------------------


async def test_close_inside_interval_truncates_occupancy(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    closed_at = clock_at(monkeypatch, _dt(venue, 19, 7))
    result = lifecycle(venue, opened, "close")
    assert result["status"] == "CLOSED"
    assert result["version"] == 3
    assert result["closed_at"] is not None
    assert result["live_table_ids"] == []
    assert _same_instant(result["ends_at"], _dt(venue, 20))  # plan end unchanged

    occ = await _occupancies(session_factory, booking["id"])
    assert len(occ) == 1
    # §24: an in-progress segment is truncated at closed_at and stays factually
    # active until then (only a still-future segment is deactivated).
    assert occ[0].is_active is True
    assert occ[0].starts_at == _dt(venue, 18)
    assert occ[0].ends_at == closed_at
    assert _same_instant(result["closed_at"], closed_at)
    assert await _live(session_factory, booking["id"]) == []
    events = await _events(session_factory, booking["id"])
    assert [e.event_type for e in events][-1] == "BOOKING_CLOSED"


async def test_close_after_plan_end_keeps_segment(
    api_client, tmp_path, monkeypatch, session_factory
):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    clock_at(monkeypatch, _dt(venue, 21, 0))
    assert lifecycle(venue, opened, "close")["status"] == "CLOSED"
    occ = await _occupancies(session_factory, booking["id"])
    assert occ[0].ends_at == _dt(venue, 20)  # ends_at <= closed_at -> unchanged


async def test_close_from_new_invalid(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    lifecycle(venue, booking, "close", expect=409)


async def test_close_repeat_invalid_and_stale(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    closed = lifecycle(venue, opened, "close")
    lifecycle(venue, closed, "close", expect=409)
    lifecycle(venue, closed, "close", version=opened["version"], expect=409)


# --- CANCEL vs Stage 8 states (§25) -----------------------------------------


async def test_cancel_open_forbidden(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/cancel",
        headers=venue.headers(),
        json={"expected_version": opened["version"], "reason": "OTHER"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BOOKING_INVALID_STATE"


async def test_cancel_waiting_releases_plan(api_client, tmp_path, monkeypatch, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    waiting = lifecycle(venue, booking, "wait")
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/cancel",
        headers=venue.headers(),
        json={"expected_version": waiting["version"], "reason": "NO_SHOW"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELED"
    occ = await _occupancies(session_factory, booking["id"])
    assert all(o.is_active is False for o in occ)
    assert await _live(session_factory, booking["id"]) == []


async def test_cancel_overdue_new_allowed(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 21, 0))
    response = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/cancel",
        headers=venue.headers(),
        json={"expected_version": 1, "reason": "NO_SHOW"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELED"


# --- previous shift / unresolved (§17.3, §61) -------------------------------


async def test_previous_shift_open_is_unresolved_and_closeable(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    clock_at(monkeypatch, _dt(venue, 12, 0, day_offset=1))  # after shift end (02:00 +1)
    view = venue.client.get(f"{BOOKINGS_URL}/{booking['id']}", headers=venue.headers()).json()
    assert view["status"] == "OPEN"
    assert view["is_previous_shift"] is True
    assert view["available_actions"] == ["close"]
    unresolved = api_client.get(f"{BOOKINGS_URL}/unresolved", headers=venue.headers()).json()[
        "items"
    ]
    assert [b["id"] for b in unresolved] == [booking["id"]]
    assert lifecycle(venue, opened, "close")["status"] == "CLOSED"


async def test_previous_shift_guest_edit_text_only(api_client, tmp_path, monkeypatch):
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened = lifecycle(venue, booking, "open")
    clock_at(monkeypatch, _dt(venue, 12, 0, day_offset=1))
    result = patch(venue, opened, guest_name="Поздний гость")
    assert result.status_code == 200, result.text
    forbidden = venue.client.patch(
        f"{BOOKINGS_URL}/{booking['id']}",
        headers=venue.headers(),
        json={"expected_version": result.json()["version"], "party_size": 4},
    )
    assert forbidden.status_code == 422


async def test_new_visit_on_same_table_next_business_date(
    api_client, tmp_path, monkeypatch, session_factory
):
    """A stale OPEN of D1 must not block a new visit of D2 on the same table (§61)."""
    venue = Venue(api_client, tmp_path, tables=1)
    d1 = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    opened_d1 = lifecycle(venue, d1, "open")

    next_day = venue.business_date + timedelta(days=1)
    venue._open_day(next_day)
    # Create D2 with a clock just before its plan so the create is valid.
    clock_at(monkeypatch, _dt(venue, 17, 0, day_offset=1))
    d2 = venue.create(start=venue.at(18, day_offset=1), end=venue.at(20, day_offset=1)).json()
    clock_at(monkeypatch, _dt(venue, 18, 30, day_offset=1))
    opened_d2 = lifecycle(venue, d2, "open")
    assert opened_d2["status"] == "OPEN"

    async with session_factory() as session:
        live = list(
            (
                await session.scalars(
                    select(BookingLiveTable).where(BookingLiveTable.venue_id == venue.venue_id)
                )
            ).all()
        )
    assert {(row.booking_id, row.business_date) for row in live} == {
        (d1["id"], venue.business_date),
        (d2["id"], next_day),
    }

    # Closing the D1 visit must not touch D2's live row.
    assert lifecycle(venue, opened_d1, "close")["status"] == "CLOSED"
    assert [row.table_id for row in await _live(session_factory, d2["id"])] == venue.table_ids[:1]
    still_open = venue.client.get(f"{BOOKINGS_URL}/{d2['id']}", headers=venue.headers()).json()
    assert still_open["status"] == "OPEN"


# --- tenant isolation -------------------------------------------------------


async def test_lifecycle_cross_tenant_404(api_client, tmp_path, monkeypatch):
    a, b = Venue(api_client, tmp_path), Venue(api_client, tmp_path)
    foreign = create(b)
    clock_at(monkeypatch, _dt(b, 18, 30))
    for action in ("wait", "open", "undo-open", "close"):
        response = a.client.post(
            f"{BOOKINGS_URL}/{foreign['id']}/{action}",
            headers=a.headers(),
            json={"expected_version": 1},
        )
        assert response.status_code == 404, action


# --- Stage 7 regression: WAITING + change-time future -> NEW (§26.1) --------


async def test_waiting_change_time_future_resets_to_new(
    api_client, tmp_path, monkeypatch, session_factory
):
    """A WAITING booking moved to a future interval returns to NEW (§26.1)."""
    venue = Venue(api_client, tmp_path)
    booking = create(venue)
    clock_at(monkeypatch, _dt(venue, 18, 30))
    waiting = lifecycle(venue, booking, "wait")
    assert waiting["status"] == "WAITING" and waiting["waiting_at"] is not None

    clock_at(monkeypatch, _dt(venue, 18, 40))
    result = venue.client.post(
        f"{BOOKINGS_URL}/{booking['id']}/change-time",
        headers=venue.headers(),
        json={
            "expected_version": waiting["version"],
            "starts_at": venue.at(19),
            "ends_at": venue.at(21),
        },
    )
    assert result.status_code == 200, result.text
    moved = result.json()
    assert moved["status"] == "NEW"
    assert moved["waiting_at"] is None
    assert moved["version"] == waiting["version"] + 1

    events = await _events(session_factory, booking["id"])
    assert [e.event_type for e in events] == [
        "BOOKING_CREATED",
        "WAITING_SET",
        "TIME_CHANGED",
    ]
    active = [o for o in await _occupancies(session_factory, booking["id"]) if o.is_active]
    assert len(active) == 1
    assert active[0].starts_at == _dt(venue, 19)
    assert active[0].ends_at == _dt(venue, 21)
