"""Health endpoints (PROJECT-SPEC §47).

The three endpoints have distinct audiences:

* ``/health/live``  — the process is alive. Used by container liveness probes.
* ``/health/ready`` — the app can serve requests and can talk to PostgreSQL.
  Used by container readiness probes and load balancers.
* ``/health/ops``   — operational health for external monitoring. It may report
  ``degraded`` for non-fatal conditions and must never be used as a container
  liveness probe, so that a VK/outbox problem cannot restart-loop the app.

None of them expose PII or secrets.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.db.time import operation_now
from app.services.outbox_worker import collect_metrics
from app.services.public_rate_limit import public_abuse_alert_count
from app.services.timezone_capability import get_monitor
from app.settings import Settings, get_settings
from app.worker.heartbeat import heartbeat_age_seconds

router = APIRouter(tags=["health"])

#: A worker heartbeat older than this degrades ``/health/ops`` (§47). It is a
#: multiple of the configured heartbeat cadence so a single slow cycle does not
#: flap the status.
_HEARTBEAT_STALE_MULTIPLIER = 3


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]


class OpsResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    environment: str
    # Outbox/worker signals (§47, §49). ``outbox_unacknowledged_dead`` and a stale
    # worker heartbeat degrade ops without ever being a liveness probe.
    outbox_unacknowledged_dead: int = 0
    outbox_total_dead: int = 0
    outbox_skipped_by_reason: dict[str, int] = {}
    outbox_pending: int = 0
    outbox_retry: int = 0
    outbox_processing: int = 0
    outbox_oldest_pending_age_seconds: float | None = None
    worker_heartbeat_age_seconds: float | None = None
    timezone_capability: Literal["ok", "unsupported"] = "ok"
    online_abuse_alerts: int = 0


@router.get("/health/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    """Process liveness: no dependency checks."""
    return LivenessResponse(status="ok")


async def _database_ready(session: AsyncSession) -> bool:
    """Verify PostgreSQL is reachable and usable, not just TCP-reachable."""
    try:
        result = await session.execute(text("SELECT 1"))
        return int(result.scalar_one()) == 1
    except Exception:
        return False


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def ready(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReadinessResponse:
    """Readiness: the app can serve requests and sees a working database."""
    if await _database_ready(session):
        return ReadinessResponse(status="ok", database="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="unavailable", database="unavailable")


@router.get("/health/ops", response_model=OpsResponse)
async def ops(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OpsResponse:
    """Operational health for external monitoring.

    Always returns 200 so monitoring can distinguish "degraded" from "down".
    """
    database_ok = await _database_ready(session)

    # The rolling timezone capability check (§47): unsupported zones degrade ops
    # without ever being used as a container liveness probe.
    monitor = get_monitor()
    capability = monitor.snapshot() if monitor is not None else None
    timezone_ok = capability is None or not capability.checked or capability.status == "ok"

    abuse_alerts = public_abuse_alert_count()

    # Outbox + worker signals (§47, §49). Only meaningful with a working database.
    unack_dead = 0
    pending = retry = processing = total_dead = 0
    skipped: dict[str, int] = {}
    oldest_age: float | None = None
    heartbeat_age: float | None = None
    if database_ok:
        now = await operation_now(session)
        metrics = await collect_metrics(session, now=now)
        unack_dead = metrics.unacknowledged_dead
        total_dead = metrics.total_dead
        skipped = metrics.skipped_by_reason
        pending = metrics.pending
        retry = metrics.retry
        processing = metrics.processing
        oldest_age = metrics.oldest_pending_age_seconds
        heartbeat_age = await heartbeat_age_seconds(session, now=now)

    heartbeat_stale = settings.vk_worker_enabled and (
        heartbeat_age is None
        or heartbeat_age > settings.vk_worker_heartbeat_seconds * _HEARTBEAT_STALE_MULTIPLIER
    )
    degraded = (
        not database_ok
        or not timezone_ok
        or abuse_alerts > 0
        or unack_dead > 0
        or heartbeat_stale
        or (oldest_age is not None and oldest_age > settings.vk_notification_max_age_seconds)
    )
    return OpsResponse(
        status="ok" if not degraded else "degraded",
        database="ok" if database_ok else "unavailable",
        environment=settings.env.value,
        outbox_unacknowledged_dead=unack_dead,
        outbox_pending=pending,
        outbox_total_dead=total_dead,
        outbox_skipped_by_reason=skipped,
        outbox_retry=retry,
        outbox_processing=processing,
        outbox_oldest_pending_age_seconds=oldest_age,
        worker_heartbeat_age_seconds=heartbeat_age,
        timezone_capability="ok" if timezone_ok else "unsupported",
        online_abuse_alerts=abuse_alerts,
    )
