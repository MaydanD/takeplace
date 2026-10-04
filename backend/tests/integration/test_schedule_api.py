"""Stage 3 integration tests: weekly schedule, exceptions, business day.

Skipped unless ``TAKEPLACE_TEST_DATABASE_URL`` is set. They run against a real
PostgreSQL database migrated from zero and cover the Stage 3 acceptance
criterion and PROJECT-SPEC §5, §7, §44:

* weekly schedule CRUD and closed days;
* 5-minute grid validation and invalid/equal times;
* adjacent-shift (including overnight and Sunday→Monday) conflicts;
* schedule exceptions overriding, closing and reverting to the weekly rule;
* the computed business-day state, including overnight shifts and venue tz;
* tenant isolation (one venue cannot read or change another's schedule);
* the database rejecting grid-violating rows (CHECK as last arbiter).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from app.security.cookies import SESSION_COOKIE_NAME
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.support import (
    APP_URL,
    BUSINESS_DAY_URL,
    ME_URL,
    ORIGIN,
    PASSWORD,
    SCHEDULE_EXCEPTIONS_URL,
    SCHEDULE_URL,
    cookie_header,
    create_venue,
    login,
    make_client,
    unique,
)

pytestmark = pytest.mark.integration

# Known dates: 2026-10-05 is a Monday.
MONDAY = "2026-10-05"
TUESDAY = "2026-10-06"
WEDNESDAY = "2026-10-07"
FRIDAY = "2026-10-09"
SUNDAY = "2026-10-11"


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


def _closed(weekday: int) -> dict[str, object]:
    return {"weekday": weekday, "is_open": False, "open_time": None, "close_time": None}


def _open(weekday: int, open_time: str, close_time: str) -> dict[str, object]:
    return {"weekday": weekday, "is_open": True, "open_time": open_time, "close_time": close_time}


def _weekdays(**overrides: tuple[str, str]) -> list[dict[str, object]]:
    """Seven weekdays, all closed except the numeric keys in ``overrides``."""
    days = [_closed(w) for w in range(7)]
    for weekday, (open_time, close_time) in overrides.items():
        days[int(weekday)] = _open(int(weekday), open_time, close_time)
    return days


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


def _put_schedule(client: TestClient, token: str, weekdays: list[dict[str, object]]):
    return client.put(
        SCHEDULE_URL,
        json={"weekdays": weekdays},
        headers={**cookie_header(token), "Origin": ORIGIN},
    )


def _put_exception(client: TestClient, token: str, day: str, body: dict[str, object]):
    return client.put(
        f"{SCHEDULE_EXCEPTIONS_URL}/{day}",
        json=body,
        headers={**cookie_header(token), "Origin": ORIGIN},
    )


def _business_day(client: TestClient, token: str, day: str) -> dict[str, object]:
    return client.get(
        BUSINESS_DAY_URL, params={"business_date": day}, headers=cookie_header(token)
    ).json()


# --- weekly schedule CRUD --------------------------------------------------


def test_new_venue_starts_with_seven_closed_days(api_client: TestClient) -> None:
    _slug, login_name, token = _new_venue(api_client)

    response = api_client.get(SCHEDULE_URL, headers=cookie_header(token))
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Europe/Moscow"
    assert [d["weekday"] for d in body["weekdays"]] == list(range(7))
    assert all(d["is_open"] is False for d in body["weekdays"])
    assert all(d["open_time"] is None and d["close_time"] is None for d in body["weekdays"])
    assert login_name  # the venue really exists and we logged in


def test_weekly_schedule_round_trips(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)

    put = _put_schedule(
        api_client, token, _weekdays(**{"0": ("10:00", "22:00"), "5": ("16:00", "02:00")})
    )
    assert put.status_code == 200, put.text

    body = api_client.get(SCHEDULE_URL, headers=cookie_header(token)).json()
    days = {d["weekday"]: d for d in body["weekdays"]}
    assert days[0] == {"weekday": 0, "is_open": True, "open_time": "10:00", "close_time": "22:00"}
    # Overnight is encoded purely as close_time < open_time; no extra field.
    assert days[5]["open_time"] == "16:00"
    assert days[5]["close_time"] == "02:00"
    assert days[1]["is_open"] is False


def test_weekly_schedule_requires_all_seven_days(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    response = _put_schedule(api_client, token, [_closed(0), _closed(1)])
    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_INVALID"


def test_closed_day_reports_no_shift(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    # Only Monday is open.
    assert (
        _put_schedule(api_client, token, _weekdays(**{"0": ("10:00", "22:00")})).status_code == 200
    )

    closed = _business_day(api_client, token, TUESDAY)
    assert closed["is_open"] is False
    assert closed["shift_start"] is None and closed["shift_end"] is None


def test_daytime_shift_is_resolved_for_the_business_date(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    assert (
        _put_schedule(api_client, token, _weekdays(**{"0": ("10:00", "22:00")})).status_code == 200
    )

    body = _business_day(api_client, token, MONDAY)
    assert body["business_date"] == MONDAY
    assert body["is_open"] is True
    assert body["shift_start"] == "2026-10-05T10:00:00+03:00"
    assert body["shift_end"] == "2026-10-05T22:00:00+03:00"


def test_overnight_shift_ends_the_next_calendar_day(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    assert (
        _put_schedule(api_client, token, _weekdays(**{"4": ("16:00", "02:00")})).status_code == 200
    )

    body = _business_day(api_client, token, FRIDAY)
    assert body["business_date"] == FRIDAY
    assert body["shift_start"] == "2026-10-09T16:00:00+03:00"
    # 02:00 on the next day still belongs to Friday's business date.
    assert body["shift_end"] == "2026-10-10T02:00:00+03:00"


def test_shift_bounds_are_rendered_in_the_venue_timezone(api_client: TestClient) -> None:
    # Yekaterinburg is UTC+5 and has no DST; a Moscow venue is UTC+3.
    _slug, _login_name, token = _new_venue(api_client, tz="Asia/Yekaterinburg")
    assert (
        _put_schedule(api_client, token, _weekdays(**{"0": ("10:00", "22:00")})).status_code == 200
    )
    body = _business_day(api_client, token, MONDAY)
    assert body["shift_start"] == "2026-10-05T10:00:00+05:00"


def test_business_day_without_date_reports_current_business_date(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    body = api_client.get(BUSINESS_DAY_URL, headers=cookie_header(token)).json()
    assert body["business_date"] == body["current_business_date"]
    assert body["timezone"] == "Europe/Moscow"


# --- grid / invalid time validation ----------------------------------------


@pytest.mark.parametrize(
    "open_time,close_time",
    [
        ("10:03", "22:00"),  # minute not a multiple of 5
        ("10:00", "22:07"),  # minute not a multiple of 5
        ("10:00", "10:00"),  # equal times
    ],
)
def test_invalid_weekly_times_are_rejected(
    api_client: TestClient, open_time: str, close_time: str
) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    response = _put_schedule(api_client, token, _weekdays(**{"0": (open_time, close_time)}))
    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_INVALID"


def test_open_day_without_times_is_rejected(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    days = _weekdays()
    days[0] = {"weekday": 0, "is_open": True, "open_time": None, "close_time": None}
    response = _put_schedule(api_client, token, days)
    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_INVALID"


def test_weekday_out_of_range_is_rejected(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    days = _weekdays()
    days.append(_open(7, "10:00", "22:00"))
    response = _put_schedule(api_client, token, days)
    assert response.status_code == 422


# --- adjacent-shift overlap ------------------------------------------------


def test_weekly_adjacent_overnight_overlap_is_rejected(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    # Friday 16:00 -> Sat 04:00 and Saturday 03:00 -> Sun 02:00 overlap (§5.4).
    response = _put_schedule(
        api_client, token, _weekdays(**{"4": ("16:00", "04:00"), "5": ("03:00", "02:00")})
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OVERLAP"


def test_weekly_valid_overnight_neighbours_are_accepted(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    response = _put_schedule(
        api_client, token, _weekdays(**{"4": ("16:00", "02:00"), "5": ("10:00", "22:00")})
    )
    assert response.status_code == 200, response.text


def test_sunday_monday_wrap_overlap_is_rejected(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    # Sunday 22:00 -> Monday 06:00 would overlap Monday 05:00 opening.
    response = _put_schedule(
        api_client, token, _weekdays(**{"6": ("22:00", "06:00"), "0": ("05:00", "12:00")})
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OVERLAP"


def test_exception_conflicting_with_neighbour_is_rejected(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    # Tuesday is open 10:00->22:00 weekly.
    assert (
        _put_schedule(api_client, token, _weekdays(**{"1": ("10:00", "22:00")})).status_code == 200
    )
    # A Monday exception running to Tuesday 12:00 collides with Tuesday's start.
    response = _put_exception(
        api_client, token, MONDAY, {"is_closed": False, "open_time": "20:00", "close_time": "12:00"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OVERLAP"


# --- exceptions ------------------------------------------------------------


def test_exception_overrides_weekly_times(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    assert (
        _put_schedule(api_client, token, _weekdays(**{"0": ("10:00", "22:00")})).status_code == 200
    )

    put = _put_exception(
        api_client, token, MONDAY, {"is_closed": False, "open_time": "12:00", "close_time": "18:00"}
    )
    assert put.status_code == 200, put.text
    assert put.json() == {
        "date": MONDAY,
        "is_closed": False,
        "open_time": "12:00",
        "close_time": "18:00",
    }

    body = _business_day(api_client, token, MONDAY)
    assert body["shift_start"] == "2026-10-05T12:00:00+03:00"
    assert body["shift_end"] == "2026-10-05T18:00:00+03:00"


def test_exception_closes_an_otherwise_open_day(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    assert (
        _put_schedule(api_client, token, _weekdays(**{"0": ("10:00", "22:00")})).status_code == 200
    )
    assert _put_exception(api_client, token, MONDAY, {"is_closed": True}).status_code == 200

    body = _business_day(api_client, token, MONDAY)
    assert body["is_open"] is False


def test_overnight_exception_is_supported(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    assert _put_schedule(api_client, token, _weekdays()).status_code == 200
    put = _put_exception(
        api_client, token, SUNDAY, {"is_closed": False, "open_time": "22:00", "close_time": "04:00"}
    )
    assert put.status_code == 200, put.text

    body = _business_day(api_client, token, SUNDAY)
    assert body["shift_start"] == "2026-10-11T22:00:00+03:00"
    assert body["shift_end"] == "2026-10-12T04:00:00+03:00"


def test_exception_delete_falls_back_to_weekly(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    assert (
        _put_schedule(api_client, token, _weekdays(**{"0": ("10:00", "22:00")})).status_code == 200
    )
    assert _put_exception(api_client, token, MONDAY, {"is_closed": True}).status_code == 200

    deleted = api_client.delete(
        f"{SCHEDULE_EXCEPTIONS_URL}/{MONDAY}",
        headers={**cookie_header(token), "Origin": ORIGIN},
    )
    assert deleted.status_code == 204

    body = _business_day(api_client, token, MONDAY)
    assert body["is_open"] is True
    assert body["shift_start"] == "2026-10-05T10:00:00+03:00"

    exceptions = api_client.get(SCHEDULE_EXCEPTIONS_URL, headers=cookie_header(token)).json()
    assert exceptions["exceptions"] == []


def test_delete_missing_exception_returns_404(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    response = api_client.delete(
        f"{SCHEDULE_EXCEPTIONS_URL}/{WEDNESDAY}",
        headers={**cookie_header(token), "Origin": ORIGIN},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "SCHEDULE_EXCEPTION_NOT_FOUND"


def test_exception_is_validated_against_grid(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    response = _put_exception(
        api_client, token, MONDAY, {"is_closed": False, "open_time": "12:03", "close_time": "18:00"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_INVALID"


# --- auth / CSRF / tenant isolation ----------------------------------------


def test_schedule_requires_authentication(api_client: TestClient) -> None:
    assert api_client.get(SCHEDULE_URL).status_code == 401
    assert api_client.get(SCHEDULE_EXCEPTIONS_URL).status_code == 401


def test_schedule_mutation_rejects_untrusted_origin(api_client: TestClient) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    response = api_client.put(
        SCHEDULE_URL,
        json={"weekdays": _weekdays(**{"0": ("10:00", "22:00")})},
        headers={**cookie_header(token), "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_ORIGIN_REJECTED"


def test_schedule_is_tenant_isolated(api_client: TestClient) -> None:
    _slug_a, _login_a, token_a = _new_venue(api_client)
    _slug_b, _login_b, token_b = _new_venue(api_client)

    # A opens Monday.
    assert (
        _put_schedule(api_client, token_a, _weekdays(**{"0": ("10:00", "22:00")})).status_code
        == 200
    )
    # B stays closed.
    b_schedule = api_client.get(SCHEDULE_URL, headers=cookie_header(token_b)).json()
    assert all(d["is_open"] is False for d in b_schedule["weekdays"])

    # A's exception must not appear in B's list.
    assert _put_exception(api_client, token_a, MONDAY, {"is_closed": True}).status_code == 200
    assert (
        api_client.get(SCHEDULE_EXCEPTIONS_URL, headers=cookie_header(token_b)).json()["exceptions"]
        == []
    )
    # A still sees its own schedule.
    assert (
        api_client.get(SCHEDULE_URL, headers=cookie_header(token_a)).json()["weekdays"][0][
            "is_open"
        ]
        is True
    )


def test_schedule_payload_cannot_retarget_another_venue(api_client: TestClient) -> None:
    _slug_a, _login_a, token_a = _new_venue(api_client)
    _slug_b, _login_b, token_b = _new_venue(api_client)

    # A smuggled venue_id in the body is ignored: the venue from the session wins.
    response = api_client.put(
        SCHEDULE_URL,
        json={"weekdays": _weekdays(**{"0": ("10:00", "22:00")}), "venue_id": 999999},
        headers={**cookie_header(token_a), "Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text

    b_schedule = api_client.get(SCHEDULE_URL, headers=cookie_header(token_b)).json()
    assert all(d["is_open"] is False for d in b_schedule["weekdays"])


# --- database is the last arbiter ------------------------------------------


async def _venue_id(client: TestClient, token: str) -> int:
    return int(client.get(ME_URL, headers=cookie_header(token)).json()["venue"]["id"])


async def test_database_rejects_non_grid_open_time(
    api_client: TestClient, app_session: AsyncSession
) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    venue_id = await _venue_id(api_client, token)

    # Both times present and different, but the minute is off the 5-minute grid.
    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "UPDATE weekly_schedules SET is_open = true, open_time = '10:03', "
                "close_time = '22:00' WHERE venue_id = :venue_id AND weekday = 0"
            ),
            {"venue_id": venue_id},
        )
    assert "ck_weekly_schedules_consistency" in str(excinfo.value)
    await app_session.rollback()


async def test_database_rejects_out_of_range_weekday(
    api_client: TestClient, app_session: AsyncSession
) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    venue_id = await _venue_id(api_client, token)

    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "UPDATE weekly_schedules SET weekday = 7 WHERE venue_id = :venue_id AND weekday = 0"
            ),
            {"venue_id": venue_id},
        )
    assert "ck_weekly_schedules_weekday_bounds" in str(excinfo.value)
    await app_session.rollback()


async def test_database_rejects_inconsistent_exception(
    api_client: TestClient, app_session: AsyncSession
) -> None:
    _slug, _login_name, token = _new_venue(api_client)
    venue_id = await _venue_id(api_client, token)

    # is_closed=true must not carry times.
    with pytest.raises(IntegrityError) as excinfo:
        await app_session.execute(
            text(
                "INSERT INTO schedule_exceptions "
                "(venue_id, date, is_closed, open_time, close_time, created_at, updated_at) "
                "VALUES (:venue_id, '2026-10-05', true, '10:00', '22:00', now(), now())"
            ),
            {"venue_id": venue_id},
        )
    assert "ck_schedule_exceptions_consistency" in str(excinfo.value)
    await app_session.rollback()
