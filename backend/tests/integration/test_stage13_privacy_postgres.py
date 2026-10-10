"""Stage 13 privacy/retention acceptance tests on real PostgreSQL.

Covers PROJECT-SPEC §40 (request-IP HMAC TTL), §42.2 (terminal-only booking
anonymization) and §6.3/§6.11 (session and outbox retention). These are DB
semantics, so they run against the real database, never a fake.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from app.db.models import Booking
from app.db.time import operation_now
from app.services.privacy import (
    anonymize_terminal_bookings,
    purge_expired_admin_sessions,
    purge_expired_request_ip_hmacs,
    purge_terminal_outbox,
    run_retention,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration.test_bookings_api import Venue
from tests.integration.test_bookings_concurrency import api_client as api_client
from tests.integration.test_bookings_concurrency import engine as engine

pytestmark = pytest.mark.integration

CANCEL_URL = "/api/admin/v1/bookings/{booking_id}/cancel"
_PII = ("guest_name", "guest_phone_raw", "guest_phone_normalized", "guest_comment")


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _create(venue: Venue, *, hour: int = 20, table_index: int = 0) -> dict:
    return venue.create(
        start=venue.at(hour),
        end=venue.at(hour + 2),
        table_ids=[venue.table_ids[table_index]],
    ).json()


def _cancel(venue: Venue, booking: dict) -> dict:
    response = venue.client.post(
        CANCEL_URL.format(booking_id=booking["id"]),
        headers=venue.headers(),
        json={"expected_version": booking["version"], "reason": "GUEST_CANCELED"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _age_canceled(session_factory, booking_id: int, *, days: float) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE bookings SET canceled_at = clock_timestamp() - make_interval(secs => :s) "
                "WHERE id = :id"
            ),
            {"s": days * 86400, "id": booking_id},
        )


async def _make_closed(session_factory, booking_id: int, *, days: float) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE bookings SET status = 'CLOSED', waiting_at = NULL, "
                "opened_at = clock_timestamp() - make_interval(secs => :s) - interval '1 hour', "
                "closed_at = clock_timestamp() - make_interval(secs => :s), "
                "canceled_at = NULL, cancellation_reason = NULL "
                "WHERE id = :id"
            ),
            {"s": days * 86400, "id": booking_id},
        )


async def _rows(session_factory, booking_id: int) -> Booking:
    async with session_factory() as session:
        return (await session.execute(select(Booking).where(Booking.id == booking_id))).scalar_one()


async def _anonymize(
    session_factory, *, retention_days: int = 90, batch_size: int = 50, venue_id=None
) -> int:
    """Run one anonymization batch.

    Scoped to a venue whenever the caller asserts an exact count: the shared test
    database keeps eligible rows from earlier suites (and from earlier runs of
    this file), and the pass must not be credited with those rows.
    """
    async with session_factory() as session, session.begin():
        return await anonymize_terminal_bookings(
            session,
            now=await operation_now(session),
            retention_days=retention_days,
            batch_size=batch_size,
            venue_id=venue_id,
        )


# --- booking anonymization (§42.2) ------------------------------------------


async def test_recent_terminal_booking_is_not_anonymized(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _cancel(venue, _create(venue))
    processed = await _anonymize(session_factory, venue_id=venue.venue_id)
    assert processed == 0
    row = await _rows(session_factory, booking["id"])
    assert row.anonymized_at is None
    assert row.guest_name and row.guest_phone_raw


async def test_expired_terminal_booking_is_anonymized(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _cancel(venue, _create(venue))
    await _age_canceled(session_factory, booking["id"], days=100)

    processed = await _anonymize(session_factory, venue_id=venue.venue_id)
    assert processed == 1

    row = await _rows(session_factory, booking["id"])
    # PII and short-lived identifiers are gone.
    assert row.guest_name is None
    assert row.guest_phone_raw is None
    assert row.guest_phone_normalized is None
    assert row.guest_comment is None
    assert row.cancellation_note is None
    assert row.public_idempotency_key is None
    assert row.public_request_hmac is None
    assert row.admin_idempotency_key is None
    assert row.admin_request_hmac is None
    assert row.request_ip_hmac is None
    assert row.anonymized_at is not None
    # Business history is preserved.
    assert row.status == "CANCELED"
    assert row.cancellation_reason == "GUEST_CANCELED"
    assert row.number == 1
    assert row.party_size == 2


async def test_closed_booking_is_anonymized(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    await _make_closed(session_factory, booking["id"], days=100)
    processed = await _anonymize(session_factory, venue_id=venue.venue_id)
    assert processed == 1
    row = await _rows(session_factory, booking["id"])
    assert row.status == "CLOSED" and row.anonymized_at is not None and row.guest_name is None


async def test_anonymization_is_idempotent(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _cancel(venue, _create(venue))
    await _age_canceled(session_factory, booking["id"], days=100)
    assert await _anonymize(session_factory, venue_id=venue.venue_id) == 1
    async with session_factory() as session:
        first_mark = (await session.get(Booking, booking["id"])).anonymized_at
    # A second pass finds nothing left to do and does not move the marker.
    assert await _anonymize(session_factory, venue_id=venue.venue_id) == 0
    assert (await _rows(session_factory, booking["id"])).anonymized_at == first_mark


async def test_active_bookings_are_never_anonymized(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    active = _create(venue)  # NEW: no terminal timestamp, so never eligible
    assert await _anonymize(session_factory, venue_id=venue.venue_id) == 0
    row = await _rows(session_factory, active["id"])
    assert row.anonymized_at is None and row.guest_name


async def test_open_booking_is_never_anonymized(api_client, tmp_path, session_factory):
    # Audit FIX-02: only CLOSED/CANCELED are terminal; an OPEN booking is live
    # operational state, so an old timestamp must not make it eligible.
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE bookings SET status = 'OPEN', waiting_at = NULL, "
                "opened_at = clock_timestamp() - interval '200 days' WHERE id = :id"
            ),
            {"id": booking["id"]},
        )
    assert await _anonymize(session_factory, venue_id=venue.venue_id) == 0
    row = await _rows(session_factory, booking["id"])
    assert row.anonymized_at is None and row.guest_name


async def test_anonymization_is_tenant_scoped(api_client, tmp_path, session_factory):
    a = Venue(api_client, tmp_path)
    b = Venue(api_client, tmp_path)
    booked_a = _cancel(a, _create(a))
    booked_b = _cancel(b, _create(b))
    await _age_canceled(session_factory, booked_a["id"], days=100)
    await _age_canceled(session_factory, booked_b["id"], days=100)

    async with session_factory() as session, session.begin():
        processed = await anonymize_terminal_bookings(
            session,
            now=await operation_now(session),
            retention_days=90,
            batch_size=50,
            venue_id=a.venue_id,
        )
    assert processed == 1
    assert (await _rows(session_factory, booked_a["id"])).anonymized_at is not None
    assert (await _rows(session_factory, booked_b["id"])).anonymized_at is None

    # Leave the shared test database tidy: the deliberately untouched venue B row
    # would otherwise be picked up by an unscoped pass in a later run.
    assert await _anonymize(session_factory, venue_id=b.venue_id) == 1


async def test_batching_processes_every_row_across_passes(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path, tables=3)
    ids = []
    for index in range(3):
        booking = venue.create(
            start=venue.at(18),
            end=venue.at(20),
            table_ids=[venue.table_ids[index]],
        ).json()
        _cancel(venue, booking)
        ids.append(booking["id"])
    for booking_id in ids:
        await _age_canceled(session_factory, booking_id, days=100)

    async with session_factory() as session, session.begin():
        report = await run_retention(
            session,
            now=await operation_now(session),
            pii_retention_days=90,
            outbox_retention_days=30,
            batch_size=1,
            # Scoped to this venue: the shared test database may hold other
            # venues' eligible rows, and the pass must still drain every batch.
            venue_id=venue.venue_id,
        )
    assert report.anonymized_bookings == 3
    for booking_id in ids:
        assert (await _rows(session_factory, booking_id)).anonymized_at is not None


# --- request-IP HMAC cleanup (§40) ------------------------------------------


async def _set_ip_hmac(session_factory, booking_id: int, *, expires_delta_seconds: float) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE bookings SET request_ip_hmac = 'fingerprint', "
                "request_ip_hmac_expires_at = clock_timestamp() + make_interval(secs => :s) "
                "WHERE id = :id"
            ),
            {"s": expires_delta_seconds, "id": booking_id},
        )


async def test_expired_ip_hmac_is_cleared_and_fresh_is_retained(
    api_client, tmp_path, session_factory
):
    venue = Venue(api_client, tmp_path)
    stale = _create(venue, table_index=0)
    fresh = _create(venue, table_index=1)
    await _set_ip_hmac(session_factory, stale["id"], expires_delta_seconds=-1)
    await _set_ip_hmac(session_factory, fresh["id"], expires_delta_seconds=3600)
    async with session_factory() as session, session.begin():
        cleared = await purge_expired_request_ip_hmacs(session, now=await operation_now(session))
    assert cleared == 1
    assert (await _rows(session_factory, stale["id"])).request_ip_hmac is None
    assert (await _rows(session_factory, fresh["id"])).request_ip_hmac == "fingerprint"


async def test_ip_hmac_boundary_is_inclusive(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    async with session_factory() as session, session.begin():
        now = await operation_now(session)
        await session.execute(
            text(
                "UPDATE bookings SET request_ip_hmac = 'fingerprint', "
                "request_ip_hmac_expires_at = :now WHERE id = :id"
            ),
            {"now": now, "id": booking["id"]},
        )
        assert await purge_expired_request_ip_hmacs(session, now=now) == 1


async def test_ip_hmac_batching_drains_every_eligible_row(api_client, tmp_path, session_factory):
    """Audit FIX-05: a bounded batch must still drain all eligible rows."""
    venue = Venue(api_client, tmp_path, tables=3)
    bookings = [_create(venue, table_index=index) for index in range(3)]
    for booking in bookings:
        await _set_ip_hmac(session_factory, booking["id"], expires_delta_seconds=-5)

    async with session_factory() as session:
        now = await operation_now(session)
    total = 0
    for _ in range(10):  # bounded: the loop must terminate on its own
        async with session_factory() as session, session.begin():
            batch = await purge_expired_request_ip_hmacs(session, now=now, batch_size=1)
        total += batch
        if batch == 0:
            break
    assert total >= 3
    for booking in bookings:
        assert (await _rows(session_factory, booking["id"])).request_ip_hmac is None


# --- session retention (§6.3) -----------------------------------------------


async def test_admin_session_batching_drains_every_eligible_row(
    api_client, tmp_path, session_factory
):
    """Audit FIX-05: expired sessions are removed in bounded batches, none skipped."""
    venue = Venue(api_client, tmp_path)
    tokens = [uuid.uuid4().hex for _ in range(3)]
    async with session_factory() as session, session.begin():
        now = await operation_now(session)
        for token in tokens:
            await session.execute(
                text(
                    "INSERT INTO admin_sessions (venue_id, token_hash, created_at, expires_at, "
                    "last_seen_at) VALUES (:v, :t, :c, :e, :c)"
                ),
                {
                    "v": venue.venue_id,
                    "t": token,
                    "c": now - timedelta(days=40),
                    "e": now - timedelta(days=10),
                },
            )
    total = 0
    async with session_factory() as session:
        for _ in range(10):  # bounded: the loop must terminate on its own
            async with session.begin():
                batch = await purge_expired_admin_sessions(session, now=now, batch_size=1)
            total += batch
            if batch == 0:
                break
    assert total >= 3
    async with session_factory() as session:
        remaining = await session.scalar(
            text(
                "SELECT count(*) FROM admin_sessions WHERE venue_id = :v AND token_hash = ANY(:t)"
            ),
            {"v": venue.venue_id, "t": tokens},
        )
    assert remaining == 0


async def test_expired_sessions_are_deleted_and_valid_kept(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    async with session_factory() as session, session.begin():
        now = await operation_now(session)
        expired_token = uuid.uuid4().hex
        expired = (
            await session.execute(
                text(
                    "INSERT INTO admin_sessions (venue_id, token_hash, created_at, expires_at, "
                    "last_seen_at) VALUES (:v, :t, :c, :e, :c) RETURNING id"
                ),
                {
                    "v": venue.venue_id,
                    "t": expired_token,
                    "c": now - timedelta(days=40),
                    "e": now - timedelta(days=10),
                },
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO booking_events (venue_id, booking_id, event_type, actor_type, "
                "admin_session_id, payload, created_at) "
                "VALUES (:v, :b, 'BOOKING_CREATED', 'ADMIN', :s, '{}'::jsonb, :c)"
            ),
            {"v": venue.venue_id, "b": booking["id"], "s": expired, "c": now},
        )
        valid = uuid.uuid4().hex
        await session.execute(
            text(
                "INSERT INTO admin_sessions (venue_id, token_hash, created_at, expires_at, "
                "last_seen_at) VALUES (:v, :t, :c, :e, :c)"
            ),
            {"v": venue.venue_id, "t": valid, "c": now, "e": now + timedelta(days=1)},
        )
        deleted = await purge_expired_admin_sessions(session, now=now)
        assert (
            await session.scalar(
                text("SELECT count(*) FROM admin_sessions WHERE token_hash = :t"),
                {"t": expired_token},
            )
            == 0
        )
    assert deleted >= 1
    async with session_factory() as session:
        remaining = await session.scalar(
            text("SELECT count(*) FROM admin_sessions WHERE venue_id = :v AND token_hash = :t"),
            {"v": venue.venue_id, "t": valid},
        )
        assert remaining == 1
        # The FK is ON DELETE SET NULL (admin_session_id): the history rows
        # survive, keep their venue_id, and no longer point at the deleted
        # session (§6.10).
        events = (
            await session.execute(
                text(
                    "SELECT venue_id, admin_session_id FROM booking_events "
                    "WHERE booking_id = :b AND actor_type = 'ADMIN'"
                ),
                {"b": booking["id"]},
            )
        ).all()
    assert events
    assert all(event.venue_id == venue.venue_id for event in events)
    assert all(event.admin_session_id != expired for event in events)


# --- outbox retention (§6.11) -----------------------------------------------


async def _insert_outbox(session_factory, venue, *, status: str, age_days: float) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                """
                INSERT INTO notification_outbox (
                    venue_id, type, dedup_key, payload, status, attempts,
                    next_attempt_at, expires_at, sent_at, skipped_at, skip_reason,
                    acknowledged_at, created_at
                ) VALUES (
                    :v, 'ONLINE_BOOKING', :d, '{}'::jsonb, :status, 1,
                    clock_timestamp(), clock_timestamp(),
                    CASE WHEN :kind = 'SENT' THEN clock_timestamp() - make_interval(secs => :s) END,
                    CASE WHEN :kind = 'SKIPPED' THEN clock_timestamp() - make_interval(secs => :s) END,
                    CASE WHEN :kind = 'SKIPPED' THEN 'EXPIRED' END,
                    CASE WHEN :kind = 'DEAD_ACK' THEN clock_timestamp() - make_interval(secs => :s) END,
                    clock_timestamp() - make_interval(secs => :s)
                )
                """
            ),
            {
                "v": venue.venue_id,
                "d": f"stage13:{status}:{uuid.uuid4().hex}",
                # ``kind`` drives the timestamp columns; ``status`` is the stored
                # DB state (an acknowledged dead row is still status='DEAD').
                "kind": status,
                "status": "DEAD" if status == "DEAD_ACK" else status,
                "s": age_days * 86400,
            },
        )


async def test_outbox_retention_only_prunes_terminal_rows(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    for status in ("SENT", "SKIPPED", "DEAD_ACK"):
        await _insert_outbox(session_factory, venue, status=status, age_days=100)
    await _insert_outbox(session_factory, venue, status="DEAD", age_days=100)  # unacknowledged
    await _insert_outbox(session_factory, venue, status="PENDING", age_days=100)

    async with session_factory() as session, session.begin():
        deleted = await purge_terminal_outbox(
            session, now=await operation_now(session), retention_days=30, batch_size=50
        )
    assert deleted == 3
    async with session_factory() as session:
        remaining = dict(
            (
                await session.execute(
                    text(
                        "SELECT status, count(*) FROM notification_outbox WHERE venue_id = :v "
                        "GROUP BY status"
                    ),
                    {"v": venue.venue_id},
                )
            ).all()
        )
    assert remaining == {"DEAD": 1, "PENDING": 1}
