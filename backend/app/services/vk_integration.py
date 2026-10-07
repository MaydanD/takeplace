"""VK integration configuration service (PROJECT-SPEC §6.12, §38.5).

Reads the per-venue ``venue_vk_integrations`` row and decrypts its access token
for the worker. The token never leaves this module except as an in-memory string
handed to the VK client; it is never logged, never returned to the frontend and
never placed in an exception message (§38.5).

Tenant isolation: the row is keyed by ``venue_id`` (its primary key), and the
worker passes the job's own ``venue_id``, so a job for venue A can never use the
credentials or destination of venue B (§7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Venue, VenueVKIntegration
from app.integrations.vk.crypto import VKTokenCipher


@dataclass(frozen=True, slots=True)
class VKIntegrationConfig:
    """A resolved, ready-to-use VK destination for one venue."""

    venue_id: int
    community_id: int
    peer_id: int
    access_token: str


class VKIntegrationUnavailableError(RuntimeError):
    """The venue has no usable VK configuration (disabled or incomplete).

    The message names only the venue id and a machine-readable reason; it never
    contains the token or any partial credential.
    """

    def __init__(self, venue_id: int, reason: str) -> None:
        super().__init__(f"VK integration unavailable for venue {venue_id}: {reason}")
        self.venue_id = venue_id
        self.reason = reason


async def load_integration(
    session: AsyncSession, *, venue_id: int, cipher: VKTokenCipher
) -> VKIntegrationConfig:
    """Load and decrypt the venue's VK configuration (§38.5).

    Raises :class:`VKIntegrationUnavailableError` when the integration is missing,
    disabled or incomplete. This is a *permanent* condition for the job: retrying
    cannot fix a disabled integration, so the worker records it as terminal.
    """
    row = (
        await session.execute(
            select(VenueVKIntegration).where(VenueVKIntegration.venue_id == venue_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise VKIntegrationUnavailableError(venue_id, "not configured")
    if not row.enabled:
        raise VKIntegrationUnavailableError(venue_id, "disabled")
    if (
        row.community_id is None
        or row.peer_id is None
        or row.encrypted_access_token is None
        or row.encryption_key_version is None
    ):
        # The DB CHECK already forbids this for an enabled row; defensive only.
        raise VKIntegrationUnavailableError(venue_id, "incomplete configuration")
    token = cipher.decrypt(row.encrypted_access_token, row.encryption_key_version)
    return VKIntegrationConfig(
        venue_id=venue_id,
        community_id=row.community_id,
        peer_id=row.peer_id,
        access_token=token,
    )


# --- admin configuration (§35, §38.5) ---------------------------------------


@dataclass(frozen=True, slots=True)
class VKIntegrationSummary:
    """A frontend-safe view of the venue's VK integration (§38.5).

    Deliberately carries only ``has_token`` plus non-secret fields: the token and
    its ciphertext are never returned to the frontend.
    """

    enabled: bool
    community_id: int | None
    peer_id: int | None
    has_token: bool


async def get_integration_summary(session: AsyncSession, *, venue_id: int) -> VKIntegrationSummary:
    """Return the venue's VK integration as a secret-free summary (§38.5)."""
    row = (
        await session.execute(
            select(VenueVKIntegration).where(VenueVKIntegration.venue_id == venue_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return VKIntegrationSummary(enabled=False, community_id=None, peer_id=None, has_token=False)
    return VKIntegrationSummary(
        enabled=row.enabled,
        community_id=row.community_id,
        peer_id=row.peer_id,
        has_token=row.encrypted_access_token is not None,
    )


async def update_integration(
    session: AsyncSession,
    *,
    venue_id: int,
    enabled: bool,
    community_id: int | None,
    peer_id: int | None,
    access_token: str | None,
    cipher: VKTokenCipher,
    now: datetime,
) -> VKIntegrationSummary:
    """Upsert the venue's VK integration, encrypting a newly supplied token (§38.5).

    ``access_token`` is written only when supplied: a ``None`` token leaves the
    stored ciphertext untouched, so an operator can toggle ``enabled`` or change
    the peer without re-entering the secret. Enabling requires a complete
    configuration (mirroring the DB CHECK) so the worker never sees a half-set row.
    """
    # Serialize concurrent first-time PUTs as well as subsequent token rotations.
    await session.execute(select(Venue.id).where(Venue.id == venue_id).with_for_update())
    row = (
        await session.execute(
            select(VenueVKIntegration).where(VenueVKIntegration.venue_id == venue_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = VenueVKIntegration(
            venue_id=venue_id,
            enabled=False,
            community_id=None,
            peer_id=None,
            encrypted_access_token=None,
            encryption_key_version=None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)

    if access_token is not None:
        ciphertext, version = cipher.encrypt(access_token)
        row.encrypted_access_token = ciphertext
        row.encryption_key_version = version

    row.enabled = enabled
    if community_id is not None:
        row.community_id = community_id
    if peer_id is not None:
        row.peer_id = peer_id
    row.updated_at = now

    if row.enabled and (
        row.community_id is None
        or row.peer_id is None
        or row.encrypted_access_token is None
        or row.encryption_key_version is None
    ):
        raise VKIntegrationUnavailableError(venue_id, "enabling requires a complete configuration")
    await session.flush()
    return VKIntegrationSummary(
        enabled=row.enabled,
        community_id=row.community_id,
        peer_id=row.peer_id,
        has_token=row.encrypted_access_token is not None,
    )
