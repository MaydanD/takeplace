"""Dedicated PostgreSQL LISTEN connection (PROJECT-SPEC §37.3).

Each API process runs exactly one long-lived ``LISTEN`` connection. It is a raw
``asyncpg`` connection deliberately *outside* the SQLAlchemy request pool: a
pooled connection must never be parked on ``LISTEN`` because it would be handed
back out and its lifetime is not owned by the listener.

Lifecycle:

* startup: connect and ``LISTEN takeplace_realtime``;
* steady state: notification callbacks fan out to local SSE clients via the hub;
* connection loss: reconnect with bounded exponential backoff and, once
  reconnected, broadcast ``resync`` to every client — notifications sent while
  the listener was down were never seen, so clients must refetch authoritative
  state (§37.3);
* shutdown: cancel the task and close the connection.

The listener never raises into the application: a failed connect is logged and
retried, so a temporary database outage does not crash the API process.
"""

from __future__ import annotations

import asyncio
import contextlib

import asyncpg
import structlog
from sqlalchemy.engine import make_url

from app.realtime.events import CHANNEL, parse_payload
from app.realtime.hub import RealtimeHub

logger = structlog.get_logger("takeplace.realtime")

#: How often the steady-state loop checks whether the LISTEN connection dropped.
_CONNECTION_POLL_SECONDS = 1.0


def asyncpg_dsn(url: str) -> str:
    """Convert a SQLAlchemy asyncpg DSN to a plain ``postgresql://`` DSN."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


class RealtimeListener:
    """Owns one dedicated LISTEN connection and forwards events to the hub."""

    def __init__(
        self,
        dsn: str,
        hub: RealtimeHub,
        *,
        enabled: bool = True,
        backoff_initial: float = 0.5,
        backoff_max: float = 30.0,
        connect_timeout: float = 5.0,
    ) -> None:
        self._dsn = asyncpg_dsn(dsn)
        self._hub = hub
        self._enabled = enabled
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._connect_timeout = connect_timeout
        self._task: asyncio.Task[None] | None = None
        self._connection: asyncpg.Connection | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Whether the LISTEN connection is currently established."""
        return self._connected

    async def start(self) -> None:
        """Start the background listener task (no-op when disabled)."""
        if not self._enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="takeplace-realtime-listener")

    async def stop(self) -> None:
        """Cancel the listener and release the connection, if any."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_connection()

    async def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        self._connected = False
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    async def _on_notify(
        self,
        _connection: asyncpg.Connection,
        _pid: int,
        _channel: str,
        payload: str,
    ) -> None:
        """Forward one notification; invalid payloads are logged and dropped."""
        event = parse_payload(payload)
        if event is None:
            logger.warning("realtime_invalid_payload")
            return
        self._hub.publish(event)

    async def _run(self) -> None:
        backoff = self._backoff_initial
        connected_before = False
        while True:
            try:
                connection = await asyncpg.connect(self._dsn, timeout=self._connect_timeout)
                # Own the connection immediately so a failure in add_listener
                # still closes it in the ``finally`` below (no leak).
                self._connection = connection
                await connection.add_listener(CHANNEL, self._on_notify)
                self._connected = True
                backoff = self._backoff_initial
                if connected_before:
                    # Missed notifications while down: force every client to resync.
                    logger.info("realtime_listener_reconnected")
                    self._hub.broadcast_resync()
                connected_before = True
                logger.info("realtime_listener_started")
                while not connection.is_closed():
                    await asyncio.sleep(_CONNECTION_POLL_SECONDS)
                logger.warning("realtime_listener_disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the API process alive
                logger.warning("realtime_listener_error", error=str(exc))
            finally:
                await self._close_connection()
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, self._backoff_max)
