"""Health endpoint tests (PROJECT-SPEC §47)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_liveness_is_dependency_free(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_503_without_database(client: TestClient) -> None:
    # The test app never initialises an engine, so the readiness probe must
    # report unavailable rather than crash or lie.
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["database"] == "unavailable"


def test_ops_reports_degraded_but_stays_200(client: TestClient) -> None:
    response = client.get("/health/ops")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["environment"] == "development"
    assert body["outbox_unacknowledged_dead"] == 0


def test_openapi_schema_is_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Takeplace API"
    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
    assert "/health/ops" in schema["paths"]
