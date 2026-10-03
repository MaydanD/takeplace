"""Middleware tests: security headers and request correlation."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_security_headers_present_on_responses(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"
    csp = response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'self'" in csp


def test_hsts_absent_in_development(client: TestClient) -> None:
    response = client.get("/health/live")
    assert "Strict-Transport-Security" not in response.headers


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


def test_request_id_is_generated_when_absent(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.headers.get("X-Request-ID")


def test_cors_allows_configured_origin(client: TestClient) -> None:
    response = client.get(
        "/health/live",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    response = client.get(
        "/health/live",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") is None
