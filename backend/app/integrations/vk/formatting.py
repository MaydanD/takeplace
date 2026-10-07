"""VK message formatting (PROJECT-SPEC §38.6).

A pure, testable layer: it turns an already-loaded booking (plus its venue, plan
tables and their halls) into the message body sent to the staff conversation. It
never touches the database, the HTTP client or the worker lifecycle, so the exact
text can be asserted deterministically in unit tests.

Formatting rules:

* dates/times are rendered in the *venue* timezone, never the server's, so a night
  shift that crosses midnight still shows the correct wall-clock time (§4, §51);
* user-controlled strings (guest name, hall/table numbers) are length-capped,
  stripped of control characters and neutralised so they cannot become VK
  mention/markup (§38.6);
* only data the spec allows appears: booking number, hall/table, date, time,
  guest name, party size and phone (§38.6 example). No comment, no internal ids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# Cap for any user-controlled fragment embedded in the message (§38.6).
MAX_FIELD_LENGTH = 60
# Safety bound on the whole message body.
MAX_MESSAGE_LENGTH = 1000

# VK treats ``@``/``[``/``]``/``(``/``)`` specially (mentions, links, markup).
# Replace them with visually similar neutral characters so guest text cannot
# produce a mention or a link the staff did not intend (§38.6).
_MARKUP_MAP = str.maketrans(
    {
        "@": "＠",
        "[": "(",
        "]": ")",
        "|": "｜",
    }
)
# C0/C1 control characters except a normal space.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_WHITESPACE_RE = re.compile(r"\s+")

_MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


@dataclass(frozen=True, slots=True)
class BookingNotificationData:
    """Everything the formatter needs, already resolved by the caller.

    ``tables`` pairs a hall name with a table number, both already sorted in the
    canonical order used elsewhere. The guest phone may be ``None`` for a source
    that does not require one (§6.6).
    """

    booking_number: int
    starts_at: datetime
    ends_at: datetime
    guest_name: str
    party_size: int
    guest_phone_raw: str | None
    tables: tuple[tuple[str, str], ...]
    timezone: str


def sanitize_user_text(value: str, *, limit: int = MAX_FIELD_LENGTH) -> str:
    """Neutralise a user-controlled fragment for safe embedding (§38.6).

    Control characters are dropped, runs of whitespace collapse to one space,
    VK-special characters are replaced and the result is length-capped. An empty
    result becomes a single space-free placeholder is *not* used — callers decide
    how to render an empty field.
    """
    cleaned = _CONTROL_RE.sub("", value)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = cleaned.translate(_MARKUP_MAP)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    return cleaned


def _format_date(starts_at: datetime, tz: ZoneInfo) -> str:
    local = starts_at.astimezone(tz)
    return f"{local.day} {_MONTHS_RU[local.month - 1]}"


def _format_time_range(starts_at: datetime, ends_at: datetime, tz: ZoneInfo) -> str:
    start = starts_at.astimezone(tz)
    end = ends_at.astimezone(tz)
    return f"{start:%H:%M}–{end:%H:%M}"


def _format_tables(tables: tuple[tuple[str, str], ...]) -> str:
    parts = [
        f"{sanitize_user_text(hall)} · стол №{sanitize_user_text(number)}"
        for hall, number in tables
    ]
    return "\n".join(parts)


def format_booking_notification(data: BookingNotificationData) -> str:
    """Render the staff-conversation message for a new ONLINE booking (§38.6).

    Example::

        🆕 Новая бронь №2999

        Основной зал · стол №5
        2 октября · 20:00–23:00

        Виктор
        4 гостя
        +7 ...
    """
    tz = ZoneInfo(data.timezone)
    lines: list[str] = [f"🆕 Новая бронь №{data.booking_number}", ""]

    tables = _format_tables(data.tables) if data.tables else ""
    if tables:
        lines.append(tables)
    lines.append(
        f"{_format_date(data.starts_at, tz)} · {_format_time_range(data.starts_at, data.ends_at, tz)}"
    )

    lines.append("")
    name = sanitize_user_text(data.guest_name)
    if name:
        lines.append(name)
    lines.append(_party_line(data.party_size))
    if data.guest_phone_raw:
        phone = sanitize_user_text(data.guest_phone_raw)
        if phone:
            lines.append(phone)

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH].rstrip()
    return message


def _party_line(party_size: int) -> str:
    """Russian pluralisation of the guest count (1 гость, 2 гостя, 5 гостей)."""
    if party_size % 10 == 1 and party_size % 100 != 11:
        word = "гость"
    elif party_size % 10 in (2, 3, 4) and party_size % 100 not in (12, 13, 14):
        word = "гостя"
    else:
        word = "гостей"
    return f"{party_size} {word}"
