"""Admin authentication and server-side sessions (PROJECT-SPEC §6.3, §39).

Only the SHA-256 hash of a session token is ever persisted. The raw token exists
solely in the client cookie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAccount, AdminSession, Venue
from app.db.time import operation_now
from app.security.tokens import generate_session_token, hash_session_token


@dataclass(slots=True)
class AuthContext:
    """The authenticated tenant context derived exclusively from the session."""

    session: AdminSession
    admin: AdminAccount
    venue: Venue

    @property
    def venue_id(self) -> int:
        return self.venue.id


async def find_admin_by_login(
    session: AsyncSession, login: str
) -> tuple[AdminAccount, Venue] | None:
    result = await session.execute(
        select(AdminAccount, Venue)
        .join(Venue, Venue.id == AdminAccount.venue_id)
        .where(AdminAccount.login == login)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return row[0], row[1]


async def create_session(
    session: AsyncSession, *, venue_id: int, ttl_days: int
) -> tuple[str, AdminSession]:
    """Create a session and return ``(raw_token, row)``.

    ``expires_at`` is an absolute TTL from ``created_at``; there is no sliding
    extension (§6.3).
    """
    now = await operation_now(session)
    raw_token = generate_session_token()
    row = AdminSession(
        venue_id=venue_id,
        token_hash=hash_session_token(raw_token),
        created_at=now,
        expires_at=now + timedelta(days=ttl_days),
        last_seen_at=now,
    )
    session.add(row)
    await session.flush()
    return raw_token, row


async def resolve_session(
    session: AsyncSession,
    raw_token: str,
    *,
    last_seen_refresh_seconds: int,
) -> AuthContext | None:
    """Resolve a raw cookie token to its tenant context, or ``None``.

    Expired sessions are rejected and deleted. Suspension of the venue or the
    admin account is reported by ``None`` as well, so callers cannot get an
    authenticated context for a suspended tenant.
    """
    token_hash = hash_session_token(raw_token)
    result = await session.execute(
        select(AdminSession, AdminAccount, Venue)
        .join(AdminAccount, AdminAccount.venue_id == AdminSession.venue_id)
        .join(Venue, Venue.id == AdminSession.venue_id)
        .where(AdminSession.token_hash == token_hash)
    )
    row = result.one_or_none()
    if row is None:
        return None
    db_session, admin, venue = row

    now = await operation_now(session)
    if db_session.expires_at <= now:
        await session.execute(delete(AdminSession).where(AdminSession.id == db_session.id))
        return None

    if not admin.is_active or not venue.is_active:
        return None

    # Refresh last_seen_at at most once per configured interval (§6.3).
    if (now - db_session.last_seen_at).total_seconds() >= last_seen_refresh_seconds:
        db_session.last_seen_at = now

    return AuthContext(session=db_session, admin=admin, venue=venue)


async def session_validity(session: AsyncSession, raw_token: str) -> int | None:
    """Cheap, read-only validity probe for long-lived connections (§37.4).

    Unlike :func:`resolve_session` this never refreshes ``last_seen_at`` and never
    deletes rows: the realtime stream re-runs it on a bounded cadence, so it must
    stay a single indexed lookup that cannot write. It returns the session's
    ``venue_id`` while the session is live and its admin and venue are active,
    otherwise ``None`` — the same revocation signals an ordinary request would
    observe (logout, logout-all, disabled admin, disabled venue, expiry).
    """
    if not raw_token:
        return None
    token_hash = hash_session_token(raw_token)
    result = await session.execute(
        select(AdminSession.venue_id)
        .join(AdminAccount, AdminAccount.venue_id == AdminSession.venue_id)
        .join(Venue, Venue.id == AdminSession.venue_id)
        .where(
            AdminSession.token_hash == token_hash,
            AdminSession.expires_at > func.clock_timestamp(),
            AdminAccount.is_active.is_(True),
            Venue.is_active.is_(True),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def delete_session(session: AsyncSession, session_id: int) -> None:
    await session.execute(delete(AdminSession).where(AdminSession.id == session_id))


async def purge_expired_sessions(session: AsyncSession) -> int:
    """Delete expired sessions. Returns the number removed."""
    now = await operation_now(session)
    result = await session.execute(delete(AdminSession).where(AdminSession.expires_at <= now))
    return int(cast(CursorResult[Any], result).rowcount or 0)
