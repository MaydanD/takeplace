"Stage 12 atomic outbox acceptance tests on real PostgreSQL."

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from app.db.models import (
    Booking,
    BookingEvent,
    NotificationOutbox,
    TableOccupancy,
    VenueVKIntegration,
)
from app.db.time import operation_now
from app.integrations.vk import VKClient
from app.services.bookings import PublicBookingInput, create_public_booking
from app.services.outbox_worker import claim_jobs, mark_dead, mark_sent
from app.worker import VKOutboxWorker
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration.support import make_settings
from tests.integration.test_bookings_api import MSK, Venue
from tests.integration.test_bookings_concurrency import HMAC_KEY
from tests.integration.test_bookings_concurrency import api_client as api_client
from tests.integration.test_bookings_concurrency import engine as engine

pytestmark = pytest.mark.integration


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _online(session_factory, venue, key, table_index=0):
    venue.client.patch(
        "/api/admin/v1/settings", headers=venue.headers(), json={"online_booking_enabled": True}
    )
    now = datetime.now(MSK)
    start = (now + timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=2)
    async with session_factory() as session:
        view, created = await create_public_booking(
            session,
            venue_id=venue.venue_id,
            data=PublicBookingInput(
                starts_at=start,
                ends_at=end,
                table_id=venue.table_ids[table_index],
                party_size=2,
                guest_name="outbox-test",
                guest_phone_raw="+79990000000",
            ),
            idempotency_key=key,
            hmac_key=HMAC_KEY,
            request_ip_hmac="test",
            ip_hmac_ttl_days=1,
        )
        return view.booking.id, created


async def test_online_booking_atomic_outbox_disabled_integration(
    api_client, tmp_path, session_factory
):
    venue = Venue(api_client, tmp_path)
    venue.client.patch(
        "/api/admin/v1/settings", headers=venue.headers(), json={"online_booking_enabled": True}
    )
    booking_id, created = await _online(session_factory, venue, str(uuid.uuid4()))
    assert created
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Booking).where(Booking.id == booking_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TableOccupancy)
                .where(TableOccupancy.booking_id == booking_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BookingEvent)
                .where(BookingEvent.booking_id == booking_id)
            )
            == 1
        )
        jobs = list(
            (
                await session.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.payload["booking_id"].astext == str(booking_id)
                    )
                )
            ).all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == "PENDING"
        serialized = str(jobs[0].payload)
        assert all(
            value not in serialized
            for value in ("outbox-test", "+79990000000", "access_token", "guest_comment")
        )
        assert jobs[0].payload == {
            "booking_id": booking_id,
            "kind": "BOOKING_CREATED",
            "formatter_version": 1,
        }


async def test_public_idempotent_replay_has_single_outbox(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    venue.client.patch(
        "/api/admin/v1/settings", headers=venue.headers(), json={"online_booking_enabled": True}
    )
    key = str(uuid.uuid4())
    first, created = await _online(session_factory, venue, key)
    second, replay_created = await _online(session_factory, venue, key)
    assert first == second and created and not replay_created
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Booking).where(Booking.id == first)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BookingEvent)
                .where(
                    BookingEvent.booking_id == first, BookingEvent.event_type == "BOOKING_CREATED"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.payload["booking_id"].astext == str(first))
            )
            == 1
        )


async def test_manual_booking_and_mutation_do_not_create_outbox(
    api_client, tmp_path, session_factory
):
    venue = Venue(api_client, tmp_path)
    venue.create(start=venue.at(20), end=venue.at(22))
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.venue_id == venue.venue_id)
            )
            == 0
        )


