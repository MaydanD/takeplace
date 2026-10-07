"""Outbox administration endpoints (PROJECT-SPEC §35, §6.11, §38.3).

Scoped to the venue resolved from the session cookie (§7.1). Operators can list
DEAD jobs, requeue one for a manual retry (without extending its TTL) and
acknowledge a known problem so it stops degrading ``/health/ops``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.admin.dependencies import (
    AuthContextDep,
    SessionDep,
    require_trusted_origin,
)
from app.api.admin.schemas import OutboxDeadJob, OutboxDeadList
from app.api.errors import ApiError
from app.db.time import operation_now
from app.services.outbox_worker import (
    acknowledge,
    list_dead_jobs,
    manual_retry,
    unacknowledged_dead_count,
)

router = APIRouter(prefix="/outbox", tags=["admin-outbox"])


@router.get("/dead", response_model=OutboxDeadList)
async def list_dead(
    context: AuthContextDep,
    session: SessionDep,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> OutboxDeadList:
    """List this venue's DEAD outbox jobs plus the unacknowledged count."""
    jobs = await list_dead_jobs(
        session, venue_id=context.venue_id, limit=limit + 1, before_id=before_id
    )
    unacknowledged = await unacknowledged_dead_count(session, venue_id=context.venue_id)
    return OutboxDeadList(
        jobs=[OutboxDeadJob.from_summary(job) for job in jobs[:limit]],
        next_cursor=jobs[limit - 1].id if len(jobs) > limit else None,
        unacknowledged=unacknowledged,
    )


@router.post(
    "/{outbox_id}/retry",
    response_model=OutboxDeadJob,
    dependencies=[Depends(require_trusted_origin)],
)
async def retry_dead(
    outbox_id: int,
    context: AuthContextDep,
    session: SessionDep,
) -> OutboxDeadJob:
    """Requeue a DEAD/RETRY job for immediate retry without extending its TTL (§38.3)."""
    now = await operation_now(session)
    summary = await manual_retry(session, venue_id=context.venue_id, outbox_id=outbox_id, now=now)
    if summary is None:
        raise ApiError(404, "OUTBOX_NOT_FOUND", "no retryable outbox job with that id")
    await session.commit()
    return OutboxDeadJob.from_summary(summary)


@router.post(
    "/{outbox_id}/acknowledge",
    response_model=OutboxDeadJob,
    dependencies=[Depends(require_trusted_origin)],
)
async def acknowledge_dead(
    outbox_id: int,
    context: AuthContextDep,
    session: SessionDep,
) -> OutboxDeadJob:
    """Acknowledge a DEAD job so it stops degrading ``/health/ops`` (§38.3)."""
    now = await operation_now(session)
    summary = await acknowledge(
        session,
        venue_id=context.venue_id,
        outbox_id=outbox_id,
        now=now,
        admin_session_id=context.session.id,
    )
    if summary is None:
        raise ApiError(404, "OUTBOX_NOT_FOUND", "no dead outbox job with that id")
    await session.commit()
    return OutboxDeadJob.from_summary(summary)
