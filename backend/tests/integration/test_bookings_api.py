"""Stage 5 integration tests: booking create/cancel/change-time (PROJECT-SPEC §18, §19, §29, §32).

Run against a real PostgreSQL migrated from zero. They cover the booking-core
acceptance criterion, tenant isolation, the idempotency semantics, the schedule
snapshot, the cancellation/occupancy rules, the new archive and capacity guards
and — most importantly — that the database itself is the last arbiter.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.security.cookies import SESSION_COOKIE_NAME
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.support import (
    APP_URL,
    BOOKINGS_URL,
    HALLS_URL,
    ME_URL,
    ORIGIN,
    PASSWORD,
    SCHEDULE_EXCEPTIONS_URL,
    TABLES_URL,
    cookie_header,
    create_venue,
    import_layout_cli,
    login,
    make_client,
    unique,
)

pytestmark = pytest.mark.integration

MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture(autouse=True)
def _require_test_db() -> None:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    with make_client() as client:
        yield client


@pytest.fixture
async def app_session() -> AsyncIterator[AsyncSession]:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    engine = create_async_engine(APP_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# --- helpers ----------------------------------------------------------------


def _login(client: TestClient, login_name: str) -> str:
    response = login(client, login_name, PASSWORD)
    assert response.status_code == 200, response.text
    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token
    client.cookies.clear()
    return token


def _write(tmp_path: Path, payload: dict, name: str = "layout.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _layout(capacity: int = 4, tables: int = 3) -> dict:
    return {
        "halls": [
            {
                "name": "Зал",
                "canvas_width": 640,
                "canvas_height": 480,
                "tables": [
                    {
                        "number": str(i + 1),
                        "capacity": capacity,
                        "shape": "rect",
                        "x": 10 + i * 90,
                        "y": 10,
                        "width": 80,
                        "height": 80,
                    }
                    for i in range(tables)
                ],
            }
        ]
    }


class Venue:
    """A set-up venue with an open future day and imported tables."""

    def __init__(self, client: TestClient, tmp_path: Path, *, capacity: int = 4, tables: int = 3):
        self.client = client
        self.slug, login_name = unique("venue"), unique("admin")
        assert create_venue(self.slug, login_name).returncode == 0
        self.token = _login(client, login_name)
        assert (
            import_layout_cli(self.slug, _write(tmp_path, _layout(capacity, tables))).returncode
            == 0
        )
        self.business_date = datetime.now(MSK).date() + timedelta(days=1)
        self._open_day(self.business_date)
        self.table_ids = [
            t["id"]
            for t in client.get(TABLES_URL, headers=cookie_header(self.token)).json()["tables"]
        ]
        self.venue_id = int(
            client.get(ME_URL, headers=cookie_header(self.token)).json()["venue"]["id"]
        )

    def headers(self, **extra: str) -> dict[str, str]:
        return {**cookie_header(self.token), "Origin": ORIGIN, **extra}

    def _open_day(self, business_date, *, confirm: bool = False) -> None:
        params = {"confirm": "true"} if confirm else None
        response = self.client.put(
            f"{SCHEDULE_EXCEPTIONS_URL}/{business_date.isoformat()}",
            json={"is_closed": False, "open_time": "16:00", "close_time": "02:00"},
            headers=self.headers(),
            params=params,
        )
        assert response.status_code == 200, response.text

    def at(self, hour: int, minute: int = 0, *, day_offset: int = 0) -> str:
        day = self.business_date + timedelta(days=day_offset)
        return (
            datetime.combine(day, datetime.min.time(), tzinfo=MSK)
            .replace(hour=hour, minute=minute)
            .isoformat()
        )

    def create(
        self,
        *,
        start: str,
        end: str,
        table_ids: list[int] | None = None,
        party_size: int = 2,
        source: str = "PHONE",
        phone: str | None = "+79990000000",
        name: str = "Гость",
        key: str | None = None,
        expect: int = 201,
    ):
        response = self.client.post(
            BOOKINGS_URL,
            json={
                "starts_at": start,
                "ends_at": end,
                "table_ids": table_ids or self.table_ids[:1],
                "party_size": party_size,
                "source": source,
                "guest_name": name,
                "guest_phone_raw": phone,
            },
            headers=self.headers(**{"Idempotency-Key": key or str(uuid.uuid4())}),
        )
        assert response.status_code == expect, response.text
        return response


# --- create -----------------------------------------------------------------


def test_create_booking_happy_path(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    response = venue.create(start=venue.at(20), end=venue.at(22))
    body = response.json()
    assert body["number"] == 1
    assert body["status"] == "NEW"
    assert body["source"] == "PHONE"
    assert body["business_date"] == venue.business_date.isoformat()
    assert body["table_ids"] == [venue.table_ids[0]]
    assert body["version"] == 1

    history = api_client.get(
        f"{BOOKINGS_URL}/{body['id']}/history", headers=cookie_header(venue.token)
    ).json()["events"]
    assert [event["event_type"] for event in history] == ["BOOKING_CREATED"]
    assert "guest_phone" not in json.dumps(history)


def test_booking_number_increments_per_venue(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path, tables=3)
    first = venue.create(start=venue.at(18), end=venue.at(20), table_ids=[venue.table_ids[0]])
    second = venue.create(start=venue.at(18), end=venue.at(20), table_ids=[venue.table_ids[1]])
    third = venue.create(start=venue.at(18), end=venue.at(20), table_ids=[venue.table_ids[2]])
    assert [first.json()["number"], second.json()["number"], third.json()["number"]] == [1, 2, 3]


def test_minimum_duration_and_grid_are_rejected(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    venue.create(start=venue.at(20), end=venue.at(20, 40), expect=422)
    venue.create(start=venue.at(20, 3), end=venue.at(22), expect=422)


def test_interval_outside_shift_is_rejected(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    # 15:00 is before the 16:00 shift start.
    venue.create(start=venue.at(15), end=venue.at(17), expect=422)


def test_closed_day_has_no_shift(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    # A further day with no exception resolves to closed.
    venue.create(start=venue.at(20, day_offset=3), end=venue.at(22, day_offset=3), expect=422)


def test_overnight_booking_belongs_to_previous_business_date(
    api_client: TestClient, tmp_path: Path
) -> None:
    venue = Venue(api_client, tmp_path)
    body = venue.create(start=venue.at(1, 0, day_offset=1), end=venue.at(2, 0, day_offset=1)).json()
    assert body["business_date"] == venue.business_date.isoformat()


def test_insufficient_capacity_is_rejected(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path, capacity=2, tables=1)
    venue.create(start=venue.at(20), end=venue.at(22), party_size=3, expect=422)


def test_multiple_tables_sum_capacity(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path, capacity=2, tables=2)
    venue.create(
        start=venue.at(20),
        end=venue.at(22),
        party_size=4,
        table_ids=venue.table_ids[:2],
        expect=201,
    )


def test_archived_or_unbookable_table_is_rejected(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path, tables=2)
    api_client.patch(
        f"{TABLES_URL}/{venue.table_ids[0]}", json={"is_bookable": False}, headers=venue.headers()
    )
    venue.create(start=venue.at(20), end=venue.at(22), table_ids=[venue.table_ids[0]], expect=409)
    api_client.post(f"{TABLES_URL}/{venue.table_ids[1]}/archive", headers=venue.headers())
    venue.create(start=venue.at(20), end=venue.at(22), table_ids=[venue.table_ids[1]], expect=409)


def test_table_of_another_venue_is_a_404(api_client: TestClient, tmp_path: Path) -> None:
    a = Venue(api_client, tmp_path)
    b = Venue(api_client, tmp_path)
    response = api_client.post(
        BOOKINGS_URL,
        json={
            "starts_at": a.at(20),
            "ends_at": a.at(22),
            "table_ids": [b.table_ids[0]],
            "party_size": 2,
            "source": "PHONE",
            "guest_name": "X",
            "guest_phone_raw": "+79990000000",
        },
        headers=a.headers(**{"Idempotency-Key": str(uuid.uuid4())}),
    )
    assert response.status_code == 404


# --- idempotency ------------------------------------------------------------


def test_idempotent_replay_returns_same_booking(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    key = str(uuid.uuid4())
    first = venue.create(start=venue.at(20), end=venue.at(22), key=key)
    replay = venue.create(start=venue.at(20), end=venue.at(22), key=key, expect=200)
    assert replay.json()["id"] == first.json()["id"]
    listing = api_client.get(BOOKINGS_URL, headers=cookie_header(venue.token)).json()["items"]
    assert len(listing) == 1


def test_replay_survives_a_cancel(api_client: TestClient, tmp_path: Path) -> None:
    """A lost response replayed after the admin canceled returns the original (§18.2)."""
    venue = Venue(api_client, tmp_path)
    key = str(uuid.uuid4())
    booking = venue.create(start=venue.at(20), end=venue.at(22), key=key).json()
    api_client.post(
        f"{BOOKINGS_URL}/{booking['id']}/cancel",
        json={"expected_version": 1, "reason": "GUEST_CANCELED"},
        headers=venue.headers(),
    )
    replay = venue.create(start=venue.at(20), end=venue.at(22), key=key, expect=200)
    assert replay.json()["id"] == booking["id"]
    assert replay.json()["status"] == "CANCELED"


def test_same_key_different_payload_is_reused(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    key = str(uuid.uuid4())
    venue.create(start=venue.at(20), end=venue.at(22), key=key)
    response = venue.create(start=venue.at(20), end=venue.at(23), key=key, expect=409)
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_conflict_and_adjacency(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path, tables=1)
    venue.create(start=venue.at(20), end=venue.at(22))
    # 20:00-22:00 vs 21:55-23:00 overlaps -> conflict.
    conflict = venue.create(start=venue.at(21, 55), end=venue.at(23), expect=409)
    assert conflict.json()["code"] == "BOOKING_CONFLICT"
    # 22:00-00:00 is adjacent -> allowed (half-open interval).
    venue.create(start=venue.at(22), end=venue.at(23, 55))


# --- cancel / change-time ---------------------------------------------------


def test_cancel_deactivates_occupancies(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    booking = venue.create(start=venue.at(20), end=venue.at(22)).json()
    canceled = api_client.post(
        f"{BOOKINGS_URL}/{booking['id']}/cancel",
        json={"expected_version": 1, "reason": "NO_SHOW"},
        headers=venue.headers(),
    ).json()
    assert canceled["status"] == "CANCELED"
    assert canceled["version"] == 2
    # The slot is free again.
    venue.create(start=venue.at(20), end=venue.at(22))
    # A second cancel with the stale version fails.
    stale = api_client.post(
        f"{BOOKINGS_URL}/{booking['id']}/cancel",
        json={"expected_version": 1, "reason": "NO_SHOW"},
        headers=venue.headers(),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "BOOKING_STALE"


def test_change_time_moves_occupancy_and_snapshot(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    booking = venue.create(start=venue.at(18), end=venue.at(20)).json()
    moved = api_client.post(
        f"{BOOKINGS_URL}/{booking['id']}/change-time",
        json={"expected_version": 1, "starts_at": venue.at(20), "ends_at": venue.at(22)},
        headers=venue.headers(),
    ).json()
    assert moved["version"] == 2
    assert datetime.fromisoformat(moved["starts_at"]) == datetime.fromisoformat(venue.at(20))
    # The old slot is free, the new one is taken.
    venue.create(start=venue.at(18), end=venue.at(20))
    conflict = venue.create(start=venue.at(20), end=venue.at(22), expect=409)
    assert conflict.json()["code"] == "BOOKING_CONFLICT"


# --- database is the last arbiter -------------------------------------------


async def _booking_row(app_session: AsyncSession, venue: Venue) -> dict:
    row = (
        await app_session.execute(
            text(
                "SELECT venue_id, table_id, starts_at, ends_at FROM table_occupancies "
                "WHERE venue_id = :v AND is_active ORDER BY table_id LIMIT 1"
            ),
            {"v": venue.venue_id},
        )
    ).one()
    return {"venue_id": row[0], "table_id": row[1], "starts_at": row[2], "ends_at": row[3]}


async def test_exclusion_constraint_blocks_direct_overlap(
    api_client: TestClient, tmp_path: Path, app_session: AsyncSession
) -> None:
    venue = Venue(api_client, tmp_path)
    venue.create(start=venue.at(20), end=venue.at(22))
    # A direct INSERT bypassing the application pre-check must be rejected.
    owner = await _booking_row(app_session, venue)
    overlaps = {
        "v": venue.venue_id,
        "t": owner["table_id"],
        "s": owner["starts_at"] + timedelta(hours=1),
        "e": owner["ends_at"] + timedelta(hours=1),
    }
    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "INSERT INTO table_occupancies "
                "(venue_id, table_id, kind, starts_at, ends_at) "
                "VALUES (:v, :t, 'BLOCK', :s, :e)"
            ),
            overlaps,
        )
    assert "occupancy_no_overlap" in str(excinfo.value)
    await app_session.rollback()


async def test_database_rejects_invalid_booking_rows(
    api_client: TestClient, tmp_path: Path, app_session: AsyncSession
) -> None:
    venue = Venue(api_client, tmp_path)
    venue.create(start=venue.at(20), end=venue.at(22))
    owner = await _booking_row(app_session, venue)

    # Non grid-aligned start.
    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "INSERT INTO bookings "
                "(venue_id, number, business_date, shift_starts_at, shift_ends_at, starts_at, "
                "ends_at, party_size, source, status, guest_name, guest_phone_raw, "
                "admin_idempotency_key, admin_request_hmac) "
                "VALUES (:v, 999, :d, :ss, :se, :s, :e, 2, 'PHONE', 'NEW', 'X', '+79990000000', "
                "gen_random_uuid(), 'x')"
            ),
            {
                "v": venue.venue_id,
                "d": venue.business_date,
                "ss": owner["starts_at"],
                "se": owner["ends_at"] + timedelta(hours=6),
                "s": owner["starts_at"] + timedelta(minutes=1),
                "e": owner["ends_at"],
            },
        )
    assert "grid_starts_at" in str(excinfo.value)
    await app_session.rollback()

    # An ONLINE booking without the public key/HMAC pair is refused.
    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "INSERT INTO bookings "
                "(venue_id, number, business_date, shift_starts_at, shift_ends_at, starts_at, "
                "ends_at, party_size, source, status, guest_name, guest_phone_raw) "
                "VALUES (:v, 998, :d, :ss, :se, :s, :e, 2, 'ONLINE', 'NEW', 'X', '+79990000000')"
            ),
            {
                "v": venue.venue_id,
                "d": venue.business_date,
                "ss": owner["starts_at"],
                "se": owner["ends_at"] + timedelta(hours=6),
                "s": owner["starts_at"],
                "e": owner["ends_at"],
            },
        )
    assert "source_idempotency" in str(excinfo.value)
    await app_session.rollback()


# --- list / history ---------------------------------------------------------


def test_list_filters_and_pagination(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path, tables=3)
    for i in range(3):
        venue.create(start=venue.at(18), end=venue.at(20), table_ids=[venue.table_ids[i]])

    page = api_client.get(
        BOOKINGS_URL, params={"limit": 2}, headers=cookie_header(venue.token)
    ).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"] is not None
    page2 = api_client.get(
        BOOKINGS_URL,
        params={"limit": 2, "cursor": page["next_cursor"]},
        headers=cookie_header(venue.token),
    ).json()
    assert len(page2["items"]) == 1

    by_table = api_client.get(
        BOOKINGS_URL, params={"table_id": venue.table_ids[1]}, headers=cookie_header(venue.token)
    ).json()["items"]
    assert len(by_table) == 1
    assert by_table[0]["table_ids"] == [venue.table_ids[1]]

    by_phone = api_client.get(
        BOOKINGS_URL, params={"phone": "+7 999 000-00-00"}, headers=cookie_header(venue.token)
    ).json()["items"]
    assert len(by_phone) == 3


def test_booking_is_tenant_isolated(api_client: TestClient, tmp_path: Path) -> None:
    a = Venue(api_client, tmp_path)
    b = Venue(api_client, tmp_path)
    booking_id = a.create(start=a.at(20), end=a.at(22)).json()["id"]
    assert (
        api_client.get(f"{BOOKINGS_URL}/{booking_id}", headers=cookie_header(b.token)).status_code
        == 404
    )
    assert (
        api_client.get(
            f"{BOOKINGS_URL}/{booking_id}/history", headers=cookie_header(b.token)
        ).status_code
        == 404
    )
    assert (
        api_client.post(
            f"{BOOKINGS_URL}/{booking_id}/cancel",
            json={"expected_version": 1, "reason": "NO_SHOW"},
            headers=b.headers(),
        ).status_code
        == 404
    )


def test_vk_and_walk_in_may_omit_phone(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path, tables=2)
    # VK/WALK_IN may omit the phone; PHONE must not (§6.6, §19).
    venue.create(
        start=venue.at(18),
        end=venue.at(20),
        table_ids=[venue.table_ids[0]],
        source="VK",
        phone=None,
    )
    venue.create(
        start=venue.at(18),
        end=venue.at(20),
        table_ids=[venue.table_ids[1]],
        source="PHONE",
        phone=None,
        expect=422,
    )


def test_unresolved_endpoint_returns_a_list(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    venue.create(start=venue.at(20), end=venue.at(22))
    response = api_client.get(f"{BOOKINGS_URL}/unresolved", headers=cookie_header(venue.token))
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_no_idempotency_key_header_is_rejected(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    response = api_client.post(
        BOOKINGS_URL,
        json={
            "starts_at": venue.at(20),
            "ends_at": venue.at(22),
            "table_ids": [venue.table_ids[0]],
            "party_size": 2,
            "source": "PHONE",
            "guest_name": "X",
            "guest_phone_raw": "+79990000000",
        },
        headers=venue.headers(),
    )
    assert response.status_code == 422


# --- guards (Stage 5 additions) ---------------------------------------------


def test_archive_blocked_by_future_booking(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    venue.create(start=venue.at(20), end=venue.at(22))
    blocked = api_client.post(f"{TABLES_URL}/{venue.table_ids[0]}/archive", headers=venue.headers())
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "TABLE_ARCHIVE_BLOCKED"


def test_schedule_change_requires_confirmation(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    venue.create(start=venue.at(20), end=venue.at(22))
    # Shrink the day to 16:00-21:00: the 20:00-22:00 booking no longer fits.
    blocked = api_client.put(
        f"{SCHEDULE_EXCEPTIONS_URL}/{venue.business_date.isoformat()}",
        json={"is_closed": False, "open_time": "16:00", "close_time": "21:00"},
        headers=venue.headers(),
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SCHEDULE_CHANGE_REQUIRES_CONFIRMATION"
    assert blocked.json()["affected_bookings"][0]["number"] == 1
    # The booking is untouched and the schedule was not changed.
    confirmed = api_client.put(
        f"{SCHEDULE_EXCEPTIONS_URL}/{venue.business_date.isoformat()}?confirm=true",
        json={"is_closed": False, "open_time": "16:00", "close_time": "21:00"},
        headers=venue.headers(),
    )
    assert confirmed.status_code == 200


def test_capacity_decrease_blocked_by_future_booking(
    api_client: TestClient, tmp_path: Path
) -> None:
    venue = Venue(api_client, tmp_path, capacity=6, tables=1)
    venue.create(start=venue.at(20), end=venue.at(22), party_size=6)
    # Lowering capacity to 4 would strand the party of 6.
    result = import_layout_cli(
        venue.slug, _write(tmp_path, _layout(capacity=4, tables=1), "small.json")
    )
    assert result.returncode == 1
    assert "capacity change would break" in result.stderr


def test_hall_archive_and_bookability(api_client: TestClient, tmp_path: Path) -> None:
    venue = Venue(api_client, tmp_path)
    tables = api_client.get(TABLES_URL, headers=cookie_header(venue.token)).json()["tables"]
    hall_id = next(t["hall_id"] for t in tables if t["id"] == venue.table_ids[0])
    api_client.patch(f"{HALLS_URL}/{hall_id}", json={"is_bookable": False}, headers=venue.headers())
    response = venue.create(start=venue.at(20), end=venue.at(22), expect=409)
    assert response.json()["code"] == "HALL_NOT_BOOKABLE"
