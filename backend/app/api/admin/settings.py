"""Venue settings endpoints (PROJECT-SPEC §35).

Every operation is scoped to the venue resolved from the session cookie. There
is no ``venue_id`` parameter, so one tenant cannot address another.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin.dependencies import AuthContextDep, SessionDep, require_trusted_origin
from app.api.admin.schemas import SettingsUpdate, VenueSummary
from app.services.venues import update_venue_settings

router = APIRouter(prefix="/settings", tags=["admin-settings"])


@router.get("", response_model=VenueSummary)
async def get_settings(context: AuthContextDep) -> VenueSummary:
    """Return this venue's settings."""
    return VenueSummary.from_model(context.venue)


@router.patch(
    "",
    response_model=VenueSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def patch_settings(
    payload: SettingsUpdate,
    context: AuthContextDep,
    session: SessionDep,
) -> VenueSummary:
    """Update this venue's editable settings.

    Only fields the client sent are applied (PATCH semantics); an unknown field
    such as ``venue_id`` is dropped by the schema and can never retarget the
    mutation (PROJECT-SPEC §7.1).
    """
    changes = payload.model_dump(exclude_unset=True)
    venue = await update_venue_settings(session, context.venue_id, changes=changes)
    await session.commit()
    return VenueSummary.from_model(venue)
