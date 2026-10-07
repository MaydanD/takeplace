"""Admin API dependencies: CSRF origin check, auth context, client identity.

The tenant context is derived **only** from the session cookie. No endpoint
accepts a ``venue_id`` from the client (PROJECT-SPEC §7.1).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError, unauthenticated
from app.db.session import get_db_session
from app.security.client_ip import canonical_client_ip
from app.security.cookies import session_cookie_name
from app.services.auth import AuthContext, resolve_session
from app.settings import Settings, get_settings

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def require_trusted_origin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Reject a state-changing admin request from an untrusted origin (§39.3).

    Browsers always send ``Origin`` on cross-site state-changing requests, so a
    missing header cannot be used to forge a cross-site request; when it is
    present it must match a configured origin exactly.
    """
    if request.method not in _STATE_CHANGING_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    if origin not in settings.cors_origin_list:
        raise ApiError(403, "CSRF_ORIGIN_REJECTED", "request origin is not allowed")


def client_ip(request: Request) -> str:
    """Return the canonical trusted client address (§39.5).

    ``request.client`` is the address the ASGI server resolved after applying the
    configured trusted-proxy policy; a direct client cannot spoof it through
    ``X-Forwarded-For``. The value is canonicalised so equivalent IPv6 forms share
    one rate-limit identity.
    """
    return canonical_client_ip(request.client.host if request.client else None)


async def get_auth_context(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    """Resolve the authenticated tenant context or raise 401."""
    cookie_name = session_cookie_name(secure=settings.cookie_secure)
    raw_token = request.cookies.get(cookie_name)
    if not raw_token:
        raise unauthenticated()
    context = await resolve_session(
        session,
        raw_token,
        last_seen_refresh_seconds=settings.session_last_seen_refresh_seconds,
    )
    if context is None:
        # Persist any expiry cleanup performed during resolution.
        await session.commit()
        raise unauthenticated()
    await session.commit()
    return context


AuthContextDep = Annotated[AuthContext, Depends(get_auth_context)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
