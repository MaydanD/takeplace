"""Admin realtime SSE endpoint (PROJECT-SPEC §3.4, §37).

``GET /api/admin/v1/stream`` is a server-sent-events stream scoped to the venue
resolved from the session cookie. There is no ``venue_id`` parameter, so a client
can only ever subscribe to its own tenant (§7.1, §37.1).

The stream emits:

* default ``message`` events whose JSON body is a minimal refetch signal
  (``{"venue_id", "type", "ids"}``) — never a full model (§37.2);
* ``: heartbeat`` comment lines on ``realtime_heartbeat_seconds``, which keep
  proxies from idling the connection closed without provoking a frontend refetch
  (§37.4).

Reliability model (§37.4): the connection's queue is bounded and an overflow
emits a final ``resync`` then closes the stream; the client reconnects and
resyncs through the ordinary HTTP API.

Session lifecycle (audit F-02/F-03): the session is revalidated on a fixed
*time* cadence driven by a monotonic deadline, not by stream idleness. A venue
with a continuous stream of booking events therefore cannot postpone validation
indefinitely: a revoked session (logout, logout-all, disabled admin or venue,
expiry) closes the stream within ``realtime_session_check_seconds`` no matter how
busy the stream is. Validation is a single read-only lookup that never refreshes
``last_seen_at`` (``session_validity``), so it does not write on every check, and
no in-memory cache lets a revoked session outlive that bound.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.admin.dependencies import AuthContextDep, SettingsDep
from app.db.session import session_scope
from app.realtime.events import RESYNC, RealtimeEvent
from app.realtime.hub import get_hub
from app.security.cookies import session_cookie_name
from app.services.auth import session_validity
from app.settings import Settings

logger = structlog.get_logger("takeplace.realtime")

router = APIRouter(prefix="/stream", tags=["admin-realtime"])

#: Prevent intermediaries from buffering the stream (Caddy is configured with
#: ``flush_interval -1`` too, §37.4).
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

_HEARTBEAT = b": heartbeat\n\n"


def _encode_event(event: RealtimeEvent) -> bytes:
    """Serialise one event as a default SSE ``message``."""
    return f"data: {event.to_payload()}\n\n".encode()


async def _session_still_valid(raw_token: str, venue_id: int, settings: Settings) -> bool:
    """Lightweight revalidation of a live stream's session.

    Read-only and never touches ``last_seen_at`` (``session_validity``), so it is
    safe to run on the security cadence without turning realtime into extra
    write traffic. Any failure fails *closed*: a broken check must never leave a
    stream running for a possibly-revoked identity.
    """
    if not raw_token:
        return False
    try:
        async with session_scope() as session:
            return await session_validity(session, raw_token) == venue_id
    except Exception as exc:  # noqa: BLE001 - a broken check must not stream forever
        logger.warning("realtime_session_check_failed", error=str(exc))
        return False


async def sse_event_stream(
    request: Request,
    *,
    venue_id: int,
    raw_token: str,
    settings: Settings,
) -> AsyncIterator[bytes]:
    """Yield SSE frames for one authenticated subscriber until it disconnects.

    Extracted from the route so tests can drive the stream directly with a real
    hub and deterministic timing.
    """
    hub = get_hub()
    subscription = hub.subscribe(venue_id)
    heartbeat = settings.realtime_heartbeat_seconds
    check_interval = settings.realtime_session_check_seconds
    loop = asyncio.get_running_loop()
    try:
        # Flush headers immediately so the browser fires ``open`` (and resyncs)
        # without waiting for the first real event.
        yield b": connected\n\n"
        # Deadlines are monotonic: continuous event traffic can never postpone
        # them, unlike a per-wait timeout that restarts on every event.
        next_check = loop.time() + check_interval
        next_heartbeat = loop.time() + heartbeat
        while True:
            if await request.is_disconnected():
                break
            now = loop.time()
            timeout = max(0.0, min(next_check, next_heartbeat) - now)
            timed_out = False
            event: RealtimeEvent | None = None
            try:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=timeout)
            except TimeoutError:
                timed_out = True
            now = loop.time()
            # Security: validate on the fixed cadence before delivering anything,
            # whether the queue was idle or delivered a burst of events.
            if now >= next_check:
                if not await _session_still_valid(raw_token, venue_id, settings):
                    break
                next_check = now + check_interval
            if timed_out:
                if now >= next_heartbeat:
                    yield _HEARTBEAT
                    next_heartbeat = now + heartbeat
                continue
            if event is None:
                # Bounded-queue overflow: ask the client to resync, then close so
                # it reconnects into a fresh, empty queue (§37.4).
                yield _encode_event(RealtimeEvent(venue_id=venue_id, type=RESYNC))
                break
            yield _encode_event(event)
    finally:
        # Cleanup is guaranteed regardless of how the loop exits: disconnect,
        # revocation, overflow or cancellation.
        hub.unsubscribe(subscription)


@router.get(
    "",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Realtime refetch signals for the session's venue.",
        }
    },
)
async def get_stream(
    request: Request,
    context: AuthContextDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Open the admin realtime stream for the session's venue (§37.1)."""
    cookie_name = session_cookie_name(secure=settings.cookie_secure)
    raw_token = request.cookies.get(cookie_name, "")
    return StreamingResponse(
        sse_event_stream(
            request,
            venue_id=context.venue_id,
            raw_token=raw_token,
            settings=settings,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
