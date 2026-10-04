"""Authenticated admin identity (PROJECT-SPEC §35)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin.dependencies import AuthContextDep
from app.api.admin.schemas import AdminSummary, MeResponse, VenueSummary

router = APIRouter(tags=["admin-account"])


@router.get("/me", response_model=MeResponse)
async def me(context: AuthContextDep) -> MeResponse:
    """Return the authenticated admin and its venue.

    The venue is derived from the session, never from a client-supplied id.
    """
    return MeResponse(
        admin=AdminSummary.from_model(context.admin),
        venue=VenueSummary.from_model(context.venue),
    )
