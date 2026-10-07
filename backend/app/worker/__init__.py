"""VK outbox worker process (PROJECT-SPEC §3.5, §38.3).

A **separate process** (``python -m app.worker``), not a background task inside
the FastAPI app: the API must never depend on VK availability, and the worker must
be restartable independently (§3.5, §38.3).

Design points:

* it owns its own engine/session factory, exactly like the API but with its own
  lifecycle;
* it claims jobs with ``FOR UPDATE SKIP LOCKED`` and never holds a transaction
  across the VK HTTP call (§38.3);
* it survives PostgreSQL outages (reconnect via the pool + retry loop), VK outages
  (RETRY with backoff), rate limits (retryable), timeouts (retryable), terminal VK
  errors (DEAD) and its own crash mid-delivery (expired lease -> reclaim);
* it terminates cleanly on SIGTERM/SIGINT, finishing the in-flight job's
  bookkeeping transaction before exiting;
* it publishes a heartbeat so ``/health/ops`` can flag a dead worker (§47).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import NotificationOutbox  # noqa: F401 - import registers metadata
from app.db.session import build_engine
from app.db.time import operation_now
from app.integrations.vk import (
    VKClient,
    VKDeliveryError,
    VKTokenCipher,
    format_booking_notification,
)
from app.integrations.vk.crypto import VKEncryptionError
from app.services.maintenance import run_maintenance
from app.services.outbox_worker import (
    ClaimedJob,
    booking_id_of,
    build_notification_data,
    claim_jobs,
    decide_skip,
    integration_skip_reason,
    kind_of,
    mark_dead,
    mark_retry,
    mark_sent,
    mark_skipped,
    reload_booking,
)
from app.services.vk_integration import (
    VKIntegrationUnavailableError,
    load_integration,
)
from app.settings import Settings, get_settings
from app.worker.heartbeat import WorkerHeartbeat, record_heartbeat

logger = structlog.get_logger("takeplace.worker")

#: Bounded wait after a database error before the poll loop retries.
_DB_RETRY_SECONDS = 2.0


class VKOutboxWorker:
    """The delivery loop. One instance per process."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: VKClient | None = None,
        heartbeat: WorkerHeartbeat | None = None,
    ) -> None:
        self._settings = settings
        self._engine = build_engine(settings)
        self._session_factory = _make_factory(self._engine)
        self._cipher = VKTokenCipher(settings.vk_key_ring)
        self._client = client or VKClient(
            api_version=settings.vk_api_version,
            timeout_seconds=settings.vk_http_timeout_seconds,
        )
        self._heartbeat = heartbeat or WorkerHeartbeat()
        self._stop = asyncio.Event()

    # --- lifecycle ----------------------------------------------------------

    def request_stop(self) -> None:
        """Signal the loop to finish the current cycle and exit (SIGTERM/SIGINT)."""
        self._stop.set()

    async def run(self) -> None:
        """Poll until stopped. Never raises out of the loop on a transient error."""
        logger.info("worker_startup", poll_interval=self._settings.vk_worker_poll_interval_seconds)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        # Retention/anonymization + invariant audit run in their own task with
        # their own session, so a slow maintenance pass never delays delivery.
        maintenance_task = asyncio.create_task(self._maintenance_loop())
        try:
            while not self._stop.is_set():
                processed = await self._run_once()
                if processed == 0:
                    # Idle: wait for the poll interval, but wake immediately on stop.
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self._settings.vk_worker_poll_interval_seconds,
                        )
        finally:
            maintenance_task.cancel()
            heartbeat_task.cancel()
            for task in (maintenance_task, heartbeat_task):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self._engine.dispose()
            logger.info("worker_shutdown")

    async def _run_once(self) -> int:
        """Claim and deliver one batch; return how many jobs were handled."""
        try:
            jobs = await self._claim()
        except Exception as exc:  # noqa: BLE001 - a DB outage must not kill the worker
            logger.warning("worker_claim_failed", error=type(exc).__name__)
            await asyncio.sleep(_DB_RETRY_SECONDS)
            return 0
        await asyncio.gather(*(self._deliver(job) for job in jobs))
        return len(jobs)

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            await self._beat()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.vk_worker_heartbeat_seconds
                )

    async def _claim(self) -> list[ClaimedJob]:
        async with self._session_factory() as session, session.begin():
            now = await operation_now(session)
            return await claim_jobs(
                session,
                now=now,
                lease_seconds=self._settings.vk_worker_lease_seconds,
                batch_size=self._settings.vk_worker_batch_size,
                throttle_window_seconds=self._settings.vk_worker_throttle_window_seconds,
                throttle_max_per_window=self._settings.vk_worker_throttle_max_per_window,
            )

    async def _maintenance_loop(self) -> None:
        """Periodic retention/anonymization + invariant audit (Stage 13)."""
        while not self._stop.is_set():
            await self._maintenance_once()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.maintenance_interval_seconds
                )

    async def _maintenance_once(self) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                now = await operation_now(session)
                report = await run_maintenance(session, now=now, settings=self._settings)
            logger.info(
                "maintenance_cycle",
                anonymized=report.retention.anonymized_bookings,
                cleared_ip_hmacs=report.retention.cleared_ip_hmacs,
                deleted_sessions=report.retention.deleted_sessions,
                deleted_outbox=report.retention.deleted_outbox_rows,
                audit_corruptions=report.audit.total_corruptions,
                audit_alerts=report.audit.alerts,
            )
            if report.audit.total_corruptions:
                # Detect-and-alert only: production never auto-fixes invariants (§60).
                logger.error("invariant_audit_corruption", counts=report.audit.corruptions)
        except Exception as exc:  # noqa: BLE001 - maintenance must never stop delivery
            logger.warning("maintenance_failed", error=type(exc).__name__)

    async def _beat(self) -> None:
        """Publish the durable worker heartbeat (best-effort, §47)."""
        self._heartbeat.beat()
        try:
            async with self._session_factory() as session, session.begin():
                now = await operation_now(session)
                await record_heartbeat(session, now=now)
        except Exception as exc:  # noqa: BLE001 - a heartbeat failure must not stop work
            logger.warning("worker_heartbeat_failed", error=type(exc).__name__)

    # --- delivery -----------------------------------------------------------

    async def _deliver(self, job: ClaimedJob) -> None:
        """Run the §38.3 lease algorithm for a single claimed job."""
        try:
            async with self._session_factory() as session, session.begin():
                now = await operation_now(session)
                current = await session.scalar(
                    select(NotificationOutbox.id).where(
                        NotificationOutbox.id == job.outbox_id,
                        NotificationOutbox.status == "PROCESSING",
                        NotificationOutbox.attempts == job.attempts,
                        NotificationOutbox.locked_until > now,
                    )
                )
                if current is None:
                    return
                booking_id = booking_id_of(job.payload)
                booking = (
                    await reload_booking(session, venue_id=job.venue_id, booking_id=booking_id)
                    if booking_id is not None
                    else None
                )
                skip = decide_skip(now=now, expires_at=job.expires_at, booking=booking)
                if skip is not None:
                    # Stale/inactive: record SKIPPED and never call VK (§38.3 step 3).
                    await mark_skipped(session, job=job, now=now, reason=skip.reason)
                    logger.info("worker_job_skipped", outbox_id=job.outbox_id, reason=skip.reason)
                    return
                integration_reason = await integration_skip_reason(session, venue_id=job.venue_id)
                if integration_reason is not None:
                    await mark_skipped(session, job=job, now=now, reason=integration_reason)
                    logger.info(
                        "worker_job_skipped", outbox_id=job.outbox_id, reason=integration_reason
                    )
                    return
                if booking is None:
                    # Defensive: decide_skip already handled the missing-booking case.
                    await mark_dead(session, job=job, error="booking_missing")
                    logger.warning("worker_booking_missing", outbox_id=job.outbox_id)
                    return
                if kind_of(job.payload) != "BOOKING_CREATED":
                    await mark_dead(session, job=job, error="unknown notification kind")
                    logger.warning("worker_unknown_kind", outbox_id=job.outbox_id)
                    return
                # Everything the HTTP call needs, loaded while the booking is fresh.
                integration = await load_integration(
                    session, venue_id=job.venue_id, cipher=self._cipher
                )
                data = await build_notification_data(session, booking=booking)
                message = format_booking_notification(data)
                # Renew after preparation, fencing against a lease reclaimed while
                # the booking/configuration queries were in progress.
                fresh_now = await operation_now(session)
                skip = decide_skip(now=fresh_now, expires_at=job.expires_at, booking=booking)
                if skip:
                    await mark_skipped(session, job=job, now=fresh_now, reason=skip.reason)
                    return
                renewed = await session.scalar(
                    update(NotificationOutbox)
                    .where(
                        NotificationOutbox.id == job.outbox_id,
                        NotificationOutbox.status == "PROCESSING",
                        NotificationOutbox.attempts == job.attempts,
                    )
                    .values(
                        locked_until=fresh_now
                        + timedelta(seconds=self._settings.vk_worker_lease_seconds)
                    )
                    .returning(NotificationOutbox.id)
                )
                if renewed is None:
                    return
                attempts = job.attempts
        except VKIntegrationUnavailableError as exc:
            if exc.reason in ("disabled", "not configured"):
                # Configuration may change between the two reads in preparation.
                try:
                    async with self._session_factory() as session, session.begin():
                        await mark_skipped(
                            session,
                            job=job,
                            now=await operation_now(session),
                            reason="INTEGRATION_DISABLED",
                        )
                except Exception as skip_exc:
                    logger.warning(
                        "worker_skip_record_failed",
                        outbox_id=job.outbox_id,
                        error=type(skip_exc).__name__,
                    )
                return
            await self._terminal(job, error=f"integration:{exc.reason}")
            return
        except VKEncryptionError:
            # A missing/rotated key is a configuration defect, not a transient one.
            await self._terminal(job, error="encryption_error")
            return
        except Exception as exc:  # noqa: BLE001 - DB error before the HTTP call: retry
            logger.warning(
                "worker_prepare_failed", outbox_id=job.outbox_id, error=type(exc).__name__
            )
            await self._record_retry(job, error=f"prepare:{type(exc).__name__}")
            return

        # The HTTP call happens with NO open database transaction (§38.3 step 6).
        try:
            await asyncio.wait_for(
                self._client.send_message(
                    access_token=integration.access_token,
                    peer_id=integration.peer_id,
                    message=message,
                    provider_dedup_id=job.provider_dedup_id,
                ),
                timeout=self._settings.vk_http_timeout_seconds,
            )
        except VKDeliveryError as exc:
            if exc.retryable:
                await self._record_retry(job, error=_safe_error(exc))
            else:
                await self._terminal(job, error=_safe_error(exc))
            return
        except Exception as exc:  # noqa: BLE001 - unexpected client failure is retryable
            await self._record_retry(job, error=f"send:{type(exc).__name__}")
            return

        # Confirmed success: a *separate* transaction records SENT (§38.3 step 7).
        try:
            async with self._session_factory() as session, session.begin():
                now = await operation_now(session)
                await mark_sent(session, job=job, now=now)
            logger.info("worker_job_sent", outbox_id=job.outbox_id, attempts=attempts)
        except Exception as exc:  # noqa: BLE001
            # Crash window: VK accepted the message but we could not persist SENT.
            # The expired lease will reclaim the job and resend; VK deduplicates by
            # ``random_id`` derived from the stable provider_dedup_id (§38.4).
            logger.warning(
                "worker_sent_record_failed",
                outbox_id=job.outbox_id,
                error=type(exc).__name__,
            )

    async def _record_retry(self, job: ClaimedJob, *, error: str) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                now = await operation_now(session)
                if job.attempts >= self._settings.vk_worker_max_attempts:
                    await mark_dead(session, job=job, error=error)
                    logger.warning("worker_job_dead", outbox_id=job.outbox_id, error=error)
                else:
                    await mark_retry(
                        session,
                        job=job,
                        now=now,
                        error=error,
                        base_seconds=self._settings.vk_worker_retry_base_seconds,
                        max_seconds=self._settings.vk_worker_retry_max_seconds,
                    )
                    logger.info("worker_job_retry", outbox_id=job.outbox_id, attempts=job.attempts)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "worker_retry_record_failed", outbox_id=job.outbox_id, error=type(exc).__name__
            )

    async def _terminal(self, job: ClaimedJob, *, error: str) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await mark_dead(session, job=job, error=error)
            logger.warning("worker_job_dead", outbox_id=job.outbox_id, error=error)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "worker_dead_record_failed", outbox_id=job.outbox_id, error=type(exc).__name__
            )


def _safe_error(exc: VKDeliveryError) -> str:
    """A loggable, secret-free error string for a failed delivery (§39.7)."""
    if exc.vk_error_code is not None:
        return f"vk:{exc.vk_error_code}:{exc.error_class.value.lower()}"
    return f"vk:{exc.error_class.value.lower()}"


def _make_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _install_signal_handlers(worker: VKOutboxWorker) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # Windows has no SIGTERM loop handler
            loop.add_signal_handler(sig, worker.request_stop)


async def run_worker(settings: Settings | None = None) -> None:
    """Build and run the worker until a stop signal is received."""
    resolved = settings or get_settings()
    worker = VKOutboxWorker(resolved)
    _install_signal_handlers(worker)
    await worker.run()


def main() -> None:
    """Process entry point: ``python -m app.worker``."""
    from app.logging_config import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)
    if not settings.vk_worker_enabled:
        logger.info("worker_disabled")
        return
    with contextlib.suppress(KeyboardInterrupt):  # operator interrupt
        asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
