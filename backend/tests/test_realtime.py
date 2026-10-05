"""Stage 9 realtime unit tests (PROJECT-SPEC §3.4, §37).

These cover the pure machinery that needs no database: the event payload
contract, bounded per-venue fan-out and the SSE stream generator's heartbeat,
overflow and cleanup semantics. Real PostgreSQL ``LISTEN/NOTIFY`` and the HTTP
endpoint are exercised by ``tests/integration/test_realtime.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.api.admin import stream as stream_module
from app.api.admin.stream import sse_event_stream
from app.realtime.events import (
    BOOKING_CREATED,
    BOOKING_UPDATED,
    CHANNEL,
    MAX_IDS,
    RESYNC,
    RealtimeEvent,
    normalize_event,
    parse_payload,
)
from app.realtime.hub import RealtimeHub, get_hub, init_hub, reset_hub
from app.settings import Settings


class _FakeRequest:
    """Minimal ``Request`` stand-in for the stream generator."""

    def __init__(self, disconnect_after: int = 10**9) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


@pytest.fixture(autouse=True)
def _fresh_hub() -> AsyncIterator[None]:
    reset_hub()
    init_hub(queue_maxsize=4)
    yield
    reset_hub()


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@127.0.0.1:1/x",
        "realtime_listener_enabled": False,
        "realtime_heartbeat_seconds": 5.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- event contract ---------------------------------------------------------


def test_channel_name_is_stable() -> None:
    # Changing this silently would strand every running API process.
    assert CHANNEL == "takeplace_realtime"


def test_event_payload_is_minimal_and_stable() -> None:
    event = RealtimeEvent(venue_id=1, type=BOOKING_UPDATED, ids=(5, 3))
    assert event.to_payload() == '{"venue_id":1,"type":"booking.updated","ids":[5,3]}'
    # No ids -> no ``ids`` key at all.
    assert RealtimeEvent(venue_id=2, type=RESYNC).to_payload() == '{"venue_id":2,"type":"resync"}'


def test_normalize_dedupes_sorts_and_rejects_junk_ids() -> None:
    event = normalize_event(1, BOOKING_UPDATED, [5, 3, 5, "x", -1, True, None])
    assert event.ids == (3, 5)
    assert event.type == BOOKING_UPDATED


def test_oversized_id_list_collapses_to_resync() -> None:
    event = normalize_event(1, BOOKING_UPDATED, list(range(MAX_IDS + 1)))
    assert event.type == RESYNC
    assert event.ids == ()


def test_parse_payload_round_trip() -> None:
    original = RealtimeEvent(venue_id=7, type=BOOKING_CREATED, ids=(42,))
    parsed = parse_payload(original.to_payload())
    assert parsed == original


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '"string"',
        "{}",
        '{"venue_id": "1", "type": "booking.updated"}',
        '{"type": "booking.updated"}',
        '{"venue_id": 1}',
        '{"venue_id": 1, "type": ""}',
        '{"venue_id": -1, "type": "x"}',
    ],
)
def test_parse_payload_rejects_malformed(payload: str) -> None:
    assert parse_payload(payload) is None


# --- hub fan-out / tenant isolation -----------------------------------------


def test_publish_is_venue_scoped() -> None:
    hub = RealtimeHub()
    a = hub.subscribe(1)
    b = hub.subscribe(2)
    hub.publish(RealtimeEvent(venue_id=1, type=BOOKING_UPDATED, ids=(9,)))
    assert not a.queue.empty()
    assert b.queue.empty(), "tenant B must never see tenant A's events"
    assert hub.subscriber_count(1) == 1
    assert hub.subscriber_count() == 2


def test_unsubscribe_removes_subscription() -> None:
    hub = RealtimeHub()
    sub = hub.subscribe(1)
    hub.unsubscribe(sub)
    hub.unsubscribe(sub)  # idempotent
    assert hub.subscriber_count() == 0
    hub.publish(RealtimeEvent(venue_id=1, type=RESYNC))
    assert sub.queue.empty()


def test_overflow_places_sentinel_instead_of_blocking() -> None:
    hub = RealtimeHub(queue_maxsize=1)
    sub = hub.subscribe(1)
    hub.publish(RealtimeEvent(venue_id=1, type=BOOKING_UPDATED, ids=(1,)))
    hub.publish(RealtimeEvent(venue_id=1, type=BOOKING_UPDATED, ids=(2,)))
    # The queue was drained and a single overflow sentinel queued.
    assert sub.queue.qsize() == 1
    assert sub.queue.get_nowait() is None


def test_broadcast_resync_reaches_every_venue() -> None:
    hub = RealtimeHub()
    subs = [hub.subscribe(v) for v in (1, 2, 3)]
    hub.broadcast_resync()
    for sub in subs:
        event = sub.queue.get_nowait()
        assert event is not None and event.type == RESYNC


# --- SSE stream generator ---------------------------------------------------


async def test_stream_emits_connected_comment_then_events(monkeypatch) -> None:
    async def _always_valid(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(stream_module, "_session_still_valid", _always_valid)
    request = _FakeRequest()
    settings = _settings()
    agen = sse_event_stream(request, venue_id=1, raw_token="t", settings=settings)

    assert await anext(agen) == b": connected\n\n"
    get_hub().publish(RealtimeEvent(venue_id=1, type=BOOKING_UPDATED, ids=(5,)))
    frame = await anext(agen)
    assert frame == b'data: {"venue_id":1,"type":"booking.updated","ids":[5]}\n\n'

    # Another venue's event never reaches this subscriber.
    get_hub().publish(RealtimeEvent(venue_id=2, type=BOOKING_UPDATED, ids=(6,)))
    await agen.aclose()
    assert get_hub().subscriber_count() == 0


async def test_stream_heartbeat_is_a_comment(monkeypatch) -> None:
    async def _always_valid(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(stream_module, "_session_still_valid", _always_valid)
    agen = sse_event_stream(
        _FakeRequest(),
        venue_id=1,
        raw_token="t",
        settings=_settings(realtime_heartbeat_seconds=0.01),
    )
    assert await anext(agen) == b": connected\n\n"
    # A heartbeat must be a comment line (starts with ':'), never a data event,
    # so the frontend does not refetch every heartbeat interval.
    assert await anext(agen) == b": heartbeat\n\n"
    await agen.aclose()


async def test_stream_closes_when_session_becomes_invalid(monkeypatch) -> None:
    async def _invalid(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(stream_module, "_session_still_valid", _invalid)
    agen = sse_event_stream(
        _FakeRequest(),
        venue_id=1,
        raw_token="t",
        settings=_settings(realtime_heartbeat_seconds=0.01),
    )
    assert await anext(agen) == b": connected\n\n"
    with pytest.raises(StopAsyncIteration):
        await anext(agen)


async def test_stream_overflow_emits_resync_then_stops(monkeypatch) -> None:
    async def _always_valid(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(stream_module, "_session_still_valid", _always_valid)
    reset_hub()
    hub = init_hub(queue_maxsize=1)
    agen = sse_event_stream(
        _FakeRequest(),
        venue_id=1,
        raw_token="t",
        settings=_settings(realtime_heartbeat_seconds=1.0),
    )
    assert await anext(agen) == b": connected\n\n"
    # Fill then overflow the one-slot queue while the generator is suspended.
    hub.publish(RealtimeEvent(venue_id=1, type=BOOKING_UPDATED, ids=(1,)))
    hub.publish(RealtimeEvent(venue_id=1, type=BOOKING_UPDATED, ids=(2,)))
    assert await anext(agen) == b'data: {"venue_id":1,"type":"resync"}\n\n'
    with pytest.raises(StopAsyncIteration):
        await anext(agen)
    assert get_hub().subscriber_count() == 0


async def test_stream_finally_unsubscribes_on_disconnect(monkeypatch) -> None:
    async def _always_valid(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(stream_module, "_session_still_valid", _always_valid)
    # Disconnect reported on the loop's first check.
    agen = sse_event_stream(
        _FakeRequest(disconnect_after=0),
        venue_id=1,
        raw_token="t",
        settings=_settings(),
    )
    assert await anext(agen) == b": connected\n\n"
    with pytest.raises(StopAsyncIteration):
        await anext(agen)
    assert get_hub().subscriber_count() == 0
