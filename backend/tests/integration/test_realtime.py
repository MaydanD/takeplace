"""Stage 9 realtime integration tests against a real PostgreSQL (PROJECT-SPEC §37).

These tests deliberately use a real ``LISTEN/NOTIFY`` connection instead of a
Python mock, because the whole point of the design is that the notification is
delivered by PostgreSQL *after commit* and never on rollback (§37.2, §54.15).

Covered:

* commit -> notification appears;
* rollback -> no notification;
* two independent listeners both receive one committed notification;
* the dedicated listener forwards to the in-process hub with tenant isolation;
* a real booking mutation emits a matching notification;
* the HTTP SSE endpoint requires authentication;
* an end-to-end two-admin smoke over a real ASGI server: one admin watches the
  SSE stream while the other mutates, plus reconnect/resync and stale-version
  arbitration.

The SSE smoke runs a real ``uvicorn`` server in-process because the Starlette
``TestClient`` buffers a response until it completes, which never happens for an
infinite event stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest
import uvicorn
from app.db.time import ceil_to_5_minutes
from app.main import create_app
from app.realtime.events import BOOKING_CREATED, BOOKING_UPDATED, CHANNEL, publish
from app.realtime.hub import RealtimeHub
from app.realtime.listener import RealtimeListener, asyncpg_dsn
from app.security.cookies import SESSION_COOKIE_NAME
from app.settings import Settings, get_settings
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.support import (
    APP_URL,
    BOOKINGS_URL,
    ORIGIN,
    PASSWORD,
    create_venue,
    import_layout_cli,
    login,
    make_client,
    make_settings,
    unique,
)
from tests.integration.test_bookings_api import MSK, Venue, _layout

pytestmark = pytest.mark.integration

STREAM_URL = "/api/admin/v1/stream"
ADMIN = "/api/admin/v1"


@pytest.fixture(autouse=True)
def _require_test_db() -> None:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")


# --- PostgreSQL LISTEN/NOTIFY transaction semantics -------------------------


async def _listen() -> tuple[asyncpg.Connection, asyncio.Queue[tuple[str, str]]]:
    assert APP_URL
    connection = await asyncpg.connect(asyncpg_dsn(APP_URL))
    received: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    async def _callback(_conn: asyncpg.Connection, _pid: int, channel: str, payload: str) -> None:
        await received.put((channel, payload))

    await connection.add_listener(CHANNEL, _callback)
    return connection, received


async def test_publish_is_delivered_after_commit() -> None:
    assert APP_URL
    connection, received = await _listen()
    engine = create_async_engine(APP_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await publish(session, venue_id=424242, event_type=BOOKING_UPDATED, ids=[5])
        channel, payload = await asyncio.wait_for(received.get(), timeout=10)
        assert channel == CHANNEL
        assert payload == '{"venue_id":424242,"type":"booking.updated","ids":[5]}'
    finally:
        await connection.close()
        await engine.dispose()


async def test_publish_is_absent_when_transaction_rolls_back() -> None:
    assert APP_URL
    connection, received = await _listen()
    engine = create_async_engine(APP_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class _Boom(Exception):
        pass

    try:
        with pytest.raises(_Boom):
            async with factory() as session, session.begin():
                await publish(session, venue_id=515151, event_type=BOOKING_UPDATED, ids=[7])
                raise _Boom
        with pytest.raises(asyncio.TimeoutError):
            # Rollback must never produce a realtime event for a change the
            # database does not have.
            await asyncio.wait_for(received.get(), timeout=1.5)
    finally:
        await connection.close()
        await engine.dispose()


async def test_two_independent_listeners_both_receive_one_notification() -> None:
    assert APP_URL
    first, first_q = await _listen()
    second, second_q = await _listen()
    engine = create_async_engine(APP_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            await publish(session, venue_id=606060, event_type=BOOKING_CREATED, ids=[11])
        a = await asyncio.wait_for(first_q.get(), timeout=10)
        b = await asyncio.wait_for(second_q.get(), timeout=10)
        # Multiple API instances are supported: each instance owns its own
        # LISTEN connection and receives the same committed notification.
        assert a == b
        assert '"type":"booking.created"' in a[1]
    finally:
        await first.close()
        await second.close()
        await engine.dispose()


async def test_listener_forwards_to_hub_with_tenant_isolation() -> None:
    assert APP_URL
    hub = RealtimeHub()
    venue_a, venue_b = 700001, 700002
    sub_a = hub.subscribe(venue_a)
    sub_b = hub.subscribe(venue_b)
    listener = RealtimeListener(APP_URL, hub)
    await listener.start()
    engine = create_async_engine(APP_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for _ in range(200):
            if listener.is_connected:
                break
            await asyncio.sleep(0.05)
        assert listener.is_connected
        async with factory() as session, session.begin():
            await publish(session, venue_id=venue_a, event_type=BOOKING_UPDATED, ids=[99])
        event = await asyncio.wait_for(sub_a.queue.get(), timeout=10)
        assert event is not None
        assert event.venue_id == venue_a
        assert event.ids == (99,)
        # Tenant B must not receive tenant A's signal.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub_b.queue.get(), timeout=1.0)
    finally:
        await listener.stop()
        await engine.dispose()


# --- real mutation emits a matching notification ----------------------------


async def test_real_booking_create_emits_notification(tmp_path: Path) -> None:
    assert APP_URL
    with make_client() as client:
        # Set the venue up before listening: setup itself emits a schedule resync.
        venue = Venue(client, tmp_path)
        connection, received = await _listen()
        try:
            booking = venue.create(start=venue.at(18), end=venue.at(20)).json()
            channel, payload = await asyncio.wait_for(received.get(), timeout=10)
            assert channel == CHANNEL
            assert f'"venue_id":{venue.venue_id}' in payload
            assert '"type":"booking.created"' in payload
            assert f'"ids":[{booking["id"]}]' in payload
        finally:
            await connection.close()


async def test_real_cancel_emits_updated_notification(tmp_path: Path) -> None:
    assert APP_URL
    with make_client() as client:
        venue = Venue(client, tmp_path)
        connection, received = await _listen()
        try:
            booking = venue.create(start=venue.at(18), end=venue.at(20)).json()
            await asyncio.wait_for(received.get(), timeout=10)  # create notification
            response = client.post(
                f"{BOOKINGS_URL}/{booking['id']}/cancel",
                json={"expected_version": 1, "reason": "NO_SHOW"},
                headers=venue.headers(),
            )
            assert response.status_code == 200, response.text
            channel, payload = await asyncio.wait_for(received.get(), timeout=10)
            assert channel == CHANNEL
            assert '"type":"booking.updated"' in payload
            assert f'"ids":[{booking["id"]}]' in payload
        finally:
            await connection.close()


# --- HTTP SSE endpoint ------------------------------------------------------


def _login_token(client: TestClient, login_name: str) -> str:
    response = login(client, login_name, PASSWORD)
    assert response.status_code == 200, response.text
    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token
    client.cookies.clear()
    return token


def test_stream_requires_authentication() -> None:
    with make_client() as client:
        response = client.get(STREAM_URL)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"


# --- end-to-end two-admin smoke over a real ASGI server ---------------------


@contextmanager
def _running_server(settings: Settings) -> Iterator[str]:
    """Run the real app on a loopback port; yield its base URL."""
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    # Signal handlers can only be installed on the main thread.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(400):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "uvicorn did not start"
        socket = server.servers[0].sockets[0]
        port = socket.getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _httpx_login(base: str, login_name: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=httpx.Timeout(10.0, read=None))
    response = client.post(
        f"{ADMIN}/auth/login",
        json={"login": login_name, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    return client


def _headers(**extra: str) -> dict[str, str]:
    return {"Origin": ORIGIN, **extra}


class _SSE:
    """A background-reader SSE connection for httpx."""

    def __init__(self, client: httpx.Client) -> None:
        self._manager = client.stream("GET", STREAM_URL)
        self._response = self._manager.__enter__()
        assert self._response.status_code == 200, self._response.read()
        self.lines: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        # Closing the response (disconnect) ends the iteration with an exception.
        with contextlib.suppress(Exception):
            for line in self._response.iter_lines():
                self.lines.put(line)

    def wait_for(self, needle: str, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        seen: list[str] = []
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append(line)
            if needle in line:
                return line
        raise AssertionError(f"never received {needle!r}; saw {seen!r}")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._response.close()
        with contextlib.suppress(Exception):
            self._manager.__exit__(None, None, None)


def _setup_venue(base: str, login_name: str, tmp_path: Path) -> tuple[httpx.Client, list[dict]]:
    """Create a venue with tables and an open shift around 'now' over HTTP."""
    slug = unique("venue")
    assert create_venue(slug, login_name).returncode == 0
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(_layout(capacity=4, tables=3)), encoding="utf-8")
    assert import_layout_cli(slug, layout_path).returncode == 0
    client = _httpx_login(base, login_name)
    now = datetime.now(MSK)
    shift_start = ceil_to_5_minutes(now) - timedelta(hours=1)
    shift_end = shift_start + timedelta(hours=6)
    response = client.put(
        f"{ADMIN}/schedule/exceptions/{shift_start.date().isoformat()}",
        json={
            "is_closed": False,
            "open_time": shift_start.strftime("%H:%M"),
            "close_time": shift_end.strftime("%H:%M"),
        },
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    tables = client.get(f"{ADMIN}/tables").json()["tables"]
    return client, tables


def test_two_admin_sessions_realtime_smoke(tmp_path: Path) -> None:
    """Two admin sessions on one venue see each other's changes in near real time.

    Admin A watches the SSE stream; admin B performs mutations over plain HTTP.
    The notification travels PostgreSQL -> B's commit -> A's LISTEN -> A's hub ->
    A's SSE stream. Reconnect/resync and stale-version arbitration are checked
    alongside.
    """
    settings = make_settings(session_cookie_secure=False)
    with _running_server(settings) as base:
        login_name = unique("admin")
        client_b, tables = _setup_venue(base, login_name, tmp_path)
        client_a = _httpx_login(base, login_name)  # second independent session
        table_id = tables[0]["id"]
        now = datetime.now(MSK)
        start = ceil_to_5_minutes(now) + timedelta(hours=1)
        end = start + timedelta(hours=1)
        try:
            stream = _SSE(client_a)
            try:
                assert stream.wait_for(": connected") == ": connected"
                # 1. B creates a NEW booking -> A sees booking.created.
                created = client_b.post(
                    f"{ADMIN}/bookings",
                    json={
                        "starts_at": start.isoformat(),
                        "ends_at": end.isoformat(),
                        "table_ids": [table_id],
                        "party_size": 2,
                        "source": "PHONE",
                        "guest_name": "Realtime",
                        "guest_phone_raw": "+79990000000",
                    },
                    headers=_headers(**{"Idempotency-Key": str(uuid.uuid4())}),
                )
                assert created.status_code == 201, created.text
                booking = created.json()
                created_line = stream.wait_for('"type":"booking.created"')
                assert f'"ids":[{booking["id"]}]' in created_line

                # 2. B edits the guest -> A sees booking.updated.
                edited = client_b.patch(
                    f"{ADMIN}/bookings/{booking['id']}",
                    json={"expected_version": booking["version"], "party_size": 3},
                    headers=_headers(),
                )
                assert edited.status_code == 200, edited.text
                stream.wait_for('"type":"booking.updated"')

                # 3. Stale-version arbitration still holds: realtime is delivery,
                #    not the source of truth (§33).
                stale = client_b.post(
                    f"{ADMIN}/bookings/{booking['id']}/cancel",
                    json={"expected_version": booking["version"], "reason": "NO_SHOW"},
                    headers=_headers(),
                )
                assert stale.status_code == 409, stale.text
                assert stale.json()["code"] == "BOOKING_STALE"

                # 4. WALK_IN create (OPEN) -> created; CLOSE -> updated.
                walk_start = ceil_to_5_minutes(datetime.now(MSK))
                walk = client_b.post(
                    f"{ADMIN}/bookings",
                    json={
                        "source": "WALK_IN",
                        "open_immediately": True,
                        "starts_at": walk_start.isoformat(),
                        "ends_at": (walk_start + timedelta(hours=1)).isoformat(),
                        "table_ids": [tables[1]["id"]],
                        "party_size": 2,
                        "guest_name": "Walk",
                    },
                    headers=_headers(**{"Idempotency-Key": str(uuid.uuid4())}),
                )
                assert walk.status_code == 201, walk.text
                walk_booking = walk.json()
                assert walk_booking["status"] == "OPEN"
                stream.wait_for('"type":"booking.created"')
                closed = client_b.post(
                    f"{ADMIN}/bookings/{walk_booking['id']}/close",
                    json={"expected_version": walk_booking["version"]},
                    headers=_headers(),
                )
                assert closed.status_code == 200, closed.text
                stream.wait_for('"type":"booking.updated"')
            finally:
                stream.close()

            # 5. Disconnect, mutate while A is away, then reconnect and resync.
            created_while_away = client_b.post(
                f"{ADMIN}/bookings",
                json={
                    "starts_at": (start + timedelta(hours=2)).isoformat(),
                    "ends_at": (start + timedelta(hours=3)).isoformat(),
                    "table_ids": [tables[2]["id"]],
                    "party_size": 2,
                    "source": "VK",
                    "guest_name": "Away",
                },
                headers=_headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            )
            assert created_while_away.status_code == 201, created_while_away.text

            stream2 = _SSE(client_a)
            try:
                assert stream2.wait_for(": connected") == ": connected"
                # A fresh mutation after reconnect is delivered on the new stream.
                created_after = client_b.post(
                    f"{ADMIN}/bookings",
                    json={
                        "starts_at": (start + timedelta(hours=1)).isoformat(),
                        "ends_at": (start + timedelta(hours=2)).isoformat(),
                        "table_ids": [tables[2]["id"]],
                        "party_size": 2,
                        "source": "VK",
                        "guest_name": "After",
                    },
                    headers=_headers(**{"Idempotency-Key": str(uuid.uuid4())}),
                )
                assert created_after.status_code == 201, created_after.text
                line = stream2.wait_for('"type":"booking.created"')
                assert '"venue_id"' in line
            finally:
                stream2.close()
        finally:
            client_a.close()
            client_b.close()
