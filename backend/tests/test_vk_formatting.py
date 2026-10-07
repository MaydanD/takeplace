"""Unit tests for the VK message formatter (PROJECT-SPEC §38.6).

The formatter is pure, so these tests pin the exact message shape, the venue
timezone handling (including a night shift crossing midnight) and the
sanitisation of user-controlled strings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.integrations.vk.formatting import (
    BookingNotificationData,
    format_booking_notification,
    sanitize_user_text,
)

MSK = ZoneInfo("Europe/Moscow")


def _booking(
    *,
    starts_at: datetime,
    ends_at: datetime,
    name: str = "Виктор",
    party: int = 4,
    phone: str | None = "+7 (999) 123-45-67",
    tables: tuple[tuple[str, str], ...] = (("Основной зал", "5"),),
    number: int = 2999,
    timezone_name: str = "Europe/Moscow",
) -> BookingNotificationData:
    return BookingNotificationData(
        booking_number=number,
        starts_at=starts_at,
        ends_at=ends_at,
        guest_name=name,
        party_size=party,
        guest_phone_raw=phone,
        tables=tables,
        timezone=timezone_name,
    )


def test_message_matches_spec_example() -> None:
    starts = datetime(2026, 10, 2, 20, 0, tzinfo=MSK)
    ends = datetime(2026, 10, 2, 23, 0, tzinfo=MSK)
    message = format_booking_notification(_booking(starts_at=starts, ends_at=ends))
    assert message == (
        "🆕 Новая бронь №2999\n"
        "\n"
        "Основной зал · стол №5\n"
        "2 октября · 20:00–23:00\n"
        "\n"
        "Виктор\n"
        "4 гостя\n"
        "+7 (999) 123-45-67"
    )


def test_message_renders_in_venue_timezone_not_server() -> None:
    """A UTC instant must be shown as the venue wall clock (here MSK = UTC+3)."""
    starts = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)  # 20:00 MSK
    ends = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)  # 23:00 MSK
    message = format_booking_notification(_booking(starts_at=starts, ends_at=ends))
    assert "20:00–23:00" in message
    assert "2 октября" in message


def test_night_shift_crossing_midnight_keeps_local_times() -> None:
    """A booking after midnight still shows the venue-local date and time (§4)."""
    starts = datetime(2026, 10, 2, 23, 30, tzinfo=MSK)
    ends = datetime(2026, 10, 3, 1, 30, tzinfo=MSK)
    message = format_booking_notification(_booking(starts_at=starts, ends_at=ends))
    assert "2 октября · 23:30–01:30" in message


def test_multiple_tables_each_on_own_line() -> None:
    starts = datetime(2026, 10, 2, 20, 0, tzinfo=MSK)
    ends = datetime(2026, 10, 2, 22, 0, tzinfo=MSK)
    message = format_booking_notification(
        _booking(
            starts_at=starts,
            ends_at=ends,
            tables=(("Основной зал", "5"), ("Веранда", "12")),
        )
    )
    assert "Основной зал · стол №5\nВеранда · стол №12" in message


def test_phone_is_optional_for_sources_without_one() -> None:
    starts = datetime(2026, 10, 2, 20, 0, tzinfo=MSK)
    ends = datetime(2026, 10, 2, 22, 0, tzinfo=MSK)
    message = format_booking_notification(_booking(starts_at=starts, ends_at=ends, phone=None))
    assert message.endswith("4 гостя")
    assert message.count("\n") >= 4


def test_sanitize_strips_control_characters_and_collapses_whitespace() -> None:
    assert sanitize_user_text("Вик\u0000тор\n\n  Иванов") == "Виктор Иванов"


def test_sanitize_neutralises_vk_mention_and_markup() -> None:
    # '@', '[', ']' and '|' must not survive as-is: they can form mentions/links.
    cleaned = sanitize_user_text("@all [club1|hack]")
    assert "@" not in cleaned
    assert "[" not in cleaned and "]" not in cleaned
    assert "|" not in cleaned


def test_sanitize_caps_fragment_length() -> None:
    assert len(sanitize_user_text("x" * 500)) == 60


def test_malicious_guest_name_cannot_inject_markup() -> None:
    starts = datetime(2026, 10, 2, 20, 0, tzinfo=MSK)
    ends = datetime(2026, 10, 2, 22, 0, tzinfo=MSK)
    message = format_booking_notification(
        _booking(starts_at=starts, ends_at=ends, name="@all [club1|pwn]")
    )
    assert "@all" not in message
    assert "[club1|pwn]" not in message


def test_party_size_pluralisation() -> None:
    starts = datetime(2026, 10, 2, 20, 0, tzinfo=MSK)
    ends = datetime(2026, 10, 2, 22, 0, tzinfo=MSK)
    assert "1 гость" in format_booking_notification(
        _booking(starts_at=starts, ends_at=ends, party=1)
    )
    assert "2 гостя" in format_booking_notification(
        _booking(starts_at=starts, ends_at=ends, party=2)
    )
    assert "5 гостей" in format_booking_notification(
        _booking(starts_at=starts, ends_at=ends, party=5)
    )
    assert "11 гостей" in format_booking_notification(
        _booking(starts_at=starts, ends_at=ends, party=11)
    )
