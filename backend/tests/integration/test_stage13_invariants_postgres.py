"""Stage 13 invariant audit acceptance tests on real PostgreSQL (PROJECT-SPEC §60).

The audit must report zero corruptions on a healthy database and must actually
*find* deliberate corruption where the schema still allows it. Corrupt fixtures
are created inside an uncommitted transaction and rolled back, so the shared test
database is left clean.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.db.time import operation_now
from app.services.invariants import run_invariant_audit
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration.test_bookings_api import Venue
from tests.integration.test_bookings_concurrency import api_client as api_client
from tests.integration.test_bookings_concurrency import engine as engine

pytestmark = pytest.mark.integration

_STUCK_LEASE = 3600


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _create(venue: Venue, *, hour: int = 20, table_index: int = 0) -> dict:
    return venue.create(
        start=venue.at(hour),
        end=venue.at(hour + 2),
        table_ids=[venue.table_ids[table_index]],
    ).json()


def _cancel(venue: Venue, booking: dict) -> None:
    response = venue.client.post(
        f"/api/admin/v1/bookings/{booking['id']}/cancel",
        headers=venue.headers(),
        json={"expected_version": booking["version"], "reason": "GUEST_CANCELED"},
    )
    assert response.status_code == 200, response.text


async def _audit(session, *, now=None):
    moment = now or await operation_now(session)
    return await run_invariant_audit(
        session, now=moment, stuck_before=moment - timedelta(seconds=_STUCK_LEASE)
    )


async def test_healthy_database_has_no_corruption(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    active = _create(venue, table_index=0)
    cancelled = _create(venue, table_index=1)
    _cancel(venue, cancelled)
    async with session_factory() as session:
        report = await _audit(session)
    assert report.is_clean, report.corruptions
    assert report.total_corruptions == 0
    assert all(check.count == 0 for check in report.checks if check.severity == "corruption")
    assert active  # silence unused warning; the booking is the healthy fixture


async def test_audit_detects_canceled_with_active_occupancy(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    _cancel(venue, booking)
    async with session_factory() as session:
        await session.execute(
            text("UPDATE table_occupancies SET is_active = true WHERE booking_id = :b"),
            {"b": booking["id"]},
        )
        report = await _audit(session)
        assert report.corruptions.get("canceled_with_active_booking_occupancy") == 1
        await session.rollback()


async def test_audit_detects_closed_with_live_rows(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE bookings SET status = 'CLOSED', waiting_at = NULL, "
                "opened_at = starts_at, closed_at = ends_at "
                "WHERE id = :b"
            ),
            {"b": booking["id"]},
        )
        await session.execute(
            text(
                "INSERT INTO booking_live_tables "
                "(venue_id, booking_id, business_date, table_id, live_since) "
                "SELECT venue_id, id, business_date, :t, clock_timestamp() "
                "FROM bookings WHERE id = :b"
            ),
            {"b": booking["id"], "t": venue.table_ids[0]},
        )
        report = await _audit(session)
        assert report.corruptions.get("closed_with_live_rows") == 1
        await session.rollback()


async def test_audit_detects_open_in_shift_without_live_rows(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE bookings SET status = 'OPEN', waiting_at = NULL, "
                "opened_at = starts_at WHERE id = :b"
            ),
            {"b": booking["id"]},
        )
        shift_start = (
            await session.execute(
                text("SELECT shift_starts_at FROM bookings WHERE id = :b"),
                {"b": booking["id"]},
            )
        ).scalar_one()
        report = await _audit(session, now=shift_start)
        assert report.corruptions.get("open_in_shift_without_live_rows", 0) >= 1
        await session.rollback()


async def test_audit_detects_stuck_processing_lease(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO notification_outbox (
                    venue_id, type, dedup_key, payload, status, attempts,
                    next_attempt_at, locked_until, expires_at, created_at
                ) VALUES (
                    :v, 'ONLINE_BOOKING', :d, '{}'::jsonb, 'PROCESSING', 1,
                    clock_timestamp(), clock_timestamp() - interval '2 hours',
                    clock_timestamp(), clock_timestamp()
                )
                """
            ),
            {"v": venue.venue_id, "d": f"stage13-stuck:{venue.venue_id}"},
        )
        report = await _audit(session)
        assert report.corruptions.get("stuck_processing_outbox_lease") == 1
        await session.rollback()


