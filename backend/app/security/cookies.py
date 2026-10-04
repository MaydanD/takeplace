"""Admin session cookie helpers (PROJECT-SPEC §39.2).

Production uses the ``__Host-`` prefix, which browsers only accept when the
cookie is also Secure, has ``Path=/`` and carries no ``Domain`` attribute.

Browsers *reject* a ``__Host-`` + ``Secure`` cookie set from a plain-http
origin (including ``http://localhost``; Chromium issue 40202941). Local HTTP
development therefore uses a non-prefixed cookie name with ``Secure`` omitted.
This relaxation is only reachable when ``Settings.cookie_secure`` is false,
which production validation forbids (§39).
"""

from __future__ import annotations

from fastapi import Response

# ``__Host-`` prefix: Secure + Path=/ + no Domain, per the browser rule.
SESSION_COOKIE_NAME = "__Host-takeplace_admin"
# Non-prefixed name used only for local HTTP development (``Secure`` omitted).
DEV_SESSION_COOKIE_NAME = "takeplace_admin"


def session_cookie_name(*, secure: bool) -> str:
    """Return the cookie name matching the requested hardening level."""
    return SESSION_COOKIE_NAME if secure else DEV_SESSION_COOKIE_NAME


def set_session_cookie(
    response: Response, token: str, *, max_age_seconds: int, secure: bool = True
) -> None:
    response.set_cookie(
        key=session_cookie_name(secure=secure),
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool = True) -> None:
    response.delete_cookie(
        key=session_cookie_name(secure=secure),
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
