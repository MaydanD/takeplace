"""VK integration configuration endpoints (PROJECT-SPEC §35, §38.5).

Scoped to the venue resolved from the session cookie, so one tenant can never
read or change another's integration (§7.1). The access token is write-only: it
is encrypted at rest and the API only ever returns ``has_token`` (§38.5).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin.dependencies import (
    AuthContextDep,
    SessionDep,
    SettingsDep,
    require_trusted_origin,
)
from app.api.admin.schemas import VKIntegrationSummary, VKIntegrationUpdate
from app.api.errors import ApiError
from app.db.time import operation_now
from app.integrations.vk.crypto import VKEncryptionError, VKTokenCipher
from app.services.vk_integration import (
    VKIntegrationUnavailableError,
    get_integration_summary,
    update_integration,
)

router = APIRouter(prefix="/integrations/vk", tags=["admin-integrations"])


@router.get("", response_model=VKIntegrationSummary)
async def get_vk_integration(
    context: AuthContextDep,
    session: SessionDep,
) -> VKIntegrationSummary:
    """Return this venue's VK integration as a secret-free summary."""
    summary = await get_integration_summary(session, venue_id=context.venue_id)
    return VKIntegrationSummary.from_summary(summary)


@router.put(
    "",
    response_model=VKIntegrationSummary,
    dependencies=[Depends(require_trusted_origin)],
)
async def put_vk_integration(
    payload: VKIntegrationUpdate,
    context: AuthContextDep,
    session: SessionDep,
    settings: SettingsDep,
) -> VKIntegrationSummary:
    """Upsert this venue's VK integration, encrypting any supplied token (§38.5)."""
    now = await operation_now(session)
    try:
        cipher = VKTokenCipher(settings.vk_key_ring)
        summary = await update_integration(
            session,
            venue_id=context.venue_id,
            enabled=payload.enabled,
            community_id=payload.community_id,
            peer_id=payload.peer_id,
            access_token=payload.access_token.get_secret_value()
            if payload.access_token is not None
            else None,
            cipher=cipher,
            now=now,
        )
    except VKIntegrationUnavailableError as exc:
        # Map the service's reason to a stable, secret-free API error.
        raise ApiError(422, "VK_INTEGRATION_INCOMPLETE", str(exc)) from exc
    except VKEncryptionError as exc:
        raise ApiError(
            503, "VK_ENCRYPTION_UNAVAILABLE", "VK encryption configuration unavailable"
        ) from exc
    await session.commit()
    return VKIntegrationSummary.from_summary(summary)
