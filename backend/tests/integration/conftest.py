"""Integration test bootstrap.

Integration tests run against a real PostgreSQL database whose schema is built
by the *real* migration path — never by hand-written DDL. This mirrors the CI
check "clean DB from zero, full ``upgrade head``" (PROJECT-SPEC §45).

Set ``TAKEPLACE_TEST_DATABASE_URL`` to the application-role DSN of a disposable
database. When it is unset, all integration tests are skipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_TEST_URL = os.environ.get("TAKEPLACE_TEST_DATABASE_URL")


def _migrator_url(app_url: str) -> str:
    """Derive the migrator-role DSN from the application-role DSN."""
    migrator_user = os.environ.get("TAKEPLACE_DB_MIGRATOR_USER", "takeplace_migrator")
    migrator_password = os.environ.get("TAKEPLACE_DB_MIGRATOR_PASSWORD")
    if not migrator_password:
        pytest.skip("TAKEPLACE_DB_MIGRATOR_PASSWORD is required for integration tests")
    parts = urlsplit(app_url)
    host = parts.hostname or "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{migrator_user}:{migrator_password}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Bring the test database to head once per session."""
    if not APP_TEST_URL:
        return
    env = {
        **os.environ,
        # alembic/env.py reads the DSN from settings; point it at the test DB.
        "TAKEPLACE_DATABASE_URL": _migrator_url(APP_TEST_URL),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed against the test database:\n"
            f"{result.stdout}\n{result.stderr}"
        )
