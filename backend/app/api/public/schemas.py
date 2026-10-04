"""Public API schemas (PROJECT-SPEC §34, §50).

Transport types are generated from these models for the frontend. Public
responses never expose PII, admin data or internal config (§34, §36).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field

from app.db.models import Hall, Table, Venue
from app.domain.layout import StaticElement
from app.services.bookings import BookingView

# ---------------------------------------------------------------------------
# Public venue
# ---------------------------------------------------------------------------


class PublicTable(BaseModel):
    """Public table geometry (no archive/internal fields, §34)."""

    id: int
    hall_id: int
    number: str
    capacity: int
    x: float
    y: float
    width: float
    height: float
    rotation: float
    shape: str
    z_index: int

    @classmethod
    def from_model(cls, table: Table) -> PublicTable:
        return cls(
            id=table.id,
            hall_id=table.hall_id,
            number=table.number,
            capacity=table.capacity,
            x=float(table.x),
            y=float(table.y),
            width=float(table.width),
            height=float(table.height),
            rotation=float(table.rotation),
            shape=table.shape,
            z_index=table.z_index,
        )


class PublicHall(BaseModel):
    """Public hall with canvas geometry, static elements and active tables."""

    id: int
    name: str
    is_bookable: bool
    canvas_width: int
    canvas_height: int
    static_elements: list[StaticElement]
    tables: list[PublicTable]

    @classmethod
    def from_model(cls, hall: Hall, tables: list[Table]) -> PublicHall:
        return cls(
            id=hall.id,
            name=hall.name,
            is_bookable=hall.is_bookable,
            canvas_width=hall.canvas_width,
            canvas_height=hall.canvas_height,
            static_elements=hall.static_elements,
            tables=[PublicTable.from_model(t) for t in tables],
        )


class PublicVenueResponse(BaseModel):
    """Public venue data for the booking UI (§34, §50)."""

    slug: str
    name: str
    timezone: str
    is_active: bool
    online_booking_enabled: bool
    privacy_policy_version: str
    halls: list[PublicHall]

    @classmethod
    def from_venue(
        cls, venue: Venue, halls: list[PublicHall], privacy_policy_version: str
    ) -> PublicVenueResponse:
        return cls(
            slug=venue.slug,
            name=venue.name,
            timezone=venue.timezone,
            is_active=venue.is_active,
            online_booking_enabled=venue.online_booking_enabled,
            privacy_policy_version=privacy_policy_version,
            halls=halls,
        )


# ---------------------------------------------------------------------------
# Public availability
# ---------------------------------------------------------------------------


class PublicAvailabilitySlot(BaseModel):
    start: str
    earliest_end: str
    latest_end: str
    end_options: list[str]


class PublicAvailabilityTable(BaseModel):
    id: int
    number: str
    capacity: int
    hall_id: int
    hall_name: str
    slots: list[PublicAvailabilitySlot]


class PublicAvailabilityResponse(BaseModel):
    """Read-only availability snapshot (§16, §34)."""

    business_date: date
    venue_timezone: str
    shift_start: str | None
    shift_end: str | None
    is_open: bool
    tables: list[PublicAvailabilityTable]


# ---------------------------------------------------------------------------
# Public booking create
# ---------------------------------------------------------------------------


class PublicCreateRequest(BaseModel):
    """Public ONLINE booking create payload (§18, §42).

    The ``honeypot`` field is hidden in the UI and must be empty for a normal
    user (§40, §50). ``captcha_token`` is only required when the CAPTCHA feature
    flag is enabled.
    """

    table_id: int
    party_size: int = Field(ge=1, le=200)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    guest_name: str = Field(min_length=1, max_length=100)
    guest_phone_raw: str = Field(min_length=1, max_length=50)
    guest_comment: str | None = Field(default=None, max_length=1000)
    privacy_policy_version: str = Field(min_length=1, max_length=50)
    honeypot: str | None = None
    captcha_token: str | None = None


class PublicCreateResponse(BaseModel):
    """Public booking confirmation (§50).

    Shows only what the success screen needs: booking number, venue, date, time,
    hall, table, party size. No guest name/phone/comment is returned to avoid
    leaking PII back through the public response (§36, §42).
    """

    id: int
    number: int
    business_date: date
    shift_starts_at: datetime
    shift_ends_at: datetime
    starts_at: datetime
    ends_at: datetime
    party_size: int
    table_ids: list[int]
    source: str

    @classmethod
    def from_view(cls, view: BookingView) -> PublicCreateResponse:
        booking = view.booking
        return cls(
            id=booking.id,
            number=booking.number,
            business_date=booking.business_date,
            shift_starts_at=booking.shift_starts_at,
            shift_ends_at=booking.shift_ends_at,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            party_size=booking.party_size,
            table_ids=view.table_ids,
            source=booking.source,
        )


# Re-export the availability dataclass-to-schema mapper payload type so the API
# layer can pass the service result straight through without re-defining it.
PublicAvailabilityPayload = dict[str, Any]
