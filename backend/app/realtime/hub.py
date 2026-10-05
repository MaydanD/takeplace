"""Per-process realtime fan-out to connected SSE subscribers (PROJECT-SPEC §37.3).

Each API process runs its own LISTEN connection and fans the notifications it
receives out to *its* local SSE clients. There is no cross-process in-memory
pub/sub: PostgreSQL ``NOTIFY`` is the single transport between instances, so any
number of API processes may run concurrently and each one is sufficient for its
own clients.

Subscriptions are grouped by ``venue_id`` and each carries a bounded queue. A
slow or dead client can never block the others: delivery uses ``put_nowait`` and
an overflowing queue is closed with a ``resync`` signal so the client reconnects
and refetches authoritative state (§37.4).
"""

from __future__ import annotations

import asyncio

from app.realtime.events import RESYNC, RealtimeEvent

DEFAULT_QUEUE_MAXSIZE = 256


class Subscription:
    """A single SSE client's queue, bounded and venue-scoped.

    A ``None`` sentinel placed by :meth:`offer` means "overflowed": the consumer
    must emit a ``resync`` and terminate so the client reconnects.
    """

    __slots__ = ("venue_id", "queue", "_closed")

    def __init__(self, venue_id: int, *, maxsize: int) -> None:
        self.venue_id = venue_id
        self.queue: asyncio.Queue[RealtimeEvent | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def offer(self, event: RealtimeEvent) -> None:
        """Deliver an event without blocking; overflow signals a resync close."""
        if self._closed:
            return
        if self.queue.full():
            self._overflow()
            return
        self.queue.put_nowait(event)

    def _overflow(self) -> None:
        # Drop the backlog and place the sentinel; the generator sees it, emits a
        # resync and ends the stream (§37.4 bounded queue -> close).
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - defensive
                break
        self.queue.put_nowait(None)

    def close(self) -> None:
        """Mark the subscription closed and unblock a waiting consumer."""
        if self._closed:
            return
        self._closed = True


class RealtimeHub:
    """In-process registry of SSE subscriptions keyed by venue."""

    def __init__(self, *, queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE) -> None:
        self._queue_maxsize = queue_maxsize
        self._subs: dict[int, set[Subscription]] = {}

    def subscribe(self, venue_id: int) -> Subscription:
        """Register a client for one venue and return its subscription."""
        subscription = Subscription(venue_id, maxsize=self._queue_maxsize)
        self._subs.setdefault(venue_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a client; safe to call twice."""
        subscription.close()
        subscribers = self._subs.get(subscription.venue_id)
        if not subscribers:
            return
        subscribers.discard(subscription)
        if not subscribers:
            self._subs.pop(subscription.venue_id, None)

    def publish(self, event: RealtimeEvent) -> None:
        """Fan an event out to the subscribers of exactly one venue (§37, §7).

        Tenant isolation lives here: a subscriber of venue A can only ever be
        reached by an event whose ``venue_id`` is A. There is no broadcast path
        that ignores the venue.
        """
        subscribers = self._subs.get(event.venue_id)
        if not subscribers:
            return
        for subscription in list(subscribers):
            subscription.offer(event)

    def broadcast_resync(self) -> None:
        """Tell every connected client to refetch (LISTEN reconnect, §37.3).

        Emitted after a lost-and-restored LISTEN connection: notifications sent
        while the listener was down were never seen, so clients must resync
        through the authoritative HTTP API rather than trust the event stream.
        """
        for venue_id, subscribers in list(self._subs.items()):
            event = RealtimeEvent(venue_id=venue_id, type=RESYNC)
            for subscription in list(subscribers):
                subscription.offer(event)

    def subscriber_count(self, venue_id: int | None = None) -> int:
        """Number of live subscriptions, for tests and diagnostics."""
        if venue_id is not None:
            return len(self._subs.get(venue_id, ()))
        return sum(len(subscribers) for subscribers in self._subs.values())


_hub: RealtimeHub | None = None


def init_hub(*, queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE) -> RealtimeHub:
    """Initialise the process-wide hub (idempotent per process generation)."""
    global _hub
    if _hub is None:
        _hub = RealtimeHub(queue_maxsize=queue_maxsize)
    return _hub


def get_hub() -> RealtimeHub:
    """Return the process-wide hub, creating a default one if needed."""
    return _hub if _hub is not None else init_hub()


def reset_hub() -> None:
    """Drop the process-wide hub (used on shutdown and by tests)."""
    global _hub
    _hub = None
