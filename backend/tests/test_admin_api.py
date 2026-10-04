"""Admin API guards that do not need a database (PROJECT-SPEC §35, §39.3).

The database-backed login/session behaviour is covered by the integration
suite; these tests pin the request-level contracts (unauthenticated access,
CSRF origin rejection, exposed API surface).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

ADMIN_PREFIX = "/api/admin/v1"


def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get(f"{ADMIN_PREFIX}/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_settings_requires_authentication(client: TestClient) -> None:
    response = client.get(f"{ADMIN_PREFIX}/settings")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_login_rejects_untrusted_origin(client: TestClient) -> None:
    response = client.post(
        f"{ADMIN_PREFIX}/auth/login",
        json={"login": "someone", "password": "whatever"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_ORIGIN_REJECTED"


def test_logout_rejects_untrusted_origin(client: TestClient) -> None:
    response = client.post(
        f"{ADMIN_PREFIX}/auth/logout",
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_ORIGIN_REJECTED"


def test_trusted_origin_passes_the_csrf_guard(client: TestClient) -> None:
    # With a trusted origin the CSRF guard passes; logout then fails auth
    # (no cookie) with 401 rather than 403, without touching the database.
    response = client.post(
        f"{ADMIN_PREFIX}/auth/logout",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_openapi_exposes_admin_auth_surface(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert f"{ADMIN_PREFIX}/auth/login" in paths
    assert f"{ADMIN_PREFIX}/auth/logout" in paths
    assert f"{ADMIN_PREFIX}/auth/logout-all" in paths
    assert f"{ADMIN_PREFIX}/me" in paths
    assert f"{ADMIN_PREFIX}/settings" in paths
    assert "SettingsUpdate" in schema["components"]["schemas"]