async def test_forced_rollback_removes_entire_online_transaction(
    api_client, tmp_path, session_factory, monkeypatch
):
    from app.services import bookings

    venue = Venue(api_client, tmp_path)
    original = bookings.enqueue_online_booking_notification

    async def fail_after_enqueue(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError("forced after outbox flush")

    monkeypatch.setattr(bookings, "enqueue_online_booking_notification", fail_after_enqueue)
    with pytest.raises(RuntimeError, match="forced"):
        await _online(session_factory, venue, str(uuid.uuid4()))
    async with session_factory() as session:
        for model in (Booking, TableOccupancy, BookingEvent, NotificationOutbox):
            assert (
                await session.scalar(
                    select(func.count()).select_from(model).where(model.venue_id == venue.venue_id)
                )
                == 0
            )


@pytest.mark.parametrize("source", ["PHONE", "VK", "OTHER", "WALK_IN", "ONLINE"])
async def test_event_policy_all_admin_mutations(
    api_client, tmp_path, session_factory, monkeypatch, source
):
    from tests.integration.test_booking_lifecycle import clock_at

    venue = Venue(api_client, tmp_path)
    if source == "ONLINE":
        booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
        booking = venue.client.get(
            f"/api/admin/v1/bookings/{booking_id}", headers=venue.headers()
        ).json()
    else:
        booking = venue.create(start=venue.at(20), end=venue.at(22), source=source).json()

    async def no_outbox():
        async with session_factory() as session:
            assert await session.scalar(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.venue_id == venue.venue_id)
            ) == (1 if source == "ONLINE" else 0)

    await no_outbox()

    def mutate(method, suffix, **body):
        nonlocal booking
        response = venue.client.request(
            method,
            f"/api/admin/v1/bookings/{booking['id']}{suffix}",
            headers=venue.headers(),
            params={"expected_version": booking["version"]} if method == "DELETE" else None,
            json={"expected_version": booking["version"], **body},
        )
        assert response.status_code == 200, response.text
        booking = response.json()

    mutate("PATCH", "", guest_name="changed")
    await no_outbox()
    mutate("POST", "/tables", table_ids=[venue.table_ids[1]])
    await no_outbox()
    mutate("DELETE", f"/tables/{venue.table_ids[1]}")
    await no_outbox()
    mutate(
        "POST",
        "/replace-table",
        from_table_ids=[venue.table_ids[0]],
        to_table_ids=[venue.table_ids[2]],
    )
    await no_outbox()
    mutate("POST", "/change-time", starts_at=venue.at(19), ends_at=venue.at(22))
    await no_outbox()
    mutate("POST", "/change-time", ends_at=venue.at(23))
    await no_outbox()
    clock_at(monkeypatch, datetime.fromisoformat(venue.at(20)))
    mutate("POST", "/wait")
    await no_outbox()
    mutate("POST", "/open")
    await no_outbox()
    mutate("POST", "/undo-open")
    await no_outbox()
    mutate("POST", "/open")
    mutate(
        "POST",
        "/replace-table",
        from_table_ids=[venue.table_ids[2]],
        to_table_ids=[venue.table_ids[1]],
    )
    await no_outbox()
    mutate("POST", "/close")
    await no_outbox()
    booking = venue.create(start=venue.at(21), end=venue.at(23)).json()
    mutate("POST", "/cancel", reason="GUEST_CANCELED")
    await no_outbox()


VK_URL = "/api/admin/v1/integrations/vk"
OUTBOX_URL = "/api/admin/v1/outbox"


def configure(venue, enabled=True):
    result = venue.client.put(
        VK_URL,
        headers=venue.headers(),
        json={
            "enabled": enabled,
            "community_id": 123,
            "peer_id": 2000000001,
            "access_token": "test-vk-secret-never-return",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["has_token"] is True
    assert "test-vk-secret" not in result.text
    return result


async def job_for(factory, booking_id):
    async with factory() as session:
        return await session.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.payload["booking_id"].astext == str(booking_id)
            )
        )


async def claim_for(factory, job_id):
    async with factory() as session, session.begin():
        jobs = await claim_jobs(
            session, now=await operation_now(session), lease_seconds=120, batch_size=10000
        )
        return next((j for j in jobs if j.outbox_id == job_id), None)


@pytest.mark.parametrize("configured", [False, True])
async def test_disabled_integration_skips_without_http(
    api_client, tmp_path, session_factory, configured
):
    venue = Venue(api_client, tmp_path)
    if configured:
        configure(venue, enabled=False)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    assert row.status == "PENDING"
    assert await claim_for(session_factory, row.id) is None
    row = await job_for(session_factory, booking_id)
    assert (row.status, row.skip_reason, row.attempts) == ("SKIPPED", "INTEGRATION_DISABLED", 0)
    assert row.skipped_at is not None


