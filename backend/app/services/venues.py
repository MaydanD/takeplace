"""Venue lifecycle services (PROJECT-SPEC §6.1, §53).

These run DDL-free DML as the migrator role during CLI operations (the CLI has
no HTTP context). The API never creates venues.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAccount, AdminSession, Venue
from app.db.time import operation_now
from app.domain.timezone import (
    UnknownTimezoneError,
    UnsupportedTimezoneError,
    assert_supported_timezone,
)
from app.domain.venues import validate_slug
from app.services.errors import (
    AdminNotFoundError,
    LoginTakenError,
    SlugTakenError,
    UnsupportedTimezoneServiceError,
    VenueNotFoundError,
)
from app.services.halls import create_default_hall
from app.services.schedule import seed_default_schedule


async def create_venue(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    timezone: str,
    login: str,
    password_hash: str,
    address: str | None = None,
    phone: str | None = None,
    online_booking_enabled: bool = False,
) -> tuple[Venue, AdminAccount]:
    """Create a venue and its single admin account.

    ``online_booking_enabled`` always defaults to false for a new venue: online
    booking is enabled explicitly after setup (§6.1).
    """
    validate_slug(slug)
    try:
        assert_supported_timezone(timezone)
    except (UnknownTimezoneError, UnsupportedTimezoneError) as exc:
        raise UnsupportedTimezoneServiceError(str(exc)) from exc

    now = await operation_now(session)
    venue = Venue(
        slug=slug,
        name=name,
        address=address,
        phone=phone,
        timezone=timezone,
        is_active=True,
        online_booking_enabled=online_booking_enabled,
        created_at=now,
        updated_at=now,
    )
    session.add(venue)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The caller owns the transaction and must roll it back; here we only
        # translate the DB-level uniqueness violation into a domain error.
        raise SlugTakenError(slug) from exc

    admin = AdminAccount(
        venue_id=venue.id,
        login=login,
        password_hash=password_hash,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(admin)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise LoginTakenError(login) from exc

    # A new venue starts fully closed and is opened day by day in the admin UI
    # (PROJECT-SPEC §5.2, §53).
    await seed_default_schedule(session, venue.id)
    # Onboarding also creates the first hall so the canvas is immediately usable
    # (PROJECT-SPEC §53).
    await create_default_hall(session, venue.id)

    return venue, admin


async def get_venue(session: AsyncSession, identifier: str | int) -> Venue:
    """Look up a venue by numeric id or slug."""
    if isinstance(identifier, int) or str(identifier).isdigit():
        venue = await session.get(Venue, int(identifier))
    else:
        result = await session.execute(select(Venue).where(Venue.slug == str(identifier)))
        venue = result.scalar_one_or_none()
    if venue is None:
        raise VenueNotFoundError(identifier)
    return venue


async def get_admin_for_venue(session: AsyncSession, venue_id: int) -> AdminAccount:
    result = await session.execute(select(AdminAccount).where(AdminAccount.venue_id == venue_id))
    admin = result.scalar_one_or_none()
    if admin is None:
        raise AdminNotFoundError(venue_id)
    return admin


async def set_password_hash(session: AsyncSession, admin: AdminAccount, password_hash: str) -> None:
    """Replace the admin password hash and update the timestamp."""
    now = await operation_now(session)
    admin.password_hash = password_hash
    admin.updated_at = now


async def revoke_all_sessions(session: AsyncSession, venue_id: int) -> int:
    """Delete every session of a venue. Returns the number removed."""
    result = await session.execute(delete(AdminSession).where(AdminSession.venue_id == venue_id))
    return int(cast(CursorResult[Any], result).rowcount or 0)


async def set_venue_active(session: AsyncSession, venue: Venue, *, active: bool) -> Venue:
    """Suspend or re-enable a venue.

    Suspending also revokes all of the venue's sessions, so a suspended venue's
    admins are locked out immediately rather than on their next cookie check.
    """
    now = await operation_now(session)
    venue.is_active = active
    venue.updated_at = now
    if not active:
        await revoke_all_sessions(session, venue.id)
    return venue


async def list_venues(session: AsyncSession) -> list[Venue]:
    result = await session.execute(select(Venue).order_by(Venue.slug))
    return list(result.scalars().all())


_EDITABLE_SETTING_FIELDS = frozenset({"name", "address", "phone", "online_booking_enabled"})


async def update_venue_settings(
    session: AsyncSession,
    venue_id: int,
    *,
    changes: Mapping[str, object],
) -> Venue:
    """Apply a PATCH to the session's own venue only.

    ``changes`` holds only the fields the client actually sent: a field present
    with ``null`` clears a nullable column, an absent field is left unchanged.
    ``slug``, ``timezone`` and ``is_active`` are not editable here (timezone
    changes need the rolling capability check, §35); any other key is ignored.
    """
    values: dict[str, object] = {"updated_at": await operation_now(session)}
    for field, value in changes.items():
        if field in _EDITABLE_SETTING_FIELDS:
            values[field] = value
    await session.execute(update(Venue).where(Venue.id == venue_id).values(**values))
    result = await session.execute(select(Venue).where(Venue.id == venue_id))
    return result.scalar_one()
