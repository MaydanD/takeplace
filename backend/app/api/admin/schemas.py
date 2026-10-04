"""Admin API schemas.

Transport types are generated from these models for the frontend.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.db.models import AdminAccount, Hall, Table, Venue
from app.domain.layout import StaticElement

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


class TableSummary(BaseModel):
    """Table geometry and state (PROJECT-SPEC §6.5)."""

    id: int
    hall_id: int
    hall_name: str | None = None
    number: str
    capacity: int
    is_bookable: bool
    archived_at: datetime | None
    x: float
    y: float
    width: float
    height: float
    rotation: float
    shape: str
    z_index: int

    @classmethod
    def from_model(cls, table: Table, *, hall_name: str | None = None) -> TableSummary:
        return cls(
            id=table.id,
            hall_id=table.hall_id,
            hall_name=hall_name,
            number=table.number,
            capacity=table.capacity,
            is_bookable=table.is_bookable,
            archived_at=table.archived_at,
            x=float(table.x),
            y=float(table.y),
            width=float(table.width),
            height=float(table.height),
            rotation=float(table.rotation),
            shape=table.shape,
            z_index=table.z_index,
        )


class HallSummary(BaseModel):
    """Hall metadata (canvas size, revision, archive state)."""

    id: int
    name: str
    is_bookable: bool
    canvas_width: int
    canvas_height: int
    layout_revision: int
    archived_at: datetime | None
    table_count: int

    @classmethod
    def from_model(cls, hall: Hall, *, table_count: int) -> HallSummary:
        return cls(
            id=hall.id,
            name=hall.name,
            is_bookable=hall.is_bookable,
            canvas_width=hall.canvas_width,
            canvas_height=hall.canvas_height,
            layout_revision=hall.layout_revision,
            archived_at=hall.archived_at,
            table_count=table_count,
        )


class HallDetail(BaseModel):
    """A hall with its tables and static elements (read-only canvas source)."""

    id: int
    name: str
    is_bookable: bool
    canvas_width: int
    canvas_height: int
    layout_revision: int
    archived_at: datetime | None
    static_elements: list[StaticElement]
    tables: list[TableSummary]

    @classmethod
    def from_model(cls, hall: Hall, tables: list[Table]) -> HallDetail:
        return cls(
            id=hall.id,
            name=hall.name,
            is_bookable=hall.is_bookable,
            canvas_width=hall.canvas_width,
            canvas_height=hall.canvas_height,
            layout_revision=hall.layout_revision,
            archived_at=hall.archived_at,
            static_elements=hall.static_elements,
            tables=[TableSummary.from_model(t) for t in tables],
        )


class HallsResponse(BaseModel):
    halls: list[HallSummary]


class TablesResponse(BaseModel):
    tables: list[TableSummary]


class HallCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    canvas_width: int = Field(default=1200, ge=1, le=1_000_000)
    canvas_height: int = Field(default=800, ge=1, le=1_000_000)
    is_bookable: bool = True


class HallUpdate(BaseModel):
    """Editable hall fields. ``is_bookable`` does not bump the layout revision."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_bookable: bool | None = None
    canvas_width: int | None = Field(default=None, ge=1, le=1_000_000)
    canvas_height: int | None = Field(default=None, ge=1, le=1_000_000)


class TableUpdate(BaseModel):
    """Operational table toggle. Geometry is owned by layout-save, not here (§35)."""

    is_bookable: bool