async def test_audit_detects_pii_keys_in_event_payload(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    booking = _create(venue)
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO booking_events "
                "(venue_id, booking_id, event_type, actor_type, payload, created_at) "
                "VALUES (:v, :b, 'BOOKING_CREATED', 'SYSTEM', "
                '        \'{"guest_name": "leak"}\'::jsonb, clock_timestamp())'
            ),
            {"v": venue.venue_id, "b": booking["id"]},
        )
        report = await _audit(session)
        assert report.corruptions.get("booking_events_pii_keys") == 1
        await session.rollback()


async def test_audit_detects_pii_keys_in_outbox_payload(api_client, tmp_path, session_factory):
    # Audit FIX-04: ``notification_outbox.payload`` is audited for forbidden PII
    # keys exactly like ``booking_events.payload``.
    venue = Venue(api_client, tmp_path)
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO notification_outbox (
                    venue_id, type, dedup_key, payload, status, attempts,
                    next_attempt_at, expires_at, created_at
                ) VALUES (
                    :v, 'ONLINE_BOOKING', :d, '{"guest_name": "leak"}'::jsonb,
                    'PENDING', 0, clock_timestamp(), clock_timestamp(), clock_timestamp()
                )
                """
            ),
            {"v": venue.venue_id, "d": f"stage13-outbox-pii:{venue.venue_id}"},
        )
        report = await _audit(session)
        assert report.corruptions.get("notification_outbox_pii_keys") == 1
        await session.rollback()


async def test_audit_accepts_the_minimal_outbox_payload(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO notification_outbox (
                    venue_id, type, dedup_key, payload, status, attempts,
                    next_attempt_at, expires_at, created_at
                ) VALUES (
                    :v, 'ONLINE_BOOKING', :d,
                    '{"booking_id": 1, "kind": "BOOKING_CREATED", "formatter_version": 1}'::jsonb,
                    'PENDING', 0, clock_timestamp(), clock_timestamp(), clock_timestamp()
                )
                """
            ),
            {"v": venue.venue_id, "d": f"stage13-outbox-safe:{venue.venue_id}"},
        )
        report = await _audit(session)
        assert report.corruptions.get("notification_outbox_pii_keys") is None
        await session.rollback()


async def test_audit_reports_unacknowledged_dead_as_alert_not_corruption(
    api_client, tmp_path, session_factory
):
    venue = Venue(api_client, tmp_path)
    async with session_factory() as session:
        # The shared test database accumulates DEAD rows from other suites, so
        # this asserts the delta for the row inserted here rather than an
        # absolute total.
        before = (await _audit(session)).alerts.get("unacknowledged_dead_outbox", 0)
        await session.execute(
            text(
                """
                INSERT INTO notification_outbox (
                    venue_id, type, dedup_key, payload, status, attempts,
                    next_attempt_at, expires_at, last_error, created_at
                ) VALUES (
                    :v, 'ONLINE_BOOKING', :d, '{}'::jsonb, 'DEAD', 3,
                    clock_timestamp(), clock_timestamp(), 'vk:5:permanent', clock_timestamp()
                )
                """
            ),
            {"v": venue.venue_id, "d": f"stage13-dead:{venue.venue_id}"},
        )
        report = await _audit(session)
        assert report.alerts.get("unacknowledged_dead_outbox", 0) == before + 1
        assert "unacknowledged_dead_outbox" not in report.corruptions
        assert report.is_clean, report.corruptions
        await session.rollback()
