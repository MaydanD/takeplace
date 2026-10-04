"""Admin authentication endpoints (PROJECT-SPEC §35, §39)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.admin.dependencies import (
    AuthContextDep,
    SessionDep,
    SettingsDep,
    client_ip,
    require_trusted_origin,
)
from app.api.admin.schemas import (
    AdminSummary,
    LoginRequest,
    MeResponse,
    StatusResponse,
    VenueSummary,
)
from app.api.errors import ApiError, invalid_credentials, rate_limited
from app.security.cookies import clear_session_cookie, set_session_cookie
from app.security.limits import get_burst_limiter, get_login_limiter
from app.security.passwords import get_password_hasher
from app.security.tokens import hmac_sha256_hex
from app.services.auth import create_session, delete_session, find_admin_by_login
from app.services.venues import revoke_all_sessions

router = APIRouter(prefix="/auth", tags=["admin-auth"])


@router.post(
    "/login",
    response_model=MeResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> MeResponse:
    """Authenticate and start a server-side session.

    Wrong login and wrong password return the same response (§39.5). Two cheap
    rate limits run before the CPU-bound Argon2id verify so the bounded pool
    cannot be saturated by garbage logins.
    """
    ip = client_ip(request)
    # Keys are HMAC fingerprints; raw IPs and logins are never stored (§40).
    network_fingerprint = hmac_sha256_hex(settings.abuse_hmac_key, ip)
    login_key = hmac_sha256_hex(settings.abuse_hmac_key, f"{payload.login}|{ip}")

    burst = get_burst_limiter()
    burst_key = f"burst:{network_fingerprint}"
    if not burst.is_allowed(burst_key):
        raise rate_limited(burst.retry_after(burst_key))
    limiter = get_login_limiter()
    if not limiter.is_allowed(login_key):
        raise rate_limited(limiter.retry_after(login_key))

    hasher = get_password_hasher()
    found = await find_admin_by_login(session, payload.login)
    if found is None:
        # Equalize timing with the existing-account path.
        await hasher.verify_dummy(payload.password)
        raise invalid_credentials()

    admin, venue = found
    if not await hasher.verify(admin.password_hash, payload.password):
        raise invalid_credentials()

    if not admin.is_active:
        raise ApiError(403, "ADMIN_DISABLED", "admin account is disabled")
    if not venue.is_active:
        raise ApiError(403, "VENUE_SUSPENDED", "venue is suspended")

    raw_token, _row = await create_session(
        session, venue_id=venue.id, ttl_days=settings.session_ttl_days
    )
    await session.commit()
    secure = settings.cookie_secure
    set_session_cookie(
        response, raw_token, max_age_seconds=settings.session_ttl_days * 86_400, secure=secure
    )
    return MeResponse(admin=AdminSummary.from_model(admin), venue=VenueSummary.from_model(venue))


@router.post(
    "/logout",
    response_model=StatusResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def logout(
    response: Response,
    context: AuthContextDep,
    session: SessionDep,
    settings: SettingsDep,
) -> StatusResponse:
    """Revoke the current session and clear the cookie."""
    await delete_session(session, context.session.id)
    await session.commit()
    clear_session_cookie(response, secure=settings.cookie_secure)
    return StatusResponse(status="ok")


@router.post(
    "/logout-all",
    response_model=StatusResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def logout_all(
    response: Response,
    context: AuthContextDep,
    session: SessionDep,
    settings: SettingsDep,
) -> StatusResponse:
    """Revoke every session of this venue, including the current one."""
    await revoke_all_sessions(session, context.venue_id)
    await session.commit()
    clear_session_cookie(response, secure=settings.cookie_secure)
    return StatusResponse(status="ok")
