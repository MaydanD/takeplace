"""Stage 6 integration tests: public booking API (PROJECT-SPEC §16–§18, §32.2, §34, §36, §40, §50).

Run against a real PostgreSQL migrated from zero. They cover the public venue
endpoint, the availability snapshot, the ONLINE booking create with idempotency,
the kill switch, honeypot, CAPTCHA seam, rate limiting, tenant isolation and
PII-safe responses.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.security.cookies import SESSION_COOKIE_NAME
from fastapi.testclient import TestClient

from tests.integration.support import (
    APP_URL,
    ME_URL,
    ORIGIN,
    PASSWORD,
    SCHEDULE_EXCEPTIONS_URL,
    SETTINGS_URL,
    TABLES_URL,
    cookie_header,
    create_venue,
    import_layout_cli,
    login,
    make_client,
    make_settings,
    unique,
)

pytestmark = pytest.mark.integration

MSK = ZoneInfo("Europe/Moscow")
PUBLIC = "/api/public/v1"


@pytest.fixture(autouse=True)
def _require_test_db() -> None:
    if not APP_URL:
        pytest.skip("TAKEPLACE_TEST_DATABASE_URL is not set")


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    with make_client() as client:
        yield client


# --- helpers ----------------------------------------------------------------


def _login(client: TestClient, login_name: str) -> str:
    response = login(client, login_name, PASSWORD)
    assert response.status_code == 200, response.text
    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token
    client.cookies.clear()
    return token


def _write(tmp_path: Path, payload: dict, name: str = "layout.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _layout(capacity: int = 4, tables: int = 3) -> dict:
    return {
        "halls": [
            {
                "name": "Зал",
                "canvas_width": 640,
                "canvas_height": 480,
                "tables": [
                    {
                        "number": str(i + 1),
                        "capacity": capacity,
                        "shape": "rect",
                        "x": 10 + i * 90,
                        "y": 10,
                        "width": 80,
                        "height": 80,
                    }
                    for i in range(tables)
                ],
            }
        ]
    }


class PublicVenue:
    """A venue with layout, open day and online booking enabled."""

    def __init__(self, client: TestClient, tmp_path: Path, *, capacity: int = 4, tables: int = 1):
        self.client = client
        self.slug, login_name = unique("venue"), unique("admin")
        assert create_venue(self.slug, login_name).returncode == 0
        self.token = _login(client, login_name)
        assert (
            import_layout_cli(self.slug, _write(tmp_path, _layout(capacity, tables))).returncode
            == 0
        )
        self.business_date = datetime.now(MSK).date() + timedelta(days=1)
        self._open_day(self.business_date)
        self._enable_online_booking()
        self.table_ids = [
            t["id"]
            for t in client.get(TABLES_URL, headers=cookie_header(self.token)).json()["tables"]
        ]
        self.venue_id = int(
            client.get(ME_URL, headers=cookie_header(self.token)).json()["venue"]["id"]
        )

    def headers(self, **extra: str) -> dict[str, str]:
        return {**cookie_header(self.token), "Origin": ORIGIN, **extra}

    def _open_day(self, business_date) -> None:
        response = self.client.put(
            f"{SCHEDULE_EXCEPTIONS_URL}/{business_date.isoformat()}",
            json={"is_closed": False, "open_time": "16:00", "close_time": "02:00"},
            headers=self.headers(),
            params={"confirm": "true"},
        )
        assert response.status_code == 200, response.text

    def _enable_online_booking(self) -> None:
        response = self.client.patch(
            SETTINGS_URL,
            json={"online_booking_enabled": True},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text

    def at(self, hour: int, minute: int = 0, *, day_offset: int = 0) -> str:
        day = self.business_date + timedelta(days=day_offset)
        return (
            datetime.combine(day, datetime.min.time(), tzinfo=MSK)
            .replace(hour=hour, minute=minute)
            .isoformat()
        )

    def create_booking(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        table_id: int | None = None,
        party_size: int = 2,
        name: str = "Guest",
        phone: str = "+79990000000",
        comment: str | None = None,
        privacy_version: str = "1.0.0",
        honeypot: str | None = None,
        captcha_token: str | None = None,
        key: str | None = None,
        expect: int = 201,
    ) -> TestClient:
        payload: dict = {
            "table_id": table_id or self.table_ids[0],
            "party_size": party_size,
            "starts_at": start or self.at(20),
            "ends_at": end or self.at(22),
            "guest_name": name,
            "guest_phone_raw": phone,
            "privacy_policy_version": privacy_version,
        }
        if comment is not None:
            payload["guest_comment"] = comment
        if honeypot is not None:
            payload["honeypot"] = honeypot
        if captcha_token is not None:
            payload["captcha_token"] = captcha_token
        response = self.client.post(
            f"{PUBLIC}/venues/{self.slug}/bookings",
            json=payload,
            headers={"Idempotency-Key": key or str(uuid.uuid4())},
        )
        assert response.status_code == expect, response.text
        return response


# --- GET /venues/{slug} -----------------------------------------------------


def test_get_public_venue(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=2)
    response = api_client.get(f"{PUBLIC}/venues/{venue.slug}")
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == venue.slug
    assert body["online_booking_enabled"] is True
    hall_names = {h["name"] for h in body["halls"]}
    assert "Зал" in hall_names
    imported_hall = next(h for h in body["halls"] if h["name"] == "Зал")
    assert len(imported_hall["tables"]) == 2


def test_get_public_venue_not_found(api_client: TestClient) -> None:
    response = api_client.get(f"{PUBLIC}/venues/no-such-venue")
    assert response.status_code == 404
    assert response.json()["code"] == "VENUE_NOT_FOUND"


def test_get_public_venue_rejects_malformed_slug(api_client: TestClient) -> None:
    response = api_client.get(f"{PUBLIC}/venues/has/slash")
    assert response.status_code == 404


def test_get_public_venue_no_pii(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    response = api_client.get(f"{PUBLIC}/venues/{venue.slug}")
    body = response.json()
    body_text = json.dumps(body)
    assert "address" not in body_text
    assert "phone" not in body_text


# --- GET /venues/{slug}/availability ----------------------------------------


def test_get_availability_returns_snapshot(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    response = api_client.get(
        f"{PUBLIC}/venues/{venue.slug}/availability",
        params={"business_date": venue.business_date.isoformat(), "party_size": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_open"] is True
    assert body["business_date"] == venue.business_date.isoformat()
    assert body["venue_timezone"] == "Europe/Moscow"
    assert len(body["tables"]) >= 1
    table = body["tables"][0]
    assert "slots" in table
    assert table["capacity"] == 4


def test_get_availability_closed_day(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    far_date = (datetime.now(MSK).date() + timedelta(days=30)).isoformat()
    response = api_client.get(
        f"{PUBLIC}/venues/{venue.slug}/availability",
        params={"business_date": far_date, "party_size": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_open"] is False
    assert body["tables"] == []


def test_get_availability_party_size_filters_by_capacity(
    api_client: TestClient, tmp_path: Path
) -> None:
    venue = PublicVenue(api_client, tmp_path, capacity=2, tables=1)
    response = api_client.get(
        f"{PUBLIC}/venues/{venue.slug}/availability",
        params={"business_date": venue.business_date.isoformat(), "party_size": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tables"] == []


def test_get_availability_venue_not_found(api_client: TestClient) -> None:
    response = api_client.get(f"{PUBLIC}/venues/no-such/availability")
    assert response.status_code == 404


# --- POST /venues/{slug}/bookings -------------------------------------------


def test_create_public_booking_happy_path(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    response = venue.create_booking()
    body = response.json()
    assert body["number"] == 1
    assert body["source"] == "ONLINE"
    assert body["party_size"] == 2
    assert body["table_ids"] == [venue.table_ids[0]]
    assert body["business_date"] == venue.business_date.isoformat()


def test_public_booking_response_has_no_pii(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    response = venue.create_booking(name="John Doe", phone="+79991234567", comment="Window table")
    body_text = response.text
    assert "John Doe" not in body_text
    assert "+79991234567" not in body_text
    assert "Window table" not in body_text


def test_public_booking_idempotent_replay(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    key = str(uuid.uuid4())
    first = venue.create_booking(key=key)
    second = venue.create_booking(key=key, expect=200)
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["number"] == second.json()["number"]


def test_public_booking_stores_the_canonical_ip_fingerprint(
    api_client: TestClient, tmp_path: Path
) -> None:
    # Audit FIX-01: the persisted ``request_ip_hmac`` must be derived from the
    # canonical client IP (via ``canonical_client_ip``), not the raw
    # ``request.client.host`` string. The TestClient host is ``testclient``,
    # which is not an IP, so the canonical value is the shared unknown marker.
    from asyncio import run

    from app.security.client_ip import UNKNOWN_CLIENT_IP
    from app.security.tokens import hmac_sha256_hex
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    venue = PublicVenue(api_client, tmp_path)
    booking = venue.create_booking().json()
    settings = make_settings()
    expected = hmac_sha256_hex(settings.abuse_hmac_key, f"{venue.venue_id}:{UNKNOWN_CLIENT_IP}")

    async def _stored() -> tuple[str | None, object]:
        engine = create_async_engine(APP_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT request_ip_hmac, request_ip_hmac_expires_at "
                        "FROM bookings WHERE id = :id"
                    ),
                    {"id": booking["id"]},
                )
            ).one()
        await engine.dispose()
        return row[0], row[1]

    stored_hmac, expires_at = run(_stored())
    assert stored_hmac == expected
    assert stored_hmac != hmac_sha256_hex(settings.abuse_hmac_key, f"{venue.venue_id}:testclient")
    assert expires_at is not None


def test_public_booking_idempotency_key_reused(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    key = str(uuid.uuid4())
    venue.create_booking(key=key, name="Alice", phone="+79990000001")
    response = venue.create_booking(key=key, name="Bob", phone="+79990000002", expect=409)
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_public_booking_honeypot_rejected(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    venue.create_booking(honeypot="bot-trap", expect=400)


def test_public_booking_missing_idempotency_key(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    payload = {
        "table_id": venue.table_ids[0],
        "party_size": 2,
        "starts_at": venue.at(20),
        "ends_at": venue.at(22),
        "guest_name": "Guest",
        "guest_phone_raw": "+79990000000",
        "privacy_policy_version": "1.0.0",
    }
    response = api_client.post(f"{PUBLIC}/venues/{venue.slug}/bookings", json=payload)
    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_MISSING"


def test_public_booking_invalid_idempotency_key(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    payload = {
        "table_id": venue.table_ids[0],
        "party_size": 2,
        "starts_at": venue.at(20),
        "ends_at": venue.at(22),
        "guest_name": "Guest",
        "guest_phone_raw": "+79990000000",
        "privacy_policy_version": "1.0.0",
    }
    response = api_client.post(
        f"{PUBLIC}/venues/{venue.slug}/bookings",
        json=payload,
        headers={"Idempotency-Key": "not-a-uuid"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"


def test_public_booking_kill_switch_off(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    response = api_client.patch(
        SETTINGS_URL,
        json={"online_booking_enabled": False},
        headers=venue.headers(),
    )
    assert response.status_code == 200
    venue.create_booking(expect=409)


def test_public_booking_venue_not_found(api_client: TestClient) -> None:
    payload = {
        "table_id": 1,
        "party_size": 2,
        "starts_at": "2026-10-05T20:00:00+03:00",
        "ends_at": "2026-10-05T22:00:00+03:00",
        "guest_name": "Guest",
        "guest_phone_raw": "+79990000000",
        "privacy_policy_version": "1.0.0",
    }
    response = api_client.post(
        f"{PUBLIC}/venues/no-such-venue/bookings",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 404


def test_public_booking_conflict(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path, tables=1)
    venue.create_booking(start=venue.at(20), end=venue.at(22))
    venue.create_booking(start=venue.at(20), end=venue.at(22), expect=409)


def test_public_booking_insufficient_capacity(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path, capacity=2, tables=1)
    venue.create_booking(party_size=5, expect=422)


def test_public_booking_minimum_duration(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    venue.create_booking(start=venue.at(20), end=venue.at(20, 20), expect=422)


def test_public_booking_outside_shift(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    venue.create_booking(start=venue.at(10), end=venue.at(12), expect=422)


def test_public_booking_closed_day(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    far_day = venue.business_date + timedelta(days=30)
    start = datetime.combine(far_day, datetime.min.time(), tzinfo=MSK).replace(hour=20).isoformat()
    end = datetime.combine(far_day, datetime.min.time(), tzinfo=MSK).replace(hour=22).isoformat()
    venue.create_booking(start=start, end=end, expect=422)


# --- tenant isolation -------------------------------------------------------


def test_public_tenant_isolation(api_client: TestClient, tmp_path: Path) -> None:
    venue_a = PublicVenue(api_client, tmp_path)
    venue_b = PublicVenue(api_client, tmp_path)
    response_a = api_client.get(f"{PUBLIC}/venues/{venue_a.slug}")
    response_b = api_client.get(f"{PUBLIC}/venues/{venue_b.slug}")
    assert response_a.json()["slug"] == venue_a.slug
    assert response_b.json()["slug"] == venue_b.slug
    assert response_a.json()["slug"] != response_b.json()["slug"]


def test_public_booking_does_not_leak_across_venues(api_client: TestClient, tmp_path: Path) -> None:
    venue_a = PublicVenue(api_client, tmp_path)
    venue_b = PublicVenue(api_client, tmp_path)
    key = str(uuid.uuid4())
    first = venue_a.create_booking(key=key)
    second = venue_b.create_booking(key=key)
    assert first.json()["id"] != second.json()["id"]


# --- CAPTCHA seam -----------------------------------------------------------


def test_captcha_disabled_by_default(api_client: TestClient, tmp_path: Path) -> None:
    venue = PublicVenue(api_client, tmp_path)
    response = venue.create_booking(captcha_token=None)
    assert response.status_code == 201


def test_missing_public_body_is_validation_error(api_client: TestClient) -> None:
    response = api_client.post(
        f"{PUBLIC}/venues/example/bookings", headers={"Idempotency-Key": str(uuid.uuid4())}
    )
    assert response.status_code == 422


def test_conflict_does_not_disclose_other_booking_ids(
    api_client: TestClient, tmp_path: Path
) -> None:
    venue = PublicVenue(api_client, tmp_path)
    venue.create_booking()
    response = venue.create_booking(expect=409)
    assert response.json() == {
        "code": "BOOKING_CONFLICT",
        "detail": "the selected time is no longer available",
    }


def test_abuse_status_is_tenant_scoped(api_client: TestClient, tmp_path: Path) -> None:
    from app.services.public_rate_limit import get_public_rate_limiters
    from app.settings import get_settings

    venue = PublicVenue(api_client, tmp_path)
    other = PublicVenue(api_client, tmp_path)
    for _ in range(get_settings().public_abuse_threshold):
        get_public_rate_limiters().record_create(venue.venue_id)
    assert api_client.get("/health/ops").json()["online_abuse_alerts"] == 1
    assert api_client.get("/api/admin/v1/system/status", headers=venue.headers()).json() == {
        "online_abuse_alert": True,
        "outbox_unacknowledged_dead": 0,
    }
    assert api_client.get("/api/admin/v1/system/status", headers=other.headers()).json() == {
        "online_abuse_alert": False,
        "outbox_unacknowledged_dead": 0,
    }
    assert api_client.get("/api/admin/v1/system/status").status_code == 401
