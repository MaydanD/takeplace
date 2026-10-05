"""Realtime signalling contract (PROJECT-SPEC §3.4, §37.2, §59).

Mutations and their realtime signal are bound *transactionally*: the publisher
runs ``pg_notify`` on the same SQLAlchemy session/transaction that performs the
business change, so PostgreSQL only delivers the notification after a successful
commit (§37.2). A rolled-back mutation therefore never produces a phantom event.

The payload is deliberately tiny and is *not* a source of truth — it only tells
subscribed admin clients which queries to invalidate/refetch. Authoritative
state is always read back through the ordinary HTTP API (§37).

Payload shape (§37.2)::

    {"venue_id": 1, "type": "booking.updated", "ids": [123]}

If a single mutation would affect too many ids (for example a bulk layout
operation), the list is dropped and the event collapses to ``type="resync"`` so
the payload stays comfortably below the PostgreSQL ``NOTIFY`` size limit and the
client performs a full venue-state refetch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Single PostgreSQL channel every API process LISTENs on (§37.3).
CHANNEL = "takeplace_realtime"

#: Event types. The frontend maps every ``booking.*`` type to booking-query
#: invalidation and ``resync`` to a full venue-state invalidate.
BOOKING_CREATED = "booking.created"
BOOKING_UPDATED = "booking.updated"
RESYNC = "resync"

#: Above this many ids the payload collapses to a ``resync`` event.
MAX_IDS = 64

_PG_NOTIFY = text("SELECT pg_notify(:channel, :payload)")


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    """A minimal, refetch-only realtime signal scoped to one venue."""

    venue_id: int
    type: str
    ids: tuple[int, ...] = ()

    def to_payload(self) -> str:
        """Serialise the event as compact JSON (no whitespace, stable keys)."""
        payload: dict[str, object] = {"venue_id": self.venue_id, "type": self.type}
        if self.ids:
            payload["ids"] = list(self.ids)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def normalize_event(venue_id: int, event_type: str, ids: object = ()) -> RealtimeEvent:
    """Build a bounded event, collapsing oversized id lists to ``resync``.

    ``ids`` is defensively coerced: duplicates are removed, order is made
    deterministic and non-integer/negative values are ignored so a malformed
    internal call cannot produce an invalid payload.
    """
    cleaned: list[int] = []
    seen: set[int] = set()
    if isinstance(ids, (list, tuple, set, frozenset)):
        for raw in ids:
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                continue
            if raw in seen:
                continue
            seen.add(raw)
            cleaned.append(raw)
    cleaned.sort()
    if len(cleaned) > MAX_IDS:
        return RealtimeEvent(venue_id=venue_id, type=RESYNC)
    return RealtimeEvent(venue_id=venue_id, type=event_type, ids=tuple(cleaned))


def parse_payload(payload: str) -> RealtimeEvent | None:
    """Parse a raw ``NOTIFY`` payload, returning ``None`` for anything invalid.

    A malformed payload never raises: the listener logs and drops it so one bad
    notification cannot tear down the LISTEN connection.
    """
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    venue_id = data.get("venue_id")
    event_type = data.get("type")
    if not isinstance(venue_id, int) or isinstance(venue_id, bool) or venue_id < 0:
        return None
    if not isinstance(event_type, str) or not event_type:
        return None
    return normalize_event(venue_id, event_type, data.get("ids", ()))


async def publish(
    session: AsyncSession,
    *,
    venue_id: int,
    event_type: str,
    ids: object = (),
) -> None:
    """Queue a ``pg_notify`` on the caller's current transaction (§37.2).

    Must be called *inside* the same transaction that performs the mutation, so
    the notification becomes visible to subscribers only after commit. The
    function never opens or commits a transaction of its own.
    """
    event = normalize_event(venue_id, event_type, ids)
    await session.execute(_PG_NOTIFY, {"channel": CHANNEL, "payload": event.to_payload()})
