"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def settings() -> Settings:
    """Development settings with test-only secrets and no real database use."""
    return Settings(
        env="development",
        cors_origins="http://localhost:5173",
        idempotency_hmac_key="test-idempotency-key-0123456789",
        abuse_hmac_key="test-abuse-key-0123456789",
        vk_encryption_keys="1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """HTTP client for the app. Lifespan is skipped so no DB connection opens."""
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
