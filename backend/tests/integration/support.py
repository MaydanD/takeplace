"""Shared helpers for the PostgreSQL-backed integration suite.

Integration tests run against a real database migrated from zero. They create
venues through the *real* CLI and authenticate through the *real* HTTP API, so
these helpers are the single place that knows how to do both. They are plain
functions, not fixtures, so any test module can import and compose them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.main import create_app
from app.security.cookies import SESSION_COOKIE_NAME
from app.settings import Settings, get_settings
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_URL = os.environ.get("TAKEPLACE_TEST_DATABASE_URL")
ORIGIN = "http://localhost:5173"
PASSWORD = "initial-password-0123456789"
NEW_PASSWORD = "rotated-password-0123456789"

ADMIN = "/api/admin/v1"
LOGIN_URL = f"{ADMIN}/auth/login"
LOGOUT_URL = f"{ADMIN}/auth/logout"
LOGOUT_ALL_URL = f"{ADMIN}/auth/logout-all"
ME_URL = f"{ADMIN}/me"
SETTINGS_URL = f"{ADMIN}/settings"
SCHEDULE_URL = f"{ADMIN}/schedule"
SCHEDULE_EXCEPTIONS_URL = f"{ADMIN}/schedule/exceptions"
BUSINESS_DAY_URL = f"{ADMIN}/schedule/business-day"

TEST_HMAC_KEYS = {
    "idempotency_hmac_key": "test-idempotency-key-0123456789",
    "abuse_hmac_key": "test-abuse-key-0123456789",
    "vk_encryption_keys": "1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
}


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def cli_env() -> dict[str, str]:
    return {
        **os.environ,
        "TAKEPLACE_DATABASE_URL": APP_URL or "",
        "TAKEPLACE_CORS_ORIGINS": ORIGIN,
        **TEST_HMAC_KEYS,
    }


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=BACKEND_ROOT,
        env=cli_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def create_venue(
    slug: str,
    login: str,
    *,
    password: str | None = PASSWORD,
    timezone: str = "Europe/Moscow",
) -> subprocess.CompletedProcess[str]:
    args = [
        "create-venue",
        "--slug",
        slug,
        "--name",
        f"Venue {slug}",
        "--timezone",
        timezone,
        "--login",
        login,
    ]
    if password is not None:
        args += ["--password", password]
    return run_cli(*args)


def make_settings(**overrides: object) -> Settings:
    """Test settings bound to the disposable database."""
    base: dict[str, object] = {
        "database_url": APP_URL,
        "cors_origins": ORIGIN,
        **TEST_HMAC_KEYS,
        # Exercise the production-hardened cookie (``__Host-`` + Secure).
        "session_cookie_secure": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@contextmanager
def make_client(settings: Settings | None = None) -> Iterator[TestClient]:
    """Yield a ``TestClient`` for an app built from explicit settings."""
    resolved = settings or make_settings()
    app = create_app(resolved)
    app.dependency_overrides[get_settings] = lambda: resolved
    with TestClient(app) as client:
        yield client


def login(client: TestClient, login_name: str, password: str, *, origin: str = ORIGIN):
    return client.post(
        LOGIN_URL, json={"login": login_name, "password": password}, headers={"Origin": origin}
    )


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE_NAME}={token}"}


def login_token(client: TestClient, login_name: str, password: str = PASSWORD) -> str:
    """Log in, clear the client cookie jar and return the raw session token.

    Clearing the jar makes the token explicit: tests then build raw ``Cookie``
    headers themselves and cannot accidentally rely on ambient client state.
    """
    response = login(client, login_name, password)
    token = response.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise AssertionError(f"login failed: {response.status_code} {response.text}")
    client.cookies.clear()
    return token