@pytest.mark.parametrize(
    "scenario",
    ["success", "retry", "permanent", "max_attempts", "cancel", "disable_after_claim", "crash"],
)
async def test_worker_delivery_smoke(
    api_client, tmp_path, session_factory, monkeypatch, caplog, scenario
):
    venue = Venue(api_client, tmp_path)
    configure(venue)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    claimed = await claim_for(session_factory, row.id)
    assert claimed is not None
    requests = []

    async def transport(request):
        # A separately opened connection can acquire the row immediately: HTTP
        # is not enclosed by the claim/bookkeeping transaction.
        async with session_factory() as session, session.begin():
            locked = await session.scalar(
                select(NotificationOutbox.id)
                .where(NotificationOutbox.id == row.id)
                .with_for_update(nowait=True)
            )
            assert locked == row.id
        requests.append(parse_qs(request.content.decode()))
        code = (
            6
            if scenario in ("retry", "max_attempts") and len(requests) == 1
            else 5
            if scenario == "permanent"
            else None
        )
        return httpx.Response(
            200,
            json={"error": {"error_code": code, "error_msg": "test-vk-secret-never-return"}}
            if code
            else {"response": 777},
        )

    worker = VKOutboxWorker(
        make_settings(vk_worker_max_attempts=1 if scenario == "max_attempts" else 8),
        client=VKClient(api_version="5.199", transport=httpx.MockTransport(transport)),
    )
    worker._session_factory = session_factory
    if scenario == "success":
        response = venue.client.patch(
            f"/api/admin/v1/bookings/{booking_id}",
            headers=venue.headers(),
            json={
                "expected_version": 1,
                "guest_name": "current guest",
                "guest_comment": "private comment",
            },
        )
        assert response.status_code == 200
    if scenario == "cancel":
        response = venue.client.post(
            f"/api/admin/v1/bookings/{booking_id}/cancel",
            headers=venue.headers(),
            json={"expected_version": 1, "reason": "GUEST_CANCELED"},
        )
        assert response.status_code == 200
    if scenario == "disable_after_claim":
        configure(venue, enabled=False)
    original_mark = mark_sent
    if scenario == "crash":

        async def fail_sent(*args, **kwargs):
            raise RuntimeError("crash after provider success")

        monkeypatch.setattr("app.worker.mark_sent", fail_sent)
    try:
        await worker._deliver(claimed)
        row = await job_for(session_factory, booking_id)
        if scenario in ("cancel", "disable_after_claim"):
            assert not requests
            assert row.status == "SKIPPED"
            assert row.skip_reason == (
                "BOOKING_INACTIVE" if scenario == "cancel" else "INTEGRATION_DISABLED"
            )
        elif scenario in ("permanent", "max_attempts"):
            assert row.status == "DEAD"
            assert "test-vk-secret" not in row.last_error
        elif scenario in ("retry", "crash"):
            assert row.status == ("RETRY" if scenario == "retry" else "PROCESSING")
            if scenario == "retry":
                assert row.next_attempt_at > row.created_at
            async with session_factory() as session, session.begin():
                current = await session.get(NotificationOutbox, row.id)
                now = await operation_now(session)
                current.locked_until = now - timedelta(seconds=1)
                current.next_attempt_at = now - timedelta(seconds=1)
            monkeypatch.setattr("app.worker.mark_sent", original_mark)
            second = await claim_for(session_factory, row.id)
            assert second.provider_dedup_id == claimed.provider_dedup_id
            assert second.attempts == 2
            # An old lease cannot overwrite the reclaimed job.
            async with session_factory() as session, session.begin():
                await mark_dead(session, job=claimed, error="stale worker")
            assert (await job_for(session_factory, booking_id)).status == "PROCESSING"
            await worker._deliver(second)
            assert (await job_for(session_factory, booking_id)).status == "SENT"
            assert requests[0]["random_id"] == requests[1]["random_id"]
            assert len({r["random_id"][0] for r in requests}) == 1
        else:
            assert row.status == "SENT"
            await worker._deliver(claimed)
            assert len(requests) == 1
            assert requests[0]["access_token"] == ["test-vk-secret-never-return"]
            assert "current guest" in requests[0]["message"][0]
            assert "private comment" not in requests[0]["message"][0]
        assert "test-vk-secret-never-return" not in caplog.text
        assert "+79990000000" not in caplog.text
    finally:
        await worker._engine.dispose()


