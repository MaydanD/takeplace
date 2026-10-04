"""PostgreSQL concurrency suite for public ONLINE bookings (PROJECT-SPEC §54, Stage 6).

Runs real parallel transactions against PostgreSQL to prove that the public
booking path — which shares the canonical lock order with the admin path — is
safe under concurrency: no double booking, no lost idempotency, kill-switch
race safety, and BLOCK vs public create isolation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.security.cookies import SESSION_COOKIE_NAME
from app.services.bookings import (
    PublicBookingInput,
    create_admin_booking,
    create_public_booking,
)
from app.services.errors import (
    BookingConflictError,
    IdempotencyKeyReusedError,
    OnlineBookingDisabledError,
)
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.support import (
    APP_URL,
    ORIGIN,
    PASSWORD,
    SETTINGS_URL,
    cookie_header,
    create_venue,
    import_layout_cli,
    login,
    make_client,
    unique,
)

pytestmark = pytest.mark.integration

MSK = ZoneInfo("Europe/Moscow")
HMAC_KEY = "test-idempotency-key-0123456789"
ABUSE_KEY = "test-abuse-key-0123456789abcdef"
PUBLIC = "/api/public/v1"


@pytest.fixture(autouse=True)
def _require_test_db() -> None:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    with make_client() as client:
        yield client


@pytest.fixture
async def engine():
    eng = create_async_engine(APP_URL, pool_size=10, max_overflow=10)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


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


def _layout(capacity: int = 4, tables: int = 1) -> dict:
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


class PublicVenue:
    """A venue with layout, open day and online booking enabled."""

    def __init__(self, client: TestClient, tmp_path: Path, *, capacity: int = 4, tables: int = 1):
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
        self._enable_online_booking()
        self.table_ids = [
            t["id"]
            for t in client.get("/api/admin/v1/tables", headers=cookie_header(self.token)).json()[
                "tables"
            ]
        ]
        self.venue_id = int(
            client.get("/api/admin/v1/me", headers=cookie_header(self.token)).json()["venue"]["id"]
        )

    def headers(self, **extra: str) -> dict[str, str]:
        return {**cookie_header(self.token), "Origin": ORIGIN, **extra}

    def _open_day(self, business_date) -> None:
        response = self.client.put(
            f"/api/admin/v1/schedule/exceptions/{business_date.isoformat()}",
            json={"is_closed": False, "open_time": "16:00", "close_time": "02:00"},
            headers=self.headers(),
            params={"confirm": "true"},
        )
        assert response.status_code == 200, response.text

    def _enable_online_booking(self) -> None:
        response = self.client.patch(
            SETTINGS_URL,
            json={"online_booking_enabled": True},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text

    def disable_online_booking(self) -> None:
        response = self.client.patch(
            SETTINGS_URL,
            json={"online_booking_enabled": False},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text

    def at(self, hour: int, minute: int = 0, *, day_offset: int = 0) -> datetime:
        day = self.business_date + timedelta(days=day_offset)
        return datetime.combine(day, datetime.min.time(), tzinfo=MSK).replace(
            hour=hour, minute=minute
        )


def _public_data(
    venue: PublicVenue,
    *,
    start: datetime,
    end: datetime,
    table_id: int | None = None,
    party: int = 2,
) -> PublicBookingInput:
    return PublicBookingInput(
        starts_at=start,
        ends_at=end,
        table_id=table_id if table_id is not None else venue.table_ids[0],
        party_size=party,
        guest_name="Guest",
        guest_phone_raw="+79990000000",
    )


async def _public_create(
    factory, venue: PublicVenue, data: PublicBookingInput, key: str | None = None
):
    async with factory() as session:
        view, _created = await create_public_booking(
            session,
            venue_id=venue.venue_id,
            data=data,
            idempotency_key=key or str(uuid.uuid4()),
            hmac_key=HMAC_KEY,
            request_ip_hmac="test-ip-hmac",
            ip_hmac_ttl_days=7,
        )
        return view


def _results(values: list) -> tuple[list, list]:
    ok = [v for v in values if not isinstance(v, BaseException)]
    errors = [v for v in values if isinstance(v, BaseException)]
    return ok, errors


async def _scalar(session: AsyncSession, sql: str, **params):
    return (await session.execute(text(sql), params)).scalar_one()


# --- 54.1 same-slot public race ---------------------------------------------


async def test_public_same_slot_exactly_one_succeeds(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    results = await asyncio.gather(
        *[_public_create(session_factory, venue, data) for _ in range(4)],
        return_exceptions=True,
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert all(isinstance(e, BookingConflictError) for e in errors), errors

    async with session_factory() as session:
        bookings = await _scalar(
            session, "SELECT count(*) FROM bookings WHERE venue_id = :v", v=venue.venue_id
        )
    assert bookings == 1


# --- 54.2 public idempotent duplicate ---------------------------------------


async def test_public_concurrent_idempotent_duplicate_creates_one(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    key = str(uuid.uuid4())
    results = await asyncio.gather(
        *[_public_create(session_factory, venue, data, key) for _ in range(4)],
        return_exceptions=True,
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


async def test_public_idempotency_key_reused_with_different_payload(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=2)
    key = str(uuid.uuid4())
    data_a = _public_data(venue, start=venue.at(20), end=venue.at(22), table_id=venue.table_ids[0])
    data_b = _public_data(venue, start=venue.at(20), end=venue.at(22), table_id=venue.table_ids[1])
    await _public_create(session_factory, venue, data_a, key)
    results = await asyncio.gather(
        _public_create(session_factory, venue, data_b, key),
        return_exceptions=True,
    )
    assert isinstance(results[0], IdempotencyKeyReusedError)


# --- 54.3 availability stale -> create conflict -----------------------------


async def test_availability_stale_then_create_conflict(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    await _public_create(session_factory, venue, data)
    results = await asyncio.gather(
        _public_create(session_factory, venue, data),
        return_exceptions=True,
    )
    assert isinstance(results[0], BookingConflictError)


# --- 54.11a kill switch race ------------------------------------------------


async def test_kill_switch_blocks_new_public_booking(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    venue.disable_online_booking()
    data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    results = await asyncio.gather(
        _public_create(session_factory, venue, data),
        return_exceptions=True,
    )
    assert isinstance(results[0], OnlineBookingDisabledError)


async def test_kill_switch_does_not_break_replay(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    key = str(uuid.uuid4())
    first = await _public_create(session_factory, venue, data, key)
    venue.disable_online_booking()
    second = await _public_create(session_factory, venue, data, key)
    assert second.booking.id == first.booking.id


# --- BLOCK vs public create -------------------------------------------------


async def test_admin_block_prevents_public_booking(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    from app.services.bookings import AdminBookingInput

    block_data = AdminBookingInput(
        starts_at=venue.at(20),
        ends_at=venue.at(22),
        table_ids=[venue.table_ids[0]],
        party_size=2,
        source="OTHER",
        guest_name="Blocked",
        guest_phone_raw="+79990000000",
    )
    async with session_factory() as session:
        await create_admin_booking(
            session,
            venue_id=venue.venue_id,
            data=block_data,
            idempotency_key=str(uuid.uuid4()),
            hmac_key=HMAC_KEY,
            admin_session_id=None,
        )
    public_data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    results = await asyncio.gather(
        _public_create(session_factory, venue, public_data),
        return_exceptions=True,
    )
    assert isinstance(results[0], BookingConflictError)


# --- public and admin share the same table safely ---------------------------


async def test_public_and_admin_same_slot_one_succeeds(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    public_data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    from app.services.bookings import AdminBookingInput

    admin_data = AdminBookingInput(
        starts_at=venue.at(20),
        ends_at=venue.at(22),
        table_ids=[venue.table_ids[0]],
        party_size=2,
        source="PHONE",
        guest_name="Admin",
        guest_phone_raw="+79990000000",
    )

    async def _admin_create():
        async with session_factory() as session:
            return await create_admin_booking(
                session,
                venue_id=venue.venue_id,
                data=admin_data,
                idempotency_key=str(uuid.uuid4()),
                hmac_key=HMAC_KEY,
                admin_session_id=None,
            )

    results = await asyncio.gather(
        _public_create(session_factory, venue, public_data),
        _admin_create(),
        return_exceptions=True,
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert isinstance(errors[0], BookingConflictError)


async def test_kill_switch_commits_while_public_waits_for_table_lock(
    api_client: TestClient, tmp_path: Path, session_factory, monkeypatch
) -> None:
    from app.services import bookings

    venue = PublicVenue(api_client, tmp_path)
    data = _public_data(venue, start=venue.at(20), end=venue.at(22))
    reached_lock = asyncio.Event()
    original = bookings._lock_halls_tables

    async def observed_lock(*args, **kwargs):
        reached_lock.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(bookings, "_lock_halls_tables", observed_lock)
    async with session_factory() as blocker:
        async with blocker.begin():
            await blocker.execute(
                text("SELECT id FROM tables WHERE id=:t FOR UPDATE"), {"t": venue.table_ids[0]}
            )
            task = asyncio.create_task(_public_create(session_factory, venue, data))
            await asyncio.wait_for(reached_lock.wait(), timeout=2)
            async with session_factory() as switch, switch.begin():
                await asyncio.wait_for(
                    switch.execute(
                        text("UPDATE venues SET online_booking_enabled=false WHERE id=:v"),
                        {"v": venue.venue_id},
                    ),
                    timeout=1,
                )
        with pytest.raises(OnlineBookingDisabledError):
            await asyncio.wait_for(task, timeout=3)
    async with session_factory() as session:
        assert (
            await _scalar(
                session, "SELECT count(*) FROM bookings WHERE venue_id=:v", v=venue.venue_id
            )
            == 0
        )


async def test_actual_block_vs_public_race(
    api_client: TestClient, tmp_path: Path, session_factory
) -> None:
    from sqlalchemy.exc import IntegrityError

    venue = PublicVenue(api_client, tmp_path)
    data = _public_data(venue, start=venue.at(20), end=venue.at(22))

    async def block():
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO table_occupancies (venue_id, table_id, kind, starts_at, ends_at) VALUES (:v, :t, 'BLOCK', :s, :e)"
                ),
                {
                    "v": venue.venue_id,
                    "t": venue.table_ids[0],
                    "s": data.starts_at,
                    "e": data.ends_at,
                },
            )

    results = await asyncio.gather(
        _public_create(session_factory, venue, data), block(), return_exceptions=True
    )
    ok, errors = _results(results)
    assert len(ok) == 1, results
    assert len(errors) == 1 and isinstance(errors[0], (BookingConflictError, IntegrityError)), (
        results
    )
    async with session_factory() as session:
        assert (
            await _scalar(
                session,
                "SELECT count(*) FROM table_occupancies WHERE venue_id=:v AND is_active",
                v=venue.venue_id,
            )
            == 1
        )
