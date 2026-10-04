"""Stage 2 integration tests against a real PostgreSQL database.

Skipped unless ``TAKEPLACE_TEST_DATABASE_URL`` is set. They exercise the real
operator path: venues are created with the CLI, sessions with the HTTP API, and
every invariant is checked against the actual database.

Coverage maps to the Stage 2 acceptance criterion and PROJECT-SPEC §7, §39, §53:

* create a venue with the CLI and log in safely;
* correct/incorrect credentials, cookie flags;
* logout, logout-all and password-reset revocation;
* suspend/enable;
* tenant isolation (a venue cannot reach another venue's data);
* raw passwords/tokens never stored;
* timezone validation and the rolling capability health signal;
* the database is the last arbiter of invariants (CHECK/UNIQUE).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator

import pytest
from app.main import create_app
from app.security.cookies import DEV_SESSION_COOKIE_NAME, SESSION_COOKIE_NAME
from app.security.tokens import hash_session_token
from app.settings import get_settings
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# The shared helpers live in ``support`` so Stage 3 tests reuse the exact same
# CLI/API bootstrap instead of re-deriving it.
from tests.integration.support import (
    APP_URL,
    LOGOUT_ALL_URL,
    LOGOUT_URL,
    ME_URL,
    NEW_PASSWORD,
    ORIGIN,
    PASSWORD,
    SETTINGS_URL,
    create_venue,
    make_settings,
    run_cli,
)
from tests.integration.support import cookie_header as _cookie_header
from tests.integration.support import login as _login
from tests.integration.support import unique as _unique

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_test_db() -> None:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    # Production-hardened cookie (``__Host-`` + Secure); local HTTP dev relaxes
    # it (see the dedicated regression test below).
    settings = make_settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client


@pytest.fixture
async def app_session() -> AsyncIterator[AsyncSession]:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    engine = create_async_engine(APP_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def test_login_logout_roundtrip_sets_and_revokes_host_cookie(api_client: TestClient) -> None:
    slug, login = _unique("venue"), _unique("admin")
    assert create_venue(slug, login).returncode == 0

    response = _login(api_client, login, PASSWORD)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["venue"]["slug"] == slug
    assert body["admin"]["login"] == login

    set_cookie = response.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie or "samesite=strict" in set_cookie
    assert "Path=/" in set_cookie
    assert "Domain=" not in set_cookie

    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token
    api_client.cookies.clear()

    me = api_client.get(ME_URL, headers=_cookie_header(token))
    assert me.status_code == 200
    assert me.json()["venue"]["slug"] == slug

    logout = api_client.post(LOGOUT_URL, headers={**_cookie_header(token), "Origin": ORIGIN})
    assert logout.status_code == 200

    # The revoked session cannot be reused.
    after = api_client.get(ME_URL, headers=_cookie_header(token))
    assert after.status_code == 401
    assert after.json()["code"] == "UNAUTHENTICATED"


def test_dev_cookie_relaxation_drops_host_prefix_and_secure() -> None:
    """Non-production may serve a non-``__Host-`` cookie without ``Secure``.

    Browsers reject ``__Host-`` + Secure over plain http, so local development
    must use the relaxed form; production validation forbids this (§39).
    """
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    settings = make_settings(session_cookie_secure=False)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    slug, login = _unique("venue"), _unique("admin")
    assert create_venue(slug, login).returncode == 0

    with TestClient(app) as client:
        response = _login(client, login, PASSWORD)
        assert response.status_code == 200, response.text
        set_cookie = response.headers.get("set-cookie", "")
        assert f"{DEV_SESSION_COOKIE_NAME}=" in set_cookie
        assert SESSION_COOKIE_NAME not in set_cookie
        assert "Secure" not in set_cookie
        assert "HttpOnly" in set_cookie
        token = response.cookies.get(DEV_SESSION_COOKIE_NAME)
        assert token
        client.cookies.clear()
        me = client.get(ME_URL, headers={"Cookie": f"{DEV_SESSION_COOKIE_NAME}={token}"})
        assert me.status_code == 200


def test_wrong_password_and_unknown_login_are_identical(api_client: TestClient) -> None:
    slug, login = _unique("venue"), _unique("admin")
    assert create_venue(slug, login).returncode == 0

    wrong_password = _login(api_client, login, "definitely-wrong")
    api_client.cookies.clear()
    unknown_login = _login(api_client, _unique("ghost"), "definitely-wrong")

    assert wrong_password.status_code == 401
    assert unknown_login.status_code == 401
    assert wrong_password.json() == unknown_login.json()
    assert wrong_password.json()["code"] == "INVALID_CREDENTIALS"


def test_logout_all_revokes_every_session(api_client: TestClient) -> None:
    slug, login = _unique("venue"), _unique("admin")
    assert create_venue(slug, login).returncode == 0

    first = _login(api_client, login, PASSWORD)
    api_client.cookies.clear()
    second = _login(api_client, login, PASSWORD)
    api_client.cookies.clear()

    token_a = first.cookies.get(SESSION_COOKIE_NAME)
    token_b = second.cookies.get(SESSION_COOKIE_NAME)
    assert token_a and token_b and token_a != token_b

    logout_all = api_client.post(
        LOGOUT_ALL_URL, headers={**_cookie_header(token_a), "Origin": ORIGIN}
    )
    assert logout_all.status_code == 200

    assert api_client.get(ME_URL, headers=_cookie_header(token_a)).status_code == 401
    assert api_client.get(ME_URL, headers=_cookie_header(token_b)).status_code == 401


def test_reset_password_invalidates_sessions(api_client: TestClient) -> None:
    slug, login = _unique("venue"), _unique("admin")
    assert create_venue(slug, login).returncode == 0
    token = _login(api_client, login, PASSWORD).cookies.get(SESSION_COOKIE_NAME)
    api_client.cookies.clear()
    assert token

    reset = run_cli("reset-password", slug, "--password", NEW_PASSWORD)
    assert reset.returncode == 0, reset.stderr

    assert api_client.get(ME_URL, headers=_cookie_header(token)).status_code == 401
    assert _login(api_client, login, PASSWORD).status_code == 401
    api_client.cookies.clear()
    assert _login(api_client, login, NEW_PASSWORD).status_code == 200


def test_suspend_blocks_access_and_enable_restores_it(api_client: TestClient) -> None:
    slug, login = _unique("venue"), _unique("admin")
    assert create_venue(slug, login).returncode == 0
    token = _login(api_client, login, PASSWORD).cookies.get(SESSION_COOKIE_NAME)
    api_client.cookies.clear()
    assert token

    assert run_cli("suspend-venue", slug).returncode == 0

    # An existing session is denied, and a fresh login is refused.
    assert api_client.get(ME_URL, headers=_cookie_header(token)).status_code == 401
    suspended = _login(api_client, login, PASSWORD)
    assert suspended.status_code == 403
    assert suspended.json()["code"] == "VENUE_SUSPENDED"
    api_client.cookies.clear()

    assert run_cli("enable-venue", slug).returncode == 0
    assert _login(api_client, login, PASSWORD).status_code == 200


def test_tenant_isolation(api_client: TestClient) -> None:
    slug_a, login_a = _unique("venue-a"), _unique("admin-a")
    slug_b, login_b = _unique("venue-b"), _unique("admin-b")
    assert create_venue(slug_a, login_a).returncode == 0
    assert create_venue(slug_b, login_b).returncode == 0

    logins: dict[str, tuple[str, int]] = {}
    tokens: dict[str, str] = {}
    for key, login in (("a", login_a), ("b", login_b)):
        response = _login(api_client, login, PASSWORD)
        assert response.status_code == 200
        tokens[key] = response.cookies.get(SESSION_COOKIE_NAME) or ""
        logins[key] = (
            response.json()["venue"]["slug"],
            response.json()["venue"]["id"],
        )
        api_client.cookies.clear()

    assert logins["a"][0] == slug_a
    assert logins["b"][0] == slug_b
    venue_b_id = logins["b"][1]

    # A's session resolves only to A: the server ignores any tenant selector.
    me_a = api_client.get(
        ME_URL, params={"venue_id": venue_b_id}, headers=_cookie_header(tokens["a"])
    )
    assert me_a.status_code == 200
    assert me_a.json()["venue"]["slug"] == slug_a

    settings_a = api_client.get(
        SETTINGS_URL, params={"venue_id": venue_b_id}, headers=_cookie_header(tokens["a"])
    )
    assert settings_a.status_code == 200
    assert settings_a.json()["slug"] == slug_a

    # A cannot mutate B, even with a smuggled venue_id in the body.
    patch = api_client.patch(
        SETTINGS_URL,
        json={"name": "Renamed by A", "venue_id": venue_b_id},
        headers={**_cookie_header(tokens["a"]), "Origin": ORIGIN},
    )
    assert patch.status_code == 200
    assert patch.json()["slug"] == slug_a
    assert patch.json()["name"] == "Renamed by A"

    settings_b = api_client.get(SETTINGS_URL, headers=_cookie_header(tokens["b"]))
    assert settings_b.json()["slug"] == slug_b
    assert settings_b.json()["name"] != "Renamed by A"


async def test_database_rejects_invalid_slug_and_reserved_slug(app_session: AsyncSession) -> None:
    for bad in ("UPPER", "admin"):
        with pytest.raises(IntegrityError):
            async with app_session.begin_nested():
                await app_session.execute(
                    text(
                        "INSERT INTO venues (slug, name, timezone) "
                        "VALUES (:slug, 'x', 'Europe/Moscow')"
                    ),
                    {"slug": bad},
                )


async def test_database_rejects_duplicate_login_and_second_admin(
    app_session: AsyncSession,
) -> None:
    slug_one, slug_two = _unique("db-venue"), _unique("db-venue")
    login = _unique("db-admin")
    ids: list[int] = []
    for slug in (slug_one, slug_two):
        result = await app_session.execute(
            text(
                "INSERT INTO venues (slug, name, timezone) "
                "VALUES (:slug, 'x', 'Europe/Moscow') RETURNING id"
            ),
            {"slug": slug},
        )
        ids.append(int(result.scalar_one()))
    await app_session.flush()

    # First admin inserts fine.
    await app_session.execute(
        text(
            "INSERT INTO admin_accounts (venue_id, login, password_hash) "
            "VALUES (:venue_id, :login, 'x')"
        ),
        {"venue_id": ids[0], "login": login},
    )
    await app_session.flush()

    # A second account for the same venue violates UNIQUE(venue_id).
    with pytest.raises(IntegrityError):
        async with app_session.begin_nested():
            await app_session.execute(
                text(
                    "INSERT INTO admin_accounts (venue_id, login, password_hash) "
                    "VALUES (:venue_id, :login, 'x')"
                ),
                {"venue_id": ids[0], "login": _unique("db-admin")},
            )

    # A duplicate global login violates UNIQUE(login).
    with pytest.raises(IntegrityError):
        async with app_session.begin_nested():
            await app_session.execute(
                text(
                    "INSERT INTO admin_accounts (venue_id, login, password_hash) "
                    "VALUES (:venue_id, :login, 'x')"
                ),
                {"venue_id": ids[1], "login": login},
            )


async def test_raw_password_and_token_are_never_stored(
    api_client: TestClient, app_session: AsyncSession
) -> None:
    slug, login = _unique("venue"), _unique("admin")
    assert create_venue(slug, login).returncode == 0

    response = _login(api_client, login, PASSWORD)
    token = response.cookies.get(SESSION_COOKIE_NAME)
    api_client.cookies.clear()
    assert token

    stored_hash = (
        await app_session.execute(
            text(
                "SELECT s.token_hash FROM admin_sessions s "
                "JOIN admin_accounts a ON a.venue_id = s.venue_id "
                "WHERE a.login = :login"
            ),
            {"login": login},
        )
    ).scalar_one()
    assert stored_hash == hash_session_token(token)
    assert stored_hash != token
    assert token not in stored_hash

    password_hash = (
        await app_session.execute(
            text("SELECT password_hash FROM admin_accounts WHERE login = :login"),
            {"login": login},
        )
    ).scalar_one()
    assert password_hash.startswith("$argon2id$")
    assert password_hash != PASSWORD
    assert PASSWORD not in password_hash


def test_cli_generates_and_prints_password_once() -> None:
    slug, login = _unique("venue"), _unique("admin")
    result = create_venue(slug, login, password=None)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("password:") == 1

    match = re.search(r"password:\s+(\S+)", result.stdout)
    assert match, result.stdout
    password = match.group(1)
    assert password != PASSWORD

    settings = make_settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        assert _login(client, login, password).status_code == 200


@pytest.mark.parametrize(
    "timezone",
    ["Not/AZone", "Europe/Berlin"],
)
def test_cli_rejects_unusable_timezone(timezone: str) -> None:
    slug, login = _unique("venue"), _unique("admin")
    result = create_venue(slug, login, timezone=timezone)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "error:" in result.stderr


def test_cli_rejects_reserved_slug() -> None:
    result = create_venue("admin", _unique("admin"))
    assert result.returncode != 0
    assert "reserved" in result.stderr


def test_health_ops_flags_timezone_capability_transition() -> None:
    # Simulate an active venue whose timezone gained a transition after
    # onboarding (e.g. a tzdata update). The rolling check must raise ops.
    import asyncio
    import time

    async def _insert_dst_venue() -> None:
        engine = create_async_engine(APP_URL)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO venues (slug, name, timezone, is_active) "
                    "VALUES (:slug, 'x', 'Europe/Berlin', true)"
                ),
                {"slug": _unique("dst-venue")},
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_insert_dst_venue())

    settings = make_settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        # The monitor scans at startup; wait for that scan to land.
        body = client.get("/health/ops").json()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and body["timezone_capability"] != "unsupported":
            time.sleep(0.05)
            body = client.get("/health/ops").json()

    assert body["timezone_capability"] == "unsupported"
    assert body["status"] == "degraded"