async def test_parallel_claim_skip_locked(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    configure(venue)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    locked = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with session_factory() as session, session.begin():
            jobs = await claim_jobs(
                session, now=await operation_now(session), lease_seconds=120, batch_size=10000
            )
            locked.set()
            await asyncio.wait_for(release.wait(), 5)
            return jobs

    async def second():
        await locked.wait()
        try:
            async with session_factory() as session, session.begin():
                return await claim_jobs(
                    session, now=await operation_now(session), lease_seconds=120, batch_size=10000
                )
        finally:
            release.set()

    a, b = await asyncio.wait_for(asyncio.gather(first(), second()), 10)
    assert sum(j.outbox_id == row.id for j in a + b) == 1
    assert not ({j.outbox_id for j in a} & {j.outbox_id for j in b})


async def test_admin_api_tenant_security_and_dead_actions(api_client, tmp_path, session_factory):
    a = Venue(api_client, tmp_path)
    b = Venue(api_client, tmp_path)
    assert api_client.get(VK_URL).status_code == 401
    assert api_client.get(OUTBOX_URL + "/dead").status_code == 401
    configure(a)
    assert api_client.get(VK_URL, headers=b.headers()).json()["has_token"] is False
    async with session_factory() as session:
        integration = await session.get(VenueVKIntegration, a.venue_id)
        assert integration.encrypted_access_token != "test-vk-secret-never-return"
        assert integration.encryption_key_version == 1
    booking_id, _ = await _online(session_factory, a, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    claimed = await claim_for(session_factory, row.id)
    async with session_factory() as session, session.begin():
        await mark_dead(session, job=claimed, error="vk:5:permanent")
    for action in ("retry", "acknowledge"):
        assert (
            api_client.post(f"{OUTBOX_URL}/{row.id}/{action}", headers=b.headers()).status_code
            == 404
        )
        assert api_client.post(f"{OUTBOX_URL}/{row.id}/{action}").status_code == 401
    assert api_client.get(OUTBOX_URL + "/dead", headers=b.headers()).json()["jobs"] == []
    dead = api_client.get(OUTBOX_URL + "/dead", headers=a.headers()).json()
    assert dead["unacknowledged"] == 1
    assert len(dead["jobs"]) == 1
    result = api_client.post(f"{OUTBOX_URL}/{row.id}/acknowledge", headers=a.headers())
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "DEAD"
    assert api_client.get(OUTBOX_URL + "/dead", headers=a.headers()).json()["unacknowledged"] == 0
    result = api_client.post(f"{OUTBOX_URL}/{row.id}/retry", headers=a.headers())
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "RETRY"
    assert result.json()["acknowledged_at"] is None
    assert (await job_for(session_factory, booking_id)).expires_at == row.expires_at
    assert (
        api_client.post(f"{OUTBOX_URL}/{row.id}/acknowledge", headers=a.headers()).status_code
        == 404
    )


@pytest.mark.parametrize("state", ["expired", "canceled", "anonymized"])
async def test_skip_before_claim_does_not_increment_attempts(
    api_client, tmp_path, session_factory, state
):
    venue = Venue(api_client, tmp_path)
    configure(venue)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    if state != "expired":
        response = venue.client.post(
            f"/api/admin/v1/bookings/{booking_id}/cancel",
            headers=venue.headers(),
            json={"expected_version": 1, "reason": "GUEST_CANCELED"},
        )
        assert response.status_code == 200
    async with session_factory() as session, session.begin():
        now = await operation_now(session)
        if state == "expired":
            (await session.get(NotificationOutbox, row.id)).expires_at = now - timedelta(seconds=1)
        elif state == "anonymized":
            booking = await session.get(Booking, booking_id)
            booking.guest_name = booking.guest_phone_raw = booking.guest_phone_normalized = (
                booking.guest_comment
            ) = None
            booking.request_ip_hmac = booking.request_ip_hmac_expires_at = None
            booking.cancellation_note = None
            booking.public_idempotency_key = booking.public_request_hmac = None
            booking.admin_idempotency_key = booking.admin_request_hmac = None
            booking.anonymized_at = now
    assert await claim_for(session_factory, row.id) is None
    row = await job_for(session_factory, booking_id)
    assert row.status == "SKIPPED" and row.attempts == 0
    assert (
        row.skip_reason
        == {
            "expired": "EXPIRED",
            "canceled": "BOOKING_INACTIVE",
            "anonymized": "BOOKING_ANONYMIZED",
        }[state]
    )


async def test_status_count_ack_refetch_and_heartbeat(api_client, tmp_path, session_factory):
    from app.worker.heartbeat import heartbeat_age_seconds, record_heartbeat

    venue = Venue(api_client, tmp_path)
    configure(venue)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    job = await claim_for(session_factory, row.id)
    async with session_factory() as session, session.begin():
        await mark_dead(session, job=job, error="vk:5:permanent")
        now = await operation_now(session)
        await record_heartbeat(session, now=now)
        assert await heartbeat_age_seconds(session, now=now) == 0
    status_url = "/api/admin/v1/system/status"
    assert (
        api_client.get(status_url, headers=venue.headers()).json()["outbox_unacknowledged_dead"]
        == 1
    )
    assert api_client.get("/health/ops").json()["worker_heartbeat_age_seconds"] < 10
    assert (
        api_client.post(f"{OUTBOX_URL}/{row.id}/acknowledge", headers=venue.headers()).status_code
        == 200
    )
    assert (
        api_client.get(status_url, headers=venue.headers()).json()["outbox_unacknowledged_dead"]
        == 0
    )
    async with session_factory() as session, session.begin():
        await record_heartbeat(session, now=now - timedelta(seconds=120))
    ops = api_client.get("/health/ops").json()
    assert ops["worker_heartbeat_age_seconds"] >= 120 and ops["status"] == "degraded"
    assert "INTEGRATION_DISABLED" in ops["outbox_skipped_by_reason"]


async def test_vk_token_validation_never_echoes_input(api_client, tmp_path):
    venue = Venue(api_client, tmp_path)
    secret = "sensitive-token-" * 700
    result = api_client.put(VK_URL, headers=venue.headers(), json={"access_token": secret})
    assert result.status_code == 422
    assert secret not in result.text and "sensitive-token" not in result.text
    assert (
        api_client.put(VK_URL, headers=venue.headers(), json={"enabled": True}).status_code == 422
    )
    configure(venue)
    result = api_client.put(VK_URL, headers=venue.headers(), json={"enabled": False})
    assert result.status_code == 200 and result.json()["has_token"]
    assert api_client.put(VK_URL, json={"enabled": False}).status_code == 401


async def test_expired_manual_retry_skips_preserving_history(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    configure(venue)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    claimed = await claim_for(session_factory, row.id)
    async with session_factory() as session, session.begin():
        await mark_dead(session, job=claimed, error="vk:5:permanent")
        (await session.get(NotificationOutbox, row.id)).expires_at = await operation_now(
            session
        ) - timedelta(seconds=1)
    assert (
        api_client.post(f"{OUTBOX_URL}/{row.id}/retry", headers=venue.headers()).status_code == 200
    )
    assert await claim_for(session_factory, row.id) is None
    row = await job_for(session_factory, booking_id)
    assert row.status == "SKIPPED" and row.skip_reason == "EXPIRED"
    assert row.provider_dedup_id == claimed.provider_dedup_id
    assert (
        api_client.post(f"{OUTBOX_URL}/{row.id}/retry", headers=venue.headers()).status_code == 404
    )


async def test_throttle_counts_processing_reservations(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    configure(venue)
    ids = [
        (await _online(session_factory, venue, str(uuid.uuid4()), table_index=i))[0]
        for i in range(2)
    ]
    async with session_factory() as session, session.begin():
        jobs = await claim_jobs(
            session,
            now=await operation_now(session),
            lease_seconds=120,
            batch_size=10000,
            throttle_max_per_window=1,
        )
    assert sum(j.venue_id == venue.venue_id for j in jobs) == 1
    rows = [await job_for(session_factory, i) for i in ids]
    assert sorted(r.status for r in rows) == ["PROCESSING", "RETRY"]
    assert sorted(r.attempts for r in rows) == [0, 1]


async def test_atomic_walk_in_open_has_no_outbox(
    api_client, tmp_path, session_factory, monkeypatch
):
    from tests.integration.test_booking_lifecycle import clock_at

    venue = Venue(api_client, tmp_path)
    clock_at(monkeypatch, datetime.fromisoformat(venue.at(20)))
    result = venue.client.post(
        "/api/admin/v1/bookings",
        headers=venue.headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        json={
            "source": "WALK_IN",
            "open_immediately": True,
            "starts_at": venue.at(20),
            "ends_at": venue.at(22),
            "table_ids": venue.table_ids[:1],
            "party_size": 2,
            "guest_name": "walk in",
        },
    )
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "OPEN"
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.venue_id == venue.venue_id)
            )
            == 0
        )


async def test_database_constraints_and_session_cleanup(api_client, tmp_path, session_factory):
    from app.db.models import AdminSession
    from sqlalchemy import delete, update
    from sqlalchemy.exc import IntegrityError

    venue = Venue(api_client, tmp_path)
    other = Venue(api_client, tmp_path)
    configure(venue)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    async with session_factory() as session:
        now = await operation_now(session)
        invalid = [
            {"status": "SKIPPED"},
            {"status": "SKIPPED", "skipped_at": now, "skip_reason": "UNKNOWN"},
            {"status": "PENDING", "skipped_at": now, "skip_reason": "EXPIRED"},
            {"attempts": -1},
        ]
        for values in invalid:
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        update(NotificationOutbox)
                        .where(NotificationOutbox.id == row.id)
                        .values(**values)
                    )
        other_session = await session.scalar(
            select(AdminSession.id).where(AdminSession.venue_id == other.venue_id)
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    update(NotificationOutbox)
                    .where(NotificationOutbox.id == row.id)
                    .values(acknowledged_by_session_id=other_session)
                )
        await session.rollback()
    claimed = await claim_for(session_factory, row.id)
    async with session_factory() as session, session.begin():
        await mark_dead(session, job=claimed, error="vk:5:permanent")
    assert (
        api_client.post(f"{OUTBOX_URL}/{row.id}/acknowledge", headers=venue.headers()).status_code
        == 200
    )
    async with session_factory() as session, session.begin():
        acknowledged = await session.get(NotificationOutbox, row.id)
        session_id = acknowledged.acknowledged_by_session_id
        assert session_id is not None
        await session.execute(delete(AdminSession).where(AdminSession.id == session_id))
    final = await job_for(session_factory, booking_id)
    assert final.venue_id == venue.venue_id and final.acknowledged_at is not None
    assert final.acknowledged_by_session_id is None


