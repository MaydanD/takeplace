"""Stage 4 integration tests: halls, tables, layout import (PROJECT-SPEC §6.4-6.5, §29).

Run against a real PostgreSQL migrated from zero. They cover the Stage 4
acceptance criterion and §7, §29, §44:

* the default onboarding hall and multiple halls;
* table geometry/capacity/bookability and the read-only layout;
* archive semantics for tables and halls;
* the DB CHECKs and the cross-tenant composite FK as last arbiter;
* the JSON/CLI import: happy path, dry-run, atomicity on invalid payloads,
  duplicate detection and idempotent re-runs;
* tenant isolation on read *and* mutation.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from app.security.cookies import SESSION_COOKIE_NAME
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.support import (
    APP_URL,
    EXAMPLE_LAYOUT_PATH,
    HALLS_URL,
    ME_URL,
    ORIGIN,
    PASSWORD,
    TABLES_URL,
    cookie_header,
    create_venue,
    import_layout_cli,
    login,
    make_client,
    unique,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_test_db() -> None:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")
    with make_client() as client:
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


def _login(client: TestClient, login_name: str) -> str:
    response = login(client, login_name, PASSWORD)
    assert response.status_code == 200, response.text
    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token
    client.cookies.clear()
    return token


def _new_venue(client: TestClient, tz: str = "Europe/Moscow") -> tuple[str, str, str]:
    slug, login_name = unique("venue"), unique("admin")
    assert create_venue(slug, login_name, timezone=tz).returncode == 0
    return slug, login_name, _login(client, login_name)


def _halls(client: TestClient, token: str, *, include_archived: bool = False):
    return client.get(
        HALLS_URL,
        params={"include_archived": include_archived},
        headers=cookie_header(token),
    ).json()["halls"]


def _hall_id(client: TestClient, token: str, name: str) -> int:
    for hall in _halls(client, token, include_archived=True):
        if hall["name"] == name:
            return int(hall["id"])
    raise AssertionError(f"hall {name!r} not found")


def _write_layout(tmp_path: Path, payload: dict, filename: str = "layout.json") -> Path:
    path = tmp_path / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _one_table_layout(hall_name: str, number: str = "1", **table_overrides: object) -> dict:
    table: dict[str, object] = {
        "number": number,
        "capacity": 4,
        "shape": "rect",
        "x": 10,
        "y": 10,
        "width": 80,
        "height": 80,
    }
    table.update(table_overrides)
    return {
        "halls": [{"name": hall_name, "canvas_width": 640, "canvas_height": 480, "tables": [table]}]
    }


# --- halls CRUD ------------------------------------------------------------


def test_new_venue_has_a_default_hall(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    halls = _halls(api_client, token)
    assert len(halls) == 1
    assert halls[0]["canvas_width"] == 1200
    assert halls[0]["canvas_height"] == 800
    assert halls[0]["layout_revision"] == 1
    assert halls[0]["archived_at"] is None


def test_create_multiple_halls(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    for name in ("Бар", "Веранда"):
        response = api_client.post(
            HALLS_URL,
            json={"name": name, "canvas_width": 900, "canvas_height": 600},
            headers={**cookie_header(token), "Origin": ORIGIN},
        )
        assert response.status_code == 201, response.text

    names = {hall["name"] for hall in _halls(api_client, token)}
    assert {"Основной зал", "Бар", "Веранда"} <= names


def test_canvas_change_bumps_revision_but_toggles_do_not(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    hall_id = _hall_id(api_client, token, "Основной зал")

    rename = api_client.patch(
        f"{HALLS_URL}/{hall_id}",
        json={"name": "Зал 1"},
        headers={**cookie_header(token), "Origin": ORIGIN},
    )
    assert rename.status_code == 200
    assert rename.json()["layout_revision"] == 1
    assert rename.json()["name"] == "Зал 1"

    resize = api_client.patch(
        f"{HALLS_URL}/{hall_id}",
        json={"canvas_width": 1500},
        headers={**cookie_header(token), "Origin": ORIGIN},
    )
    assert resize.json()["layout_revision"] == 2

    toggle = api_client.patch(
        f"{HALLS_URL}/{hall_id}",
        json={"is_bookable": False},
        headers={**cookie_header(token), "Origin": ORIGIN},
    )
    assert toggle.json()["is_bookable"] is False
    assert toggle.json()["layout_revision"] == 2  # operational flag did not bump it


def test_hall_detail_returns_tables_and_static_elements(
    api_client: TestClient, tmp_path: Path
) -> None:
    slug, _login_name, token = _new_venue(api_client)
    payload = {
        "halls": [
            {
                "name": "Главный",
                "canvas_width": 800,
                "canvas_height": 600,
                "static_elements": [
                    {"type": "wall", "x": 0, "y": 0, "width": 800, "height": 10},
                    {"type": "text", "x": 20, "y": 20, "text": "Вход"},
                ],
                "tables": [
                    {
                        "number": "1",
                        "capacity": 4,
                        "shape": "circle",
                        "x": 100,
                        "y": 100,
                        "width": 70,
                        "height": 70,
                    }
                ],
            }
        ]
    }
    path = _write_layout(tmp_path, payload)
    assert import_layout_cli(slug, path).returncode == 0

    hall_id = _hall_id(api_client, token, "Главный")
    detail = api_client.get(f"{HALLS_URL}/{hall_id}", headers=cookie_header(token)).json()
    assert detail["canvas_width"] == 800
    assert [e["type"] for e in detail["static_elements"]] == ["wall", "text"]
    assert detail["tables"][0]["number"] == "1"
    assert detail["tables"][0]["shape"] == "circle"


# --- archive semantics -----------------------------------------------------


def test_archive_hall_blocked_while_tables_exist(api_client: TestClient, tmp_path: Path) -> None:
    slug, _login_name, token = _new_venue(api_client)
    assert (
        import_layout_cli(slug, _write_layout(tmp_path, _one_table_layout("Зал"))).returncode == 0
    )
    hall_id = _hall_id(api_client, token, "Зал")

    blocked = api_client.post(
        f"{HALLS_URL}/{hall_id}/archive", headers={**cookie_header(token), "Origin": ORIGIN}
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "HALL_ARCHIVE_BLOCKED"

    # Archive the table, then the hall.
    tables = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"]
    table_id = next(t["id"] for t in tables if t["hall_id"] == hall_id)
    assert (
        api_client.post(
            f"{TABLES_URL}/{table_id}/archive", headers={**cookie_header(token), "Origin": ORIGIN}
        ).status_code
        == 200
    )

    archived = api_client.post(
        f"{HALLS_URL}/{hall_id}/archive", headers={**cookie_header(token), "Origin": ORIGIN}
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    # Hidden by default, visible explicitly.
    assert "Зал" not in {h["name"] for h in _halls(api_client, token)}
    assert "Зал" in {h["name"] for h in _halls(api_client, token, include_archived=True)}


def test_archive_table_hides_from_default_list(api_client: TestClient, tmp_path: Path) -> None:
    slug, _login_name, token = _new_venue(api_client)
    assert (
        import_layout_cli(slug, _write_layout(tmp_path, _one_table_layout("Зал"))).returncode == 0
    )
    tables = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"]
    table_id = tables[0]["id"]

    archived = api_client.post(
        f"{TABLES_URL}/{table_id}/archive", headers={**cookie_header(token), "Origin": ORIGIN}
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    active = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"]
    assert table_id not in {t["id"] for t in active}
    all_tables = api_client.get(
        TABLES_URL, params={"include_archived": True}, headers=cookie_header(token)
    ).json()["tables"]
    assert table_id in {t["id"] for t in all_tables}


def test_bookable_toggle_does_not_change_geometry(api_client: TestClient, tmp_path: Path) -> None:
    slug, _login_name, token = _new_venue(api_client)
    layout = _one_table_layout("Зал", capacity=6, width=120, height=90)
    assert import_layout_cli(slug, _write_layout(tmp_path, layout)).returncode == 0
    before = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"][0]

    toggled = api_client.patch(
        f"{TABLES_URL}/{before['id']}",
        json={"is_bookable": False},
        headers={**cookie_header(token), "Origin": ORIGIN},
    )
    assert toggled.status_code == 200, toggled.text
    body = toggled.json()
    assert body["is_bookable"] is False
    for field in ("capacity", "shape", "x", "y", "width", "height", "rotation"):
        assert body[field] == before[field]


def test_list_view_includes_hall_name(api_client: TestClient, tmp_path: Path) -> None:
    slug, _login_name, token = _new_venue(api_client)
    assert (
        import_layout_cli(slug, _write_layout(tmp_path, _one_table_layout("Зал"))).returncode == 0
    )
    tables = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"]
    assert tables[0]["hall_name"] == "Зал"


# --- database is the last arbiter ------------------------------------------


async def _venue_and_hall(
    client: TestClient, token: str, name: str = "Основной зал"
) -> tuple[int, int]:
    venue_id = int(client.get(ME_URL, headers=cookie_header(token)).json()["venue"]["id"])
    return venue_id, _hall_id(client, token, name)


async def test_database_rejects_duplicate_active_number(
    api_client: TestClient, app_session: AsyncSession, tmp_path: Path
) -> None:
    slug, _login_name, token = _new_venue(api_client)
    assert (
        import_layout_cli(slug, _write_layout(tmp_path, _one_table_layout("Зал"))).returncode == 0
    )
    venue_id, hall_id = await _venue_and_hall(api_client, token, "Зал")

    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "INSERT INTO tables (venue_id, hall_id, number, capacity, x, y, width, height, shape) "
                "VALUES (:v, :h, '1', 4, 10, 10, 80, 80, 'rect')"
            ),
            {"v": venue_id, "h": hall_id},
        )
    assert "uq_tables_hall_id_number_active" in str(excinfo.value)
    await app_session.rollback()


async def test_database_allows_reusing_archived_number(
    api_client: TestClient, app_session: AsyncSession, tmp_path: Path
) -> None:
    slug, _login_name, token = _new_venue(api_client)
    assert (
        import_layout_cli(slug, _write_layout(tmp_path, _one_table_layout("Зал"))).returncode == 0
    )
    venue_id, hall_id = await _venue_and_hall(api_client, token, "Зал")

    await app_session.execute(
        text("UPDATE tables SET archived_at = now() WHERE hall_id = :h AND number = '1'"),
        {"h": hall_id},
    )
    # The partial unique only covers non-archived rows, so this insert passes.
    await app_session.execute(
        text(
            "INSERT INTO tables (venue_id, hall_id, number, capacity, x, y, width, height, shape) "
            "VALUES (:v, :h, '1', 2, 20, 20, 60, 60, 'circle')"
        ),
        {"v": venue_id, "h": hall_id},
    )
    await app_session.commit()


@pytest.mark.parametrize(
    "sql,constraint",
    [
        (
            "INSERT INTO tables (venue_id, hall_id, number, capacity, x, y, width, height, shape) "
            "VALUES (:v, :h, '9', 0, 10, 10, 80, 80, 'rect')",
            "ck_tables_capacity_positive",
        ),
        (
            "INSERT INTO tables (venue_id, hall_id, number, capacity, x, y, width, height, shape) "
            "VALUES (:v, :h, '9', 4, -1, 10, 80, 80, 'rect')",
            "ck_tables_position_non_negative",
        ),
        (
            "INSERT INTO tables (venue_id, hall_id, number, capacity, x, y, width, height, shape) "
            "VALUES (:v, :h, '9', 4, 10, 10, 0, 80, 'rect')",
            "ck_tables_size_positive",
        ),
        (
            "INSERT INTO tables (venue_id, hall_id, number, capacity, x, y, width, height, shape) "
            "VALUES (:v, :h, '9', 4, 10, 10, 80, 80, 'triangle')",
            "ck_tables_shape_allowed",
        ),
    ],
)
async def test_database_rejects_invalid_geometry(
    api_client: TestClient, app_session: AsyncSession, sql: str, constraint: str
) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    venue_id, hall_id = await _venue_and_hall(api_client, token)

    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(text(sql), {"v": venue_id, "h": hall_id})
    assert constraint in str(excinfo.value)
    await app_session.rollback()


async def test_composite_fk_blocks_table_of_another_venue(
    api_client: TestClient, app_session: AsyncSession
) -> None:
    _slug_a, _login_a, token_a = _new_venue(api_client)
    _slug_b, _login_b, token_b = _new_venue(api_client)
    venue_b = int(api_client.get(ME_URL, headers=cookie_header(token_b)).json()["venue"]["id"])
    hall_a = _hall_id(api_client, token_a, "Основной зал")

    # A table claiming venue B but placed in venue A's hall must be rejected.
    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "INSERT INTO tables (venue_id, hall_id, number, capacity, x, y, width, height, shape) "
                "VALUES (:v, :h, '1', 4, 10, 10, 80, 80, 'rect')"
            ),
            {"v": venue_b, "h": hall_a},
        )
    assert "fk_tables_hall_id_venue_id_halls" in str(excinfo.value)
    await app_session.rollback()


# --- JSON / CLI import -----------------------------------------------------


def test_import_example_layout(api_client: TestClient) -> None:
    slug, _login_name, token = _new_venue(api_client)
    result = import_layout_cli(slug, EXAMPLE_LAYOUT_PATH)
    assert result.returncode == 0, result.stderr
    assert "created 2" in result.stdout  # two new halls

    names = {hall["name"] for hall in _halls(api_client, token)}
    assert {"Главный зал", "Летняя веранда"} <= names

    main_id = _hall_id(api_client, token, "Главный зал")
    detail = api_client.get(f"{HALLS_URL}/{main_id}", headers=cookie_header(token)).json()
    assert len(detail["tables"]) == 6
    assert any(t["is_bookable"] is False for t in detail["tables"])
    assert {e["type"] for e in detail["static_elements"]} == {
        "wall",
        "bar",
        "stage",
        "text",
        "zone",
    }


def test_import_is_idempotent(api_client: TestClient) -> None:
    slug, _login_name, token = _new_venue(api_client)
    assert import_layout_cli(slug, EXAMPLE_LAYOUT_PATH).returncode == 0
    before = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"]
    revision_before = _halls(api_client, token)[0]["layout_revision"]

    again = import_layout_cli(slug, EXAMPLE_LAYOUT_PATH)
    assert again.returncode == 0
    assert "created 0, updated 0" in again.stdout
    after = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"]
    assert len(after) == len(before)
    assert _halls(api_client, token)[0]["layout_revision"] == revision_before


def test_import_dry_run_writes_nothing(api_client: TestClient) -> None:
    slug, _login_name, token = _new_venue(api_client)
    result = import_layout_cli(slug, EXAMPLE_LAYOUT_PATH, dry_run=True)
    assert result.returncode == 0
    assert "is valid" in result.stdout
    assert {hall["name"] for hall in _halls(api_client, token)} == {"Основной зал"}


def test_import_invalid_payload_is_atomic(api_client: TestClient, tmp_path: Path) -> None:
    slug, _login_name, token = _new_venue(api_client)
    before = len(_halls(api_client, token, include_archived=True))
    payload = {
        "halls": [
            _one_table_layout("Первый")["halls"][0],  # valid
            {
                "name": "Второй",
                "canvas_width": 400,
                "canvas_height": 400,
                "tables": [],
                "static_elements": [{"type": "hologram", "x": 0, "y": 0}],
            },
        ]
    }
    result = import_layout_cli(slug, _write_layout(tmp_path, payload))
    assert result.returncode == 2  # usage error
    assert "invalid layout" in result.stderr
    # Neither hall was created.
    assert len(_halls(api_client, token, include_archived=True)) == before


def test_import_rejects_duplicate_numbers(api_client: TestClient, tmp_path: Path) -> None:
    slug, _login_name, _token = _new_venue(api_client)
    layout = _one_table_layout("Зал")
    layout["halls"][0]["tables"].append(dict(layout["halls"][0]["tables"][0]))
    result = import_layout_cli(slug, _write_layout(tmp_path, layout))
    assert result.returncode == 2
    assert "duplicate table number" in result.stderr


def test_import_updates_existing_table_geometry(api_client: TestClient, tmp_path: Path) -> None:
    slug, _login_name, token = _new_venue(api_client)
    assert (
        import_layout_cli(
            slug, _write_layout(tmp_path, _one_table_layout("Зал", capacity=4))
        ).returncode
        == 0
    )

    updated = _one_table_layout("Зал", capacity=8, width=200)
    assert import_layout_cli(slug, _write_layout(tmp_path, updated, "layout2.json")).returncode == 0

    tables = api_client.get(TABLES_URL, headers=cookie_header(token)).json()["tables"]
    assert tables[0]["capacity"] == 8
    assert tables[0]["width"] == 200


# --- auth / tenant isolation ----------------------------------------------


def test_halls_require_authentication(api_client: TestClient) -> None:
    assert api_client.get(HALLS_URL).status_code == 401
    assert api_client.get(TABLES_URL).status_code == 401


def test_hall_mutation_rejects_untrusted_origin(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    response = api_client.post(
        HALLS_URL,
        json={"name": "X", "canvas_width": 100, "canvas_height": 100},
        headers={**cookie_header(token), "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_hall_and_table_endpoints_are_tenant_isolated(
    api_client: TestClient, tmp_path: Path
) -> None:
    slug_a, _login_a, token_a = _new_venue(api_client)
    _slug_b, _login_b, token_b = _new_venue(api_client)
    assert (
        import_layout_cli(
            slug_a, _write_layout(tmp_path, _one_table_layout("Секретный зал"))
        ).returncode
        == 0
    )
    hall_a = _hall_id(api_client, token_a, "Секретный зал")
    table_a = api_client.get(TABLES_URL, headers=cookie_header(token_a)).json()["tables"][0]["id"]

    headers_b = {**cookie_header(token_b), "Origin": ORIGIN}
    # B never sees A's hall in its own list.
    assert "Секретный зал" not in {h["name"] for h in _halls(api_client, token_b)}
    # Every direct read/mutation of A's objects is a 404 for B (§7.1).
    assert (
        api_client.get(f"{HALLS_URL}/{hall_a}", headers=cookie_header(token_b)).status_code == 404
    )
    assert (
        api_client.patch(f"{HALLS_URL}/{hall_a}", json={"name": "x"}, headers=headers_b).status_code
        == 404
    )
    assert api_client.post(f"{HALLS_URL}/{hall_a}/archive", headers=headers_b).status_code == 404
    assert (
        api_client.patch(
            f"{TABLES_URL}/{table_a}", json={"is_bookable": False}, headers=headers_b
        ).status_code
        == 404
    )
    assert api_client.post(f"{TABLES_URL}/{table_a}/archive", headers=headers_b).status_code == 404
    # A is untouched: its hall still exists and still holds its table.
    detail = api_client.get(f"{HALLS_URL}/{hall_a}", headers=cookie_header(token_a)).json()
    assert [t["id"] for t in detail["tables"]] == [table_a]
