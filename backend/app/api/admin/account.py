"""Authenticated admin identity (PROJECT-SPEC §35)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.admin.dependencies import AuthContextDep
from app.api.admin.schemas import AdminSummary, MeResponse, VenueSummary
from app.services.public_rate_limit import public_abuse_alert_count

router = APIRouter(tags=["admin-account"])


class SystemStatusResponse(BaseModel):
    online_abuse_alert: bool


@router.get("/system/status", response_model=SystemStatusResponse)
async def system_status(context: AuthContextDep) -> SystemStatusResponse:
    return SystemStatusResponse(online_abuse_alert=bool(public_abuse_alert_count(context.venue.id)))


@router.get("/me", response_model=MeResponse)
async def me(context: AuthContextDep) -> MeResponse:
    """Return the authenticated admin and its venue.

    The venue is derived from the session, never from a client-supplied id.
    """
    return MeResponse(
        admin=AdminSummary.from_model(context.admin),
        venue=VenueSummary.from_model(context.venue),
    )
