"""HTTP security boundary: headers, CSRF Origin, CORS and proxy/IP trust (§39, §40).

These are transport-level guarantees, so they are asserted through the real
``TestClient`` / ASGI boundary rather than by calling a pure helper.
"""

from __future__ import annotations

import pytest
from app.api.admin.dependencies import client_ip, require_trusted_origin
from app.api.errors import ApiError
from app.api.public.venues import _client_ip_hmac
from app.middleware import CONTENT_SECURITY_POLICY
from app.settings import Settings
from fastapi.testclient import TestClient
from starlette.requests import Request

ALLOWED_ORIGIN = "http://localhost:5173"
FOREIGN_ORIGIN = "http://evil.example"


def _request(*, client: tuple[str, int] | None, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/admin/v1/auth/login",
        "query_string": b"",
        "server": ("testserver", 80),
        "headers": headers,
        "client": client,
    }
    return Request(scope)


def _origin_request(*, method: str, origin: str | None) -> Request:
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/api/admin/v1/auth/login",
        "query_string": b"",
        "server": ("testserver", 80),
        "headers": headers,
        "client": ("127.0.0.1", 5000),
    }
    return Request(scope)


# --- baseline security headers ---------------------------------------------


def test_responses_carry_the_baseline_security_headers(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    headers = response.headers
    assert headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "same-origin"
    assert "geolocation=()" in headers["Permissions-Policy"]


def test_csp_forbids_inline_and_eval_script() -> None:
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY
    assert "unsafe-inline" not in CONTENT_SECURITY_POLICY.split("style-src")[0]
    assert "unsafe-eval" not in CONTENT_SECURITY_POLICY


def test_error_responses_still_carry_security_headers(client: TestClient) -> None:
    response = client.get("/api/admin/v1/me")  # no cookie -> 401
    assert response.status_code == 401
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY


# --- CSRF / Origin ----------------------------------------------------------


def test_foreign_origin_state_changing_request_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/admin/v1/auth/login",
        json={"login": "x", "password": "y"},
        headers={"Origin": FOREIGN_ORIGIN},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_ORIGIN_REJECTED"


async def test_missing_origin_is_not_a_csrf_rejection(settings: Settings) -> None:
    # A missing Origin is treated as a non-browser client and is not rejected by
    # the CSRF check (the spec relies on Origin + SameSite, not a token).
    await require_trusted_origin(_origin_request(method="POST", origin=None), settings)


async def test_allowed_origin_is_not_rejected_by_csrf(settings: Settings) -> None:
    await require_trusted_origin(_origin_request(method="POST", origin=ALLOWED_ORIGIN), settings)


async def test_foreign_origin_dependency_raises_403(settings: Settings) -> None:
    with pytest.raises(ApiError) as info:
        await require_trusted_origin(
            _origin_request(method="POST", origin=FOREIGN_ORIGIN), settings
        )
    assert info.value.status_code == 403
    assert info.value.code == "CSRF_ORIGIN_REJECTED"


async def test_get_requests_are_not_origin_checked(settings: Settings) -> None:
    # Safe methods never require the Origin check.
    await require_trusted_origin(_origin_request(method="GET", origin=FOREIGN_ORIGIN), settings)


def test_cors_preflight_allows_the_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/admin/v1/auth/login",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_preflight_does_not_allow_a_foreign_origin(client: TestClient) -> None:
    response = client.options(
        "/api/admin/v1/auth/login",
        headers={
            "Origin": FOREIGN_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


# --- trusted-proxy / client-IP boundary -------------------------------------


def test_direct_client_cannot_spoof_x_forwarded_for() -> None:
    # ``request.client`` is whatever the ASGI server resolved after the trusted
    # proxy policy. The application layer must ignore the raw header entirely.
    request = _request(client=("203.0.113.5", 4242), forwarded_for="1.2.3.4")
    assert client_ip(request) == "203.0.113.5"


def test_application_ignores_a_multi_hop_forwarded_chain() -> None:
    request = _request(client=("203.0.113.5", 4242), forwarded_for="1.2.3.4, 5.6.7.8, 9.9.9.9")
    assert client_ip(request) == "203.0.113.5"


def test_public_create_fingerprint_ignores_forwarded_headers(settings: Settings) -> None:
    with_header = _request(client=("198.51.100.9", 1), forwarded_for="1.2.3.4")
    without_header = _request(client=("198.51.100.9", 2))
    assert _client_ip_hmac(settings, with_header) == _client_ip_hmac(settings, without_header)


def test_ipv6_textual_variants_produce_one_fingerprint(settings: Settings) -> None:
    compressed = _request(client=("::1", 1))
    expanded = _request(client=("0:0:0:0:0:0:0:1", 2))
    assert _client_ip_hmac(settings, compressed) == _client_ip_hmac(settings, expanded)


def test_missing_client_and_malformed_values_collapse(settings: Settings) -> None:
    missing = _request(client=None)
    malformed = _request(client=("not-an-ip", 1))
    assert _client_ip_hmac(settings, missing) == _client_ip_hmac(settings, malformed)
    assert client_ip(missing) == "unknown"


@pytest.mark.parametrize("forwarded", ["garbage", "1.2.3.4", "::1, 1.2.3.4"])
def test_forged_forwarded_values_never_become_the_identity(
    settings: Settings, forwarded: str
) -> None:
    trusted = _request(client=("10.0.0.7", 1))
    forged = _request(client=("10.0.0.7", 2), forwarded_for=forwarded)
    assert _client_ip_hmac(settings, trusted) == _client_ip_hmac(settings, forged)
