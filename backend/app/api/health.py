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
from app.services.timezone_capability import get_monitor
from app.settings import Settings, get_settings

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]


class OpsResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    environment: str
    # Signals that later stages will populate (outbox, worker heartbeat, tz check).
    # They are reported as explicit placeholders rather than silently omitted.
    outbox_unacknowledged_dead: int = 0
    timezone_capability: Literal["ok", "unsupported"] = "ok"


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

    degraded = not database_ok or not timezone_ok
    return OpsResponse(
        status="ok" if not degraded else "degraded",
        database="ok" if database_ok else "unavailable",
        environment=settings.env.value,
        timezone_capability="ok" if timezone_ok else "unsupported",
    )
