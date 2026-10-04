"""Admin API schemas.

Transport types are generated from these models for the frontend.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.db.models import AdminAccount, Venue

# Local schedule times are wall-clock ``HH:MM`` values in the venue timezone, on
# the 5-minute grid (§5.2). They are never ambiguous instants: the business date
# supplies the calendar day and the shift may cross midnight.
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class AdminSummary(BaseModel):
    """The authenticated admin account. Never contains the password hash."""

    id: int
    login: str
    is_active: bool

    @classmethod
    def from_model(cls, admin: AdminAccount) -> AdminSummary:
        return cls(id=admin.id, login=admin.login, is_active=admin.is_active)


class VenueSummary(BaseModel):
    """Public identity of the tenant the session belongs to."""

    id: int
    slug: str
    name: str
    address: str | None
    phone: str | None
    timezone: str
    is_active: bool
    online_booking_enabled: bool

    @classmethod
    def from_model(cls, venue: Venue) -> VenueSummary:
        return cls(
            id=venue.id,
            slug=venue.slug,
            name=venue.name,
            address=venue.address,
            phone=venue.phone,
            timezone=venue.timezone,
            is_active=venue.is_active,
            online_booking_enabled=venue.online_booking_enabled,
        )


class MeResponse(BaseModel):
    admin: AdminSummary
    venue: VenueSummary


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class StatusResponse(BaseModel):
    status: str


class SettingsUpdate(BaseModel):
    """Editable venue settings.

    ``slug``, ``timezone`` and ``is_active`` are intentionally absent: slug and
    timezone are not runtime-editable in v1, and suspension is a CLI operation.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=50)
    online_booking_enabled: bool | None = None


class ScheduleDay(BaseModel):
    """One weekday rule. ``is_open=false`` means the day is closed."""

    weekday: int = Field(ge=0, le=6)
    is_open: bool
    open_time: str | None = Field(default=None, pattern=_TIME_PATTERN)
    close_time: str | None = Field(default=None, pattern=_TIME_PATTERN)


class WeeklyScheduleResponse(BaseModel):
    timezone: str
    weekdays: list[ScheduleDay]


class WeeklyScheduleUpdate(BaseModel):
    """Full replacement of the weekly schedule (all seven weekdays)."""

    weekdays: list[ScheduleDay] = Field(min_length=1, max_length=7)


class ScheduleExceptionEntry(BaseModel):
    date: date
    is_closed: bool
    open_time: str | None
    close_time: str | None


class ScheduleExceptionsResponse(BaseModel):
    timezone: str
    exceptions: list[ScheduleExceptionEntry]


class ScheduleExceptionUpdate(BaseModel):
    """A date-specific override; it fully replaces the weekly rule (§5.3)."""

    is_closed: bool
    open_time: str | None = Field(default=None, pattern=_TIME_PATTERN)
    close_time: str | None = Field(default=None, pattern=_TIME_PATTERN)


class BusinessDayResponse(BaseModel):
    """Computed schedule state for one business date (PROJECT-SPEC §5.1, §5.6)."""

    business_date: date
    timezone: str
    is_open: bool
    is_open_now: bool
    current_business_date: date
    shift_start: datetime | None
    shift_end: datetime | None