async def test_disable_between_prepare_reads_is_skipped(
    api_client, tmp_path, session_factory, monkeypatch
):
    from app.services.vk_integration import VKIntegrationUnavailableError

    venue = Venue(api_client, tmp_path)
    configure(venue)
    booking_id, _ = await _online(session_factory, venue, str(uuid.uuid4()))
    row = await job_for(session_factory, booking_id)
    job = await claim_for(session_factory, row.id)

    async def disabled(*args, **kwargs):
        raise VKIntegrationUnavailableError(venue.venue_id, "disabled")

    monkeypatch.setattr("app.worker.load_integration", disabled)

    def no_http(_request):
        pytest.fail("disabled integration must not call VK")

    worker = VKOutboxWorker(
        make_settings(),
        client=VKClient(api_version="5.199", transport=httpx.MockTransport(no_http)),
    )
    worker._session_factory = session_factory
    try:
        await worker._deliver(job)
        row = await job_for(session_factory, booking_id)
        assert row.status == "SKIPPED" and row.skip_reason == "INTEGRATION_DISABLED"
    finally:
        await worker._engine.dispose()


async def test_dead_count_is_not_limited_to_first_page(api_client, tmp_path, session_factory):
    venue = Venue(api_client, tmp_path)
    async with session_factory() as session, session.begin():
        now = await operation_now(session)
        session.add_all(
            [
                NotificationOutbox(
                    venue_id=venue.venue_id,
                    type="ONLINE_BOOKING",
                    dedup_key=f"pagination:{venue.venue_id}:{i}",
                    payload={"booking_id": i, "kind": "BOOKING_CREATED", "formatter_version": 1},
                    status="DEAD",
                    next_attempt_at=now,
                    expires_at=now,
                    attempts=8,
                )
                for i in range(105)
            ]
        )
    first = api_client.get(OUTBOX_URL + "/dead", headers=venue.headers()).json()
    assert len(first["jobs"]) == 100 and first["unacknowledged"] == 105
    second = api_client.get(
        OUTBOX_URL + f"/dead?before_id={first['next_cursor']}", headers=venue.headers()
    ).json()
    assert len(second["jobs"]) == 5 and second["unacknowledged"] == 105
    assert second["next_cursor"] is None
    assert not ({r["id"] for r in first["jobs"]} & {r["id"] for r in second["jobs"]})
    assert (
        api_client.get("/api/admin/v1/system/status", headers=venue.headers()).json()[
            "outbox_unacknowledged_dead"
        ]
        == 105
    )


async def test_missing_heartbeat_is_degraded(api_client, session_factory):
    from app.db.models import WorkerHeartbeatRow
    from sqlalchemy import delete

    async with session_factory() as session, session.begin():
        await session.execute(delete(WorkerHeartbeatRow))
    response = api_client.get("/health/ops").json()
    assert response["worker_heartbeat_age_seconds"] is None
    assert response["status"] == "degraded"
